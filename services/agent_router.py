# -*- coding: utf-8 -*-
"""
AgentRouter — Orquestador central del ciclo de vida de una interacción.

Flujo:
1. Recibe mensaje desde el hook de discuss.channel
2. Evalúa qué agente debe responder (via RuleEngine)
3. Construye el prompt (via PromptBuilder)
4. Llama al LLM
5. Si hay tool call → ejecuta (via ToolExecutor)
6. Envía respuesta al canal de WhatsApp
7. Registra todo en ai.log
"""

import json
import logging
import time

_logger = logging.getLogger(__name__)


class AgentRouter:
    """Orquestador principal del agente AI."""

    def __init__(self, env):
        self.env = env

    # -------------------------------------------------------------------------
    # Punto de entrada principal
    # -------------------------------------------------------------------------
    def route(self, message_text: str, channel, partner, available_agents, wa_account=None):
        """
        Evaluar agentes y ejecutar el primero que coincida.

        :param message_text: texto limpio del mensaje entrante
        :param channel: discuss.channel recordset
        :param partner: res.partner del remitente
        :param available_agents: recordset de ai.agent candidatos
        :param wa_account: whatsapp.account | None
        """
        from odoo.addons.isp_ai_agent.services.rule_engine import RuleEngine

        rule_engine = RuleEngine(self.env)

        for agent in available_agents:
            decision, matched_rule = rule_engine.evaluate(
                agent=agent,
                message_text=message_text,
                channel=channel,
                partner=partner,
            )
            if decision == 'activated':
                _logger.info('Agente [%s] activado por regla [%s].', agent.name, matched_rule)
                self.handle_message(
                    message_text=message_text,
                    channel=channel,
                    partner=partner,
                    agent=agent,
                    matched_rule=matched_rule,
                )
                return  # Solo el primer agente que coincide ejecuta

            else:
                # Crear log de "omitido"
                self.env['ai.log'].sudo()._create_log(
                    agent=agent,
                    channel=channel,
                    partner=partner,
                    inbound_message=message_text,
                    activation_decision=decision,
                    state='skipped',
                )

    # -------------------------------------------------------------------------
    # Ciclo completo de una interacción
    # -------------------------------------------------------------------------
    def handle_message(self, message_text: str, channel, partner, agent, matched_rule=None):
        """
        Ejecutar el ciclo completo: prompt → LLM → tool → respuesta → log.
        """
        from odoo.addons.isp_ai_agent.services.prompt_builder import PromptBuilder
        from odoo.addons.isp_ai_agent.services.memory_manager import MemoryManager
        from odoo.addons.isp_ai_agent.services.tool_executor import ToolExecutor
        from odoo.addons.isp_ai_agent.services.llm_connectors.base import LLMProvider

        start_time = time.time()
        log_vals = {
            'inbound_message': message_text,
            'activation_decision': 'activated',
            'matched_rule_id': matched_rule.id if matched_rule else False,
            'state': 'ok',
        }

        try:
            # 1 — Obtener memoria conversacional
            memory = MemoryManager(self.env)
            history = memory.get_history(agent=agent, channel=channel, partner=partner)

            # 2 — Construir prompt
            builder = PromptBuilder(self.env)
            tools_schema = agent._get_tools_schema()
            messages = builder.build(
                agent=agent,
                history=history,
                current_message=message_text,
                partner=partner,
            )
            log_vals['prompt_sent'] = json.dumps(messages, ensure_ascii=False)
            log_vals['tools_available'] = json.dumps(tools_schema, ensure_ascii=False)

            # 3 — Llamar al LLM
            provider = LLMProvider.from_agent(agent)
            llm_result = provider.send_message(messages=messages, tools=tools_schema or None)

            log_vals['llm_raw_response'] = json.dumps(llm_result.get('raw', {}), ensure_ascii=False)
            log_vals['tokens_input'] = llm_result.get('tokens_input', 0)
            log_vals['tokens_output'] = llm_result.get('tokens_output', 0)
            log_vals['estimated_cost_usd'] = provider.estimate_cost(
                llm_result.get('tokens_input', 0),
                llm_result.get('tokens_output', 0),
            )

            # 4 — ¿Hay tool call?
            response_text = None
            tool_call = llm_result.get('tool_call')

            if tool_call:
                tool_name = tool_call['name']
                tool_args = tool_call['arguments']
                log_vals['tool_called'] = tool_name
                log_vals['tool_params'] = json.dumps(tool_args, ensure_ascii=False)

                # Verificar si requiere confirmación humana
                tool_record = self.env['ai.tool'].sudo().search(
                    [('name', '=', tool_name)], limit=1
                )
                needs_confirm = (
                    tool_record.requires_confirmation
                    and agent.execution_mode == 'confirm_sensitive'
                )

                if needs_confirm and agent.execution_mode != 'fully_automatic':
                    # Pedir confirmación al usuario
                    response_text = self._build_confirmation_request(tool_name, tool_args)
                    log_vals['state'] = 'confirmation_pending'
                else:
                    # Ejecutar la tool
                    executor = ToolExecutor(self.env)
                    tool_result = executor.execute(
                        tool_name=tool_name,
                        params=tool_args,
                        partner=partner,
                    )
                    log_vals['tool_result'] = json.dumps(tool_result, ensure_ascii=False)

                    # Segunda llamada al LLM con el resultado de la tool
                    messages_with_result = messages + [
                        {'role': 'assistant', 'content': None, 'tool_calls': [
                            {'id': tool_call.get('id', 'call_0'),
                             'type': 'function',
                             'function': {
                                 'name': tool_name,
                                 'arguments': json.dumps(tool_args),
                             }}
                        ]},
                        {'role': 'tool',
                         'tool_call_id': tool_call.get('id', 'call_0'),
                         'content': json.dumps(tool_result, ensure_ascii=False)},
                    ]
                    final_llm = provider.send_message(
                        messages=messages_with_result,
                        tools=None,  # No más tools en la ronda final
                    )
                    response_text = final_llm.get('content', '')
                    log_vals['tokens_input'] += final_llm.get('tokens_input', 0)
                    log_vals['tokens_output'] += final_llm.get('tokens_output', 0)
            else:
                response_text = llm_result.get('content', '')

            log_vals['final_response'] = response_text

            # 5 — Enviar respuesta al canal
            if response_text and channel:
                self._send_response(channel=channel, text=response_text, agent=agent)

        except Exception as e:
            _logger.error(
                'AgentRouter error en agente [%s]: %s', agent.name, e, exc_info=True
            )
            log_vals['state'] = 'llm_error'
            log_vals['error_message'] = str(e)
            # Respuesta de fallback
            fallback = self._get_fallback_message(agent)
            if fallback and channel:
                self._send_response(channel=channel, text=fallback, agent=agent)

        finally:
            elapsed_ms = int((time.time() - start_time) * 1000)
            log_vals['execution_time_ms'] = elapsed_ms
            self.env['ai.log'].sudo()._create_log(
                agent=agent,
                channel=channel,
                partner=partner,
                **log_vals,
            )

    # -------------------------------------------------------------------------
    # Helpers privados
    # -------------------------------------------------------------------------
    def _send_response(self, channel, text: str, agent):
        """Enviar mensaje de respuesta al canal de WhatsApp."""
        try:
            # message_type='whatsapp_message' dispara el envío real por la API de WhatsApp.
            # ai_agent_response=True evita que el hook vuelva a disparar el agente.
            channel.sudo().with_context(ai_agent_response=True).message_post(
                body=text,
                message_type='whatsapp_message',
                subtype_xmlid='mail.mt_comment',
            )
        except Exception as e:
            _logger.error('Error enviando respuesta al canal [%s]: %s', channel.id, e)

    def _build_confirmation_request(self, tool_name: str, tool_args: dict) -> str:
        """Construir mensaje de solicitud de confirmación al usuario."""
        human_names = {
            'check_debt': 'consultar tu deuda',
            'generate_payment_link': 'generar un link de pago',
            'create_ticket': 'crear un ticket de soporte',
            'suspend_service': 'suspender tu servicio',
            'activate_service': 'activar tu servicio',
        }
        action = human_names.get(tool_name, tool_name)
        return (
            f'Para continuar necesito tu confirmación: '
            f'Estoy a punto de *{action}*. '
            f'Respondé *SÍ* para confirmar o *NO* para cancelar.'
        )

    def _get_fallback_message(self, agent) -> str:
        """Obtener mensaje de fallback según configuración del agente."""
        rules = agent.activation_rule_ids.filtered(
            lambda r: r.fallback_action == 'message' and r.fallback_message
        )
        if rules:
            return rules[0].fallback_message
        return (
            'Disculpá, en este momento no puedo procesar tu consulta. '
            'Un agente humano te atenderá a la brevedad.'
        )
