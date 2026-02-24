# -*- coding: utf-8 -*-
"""
TestPromptWizard — Wizard para testear el agente AI desde la interfaz.

Permite enviar un mensaje de prueba y ver:
- El prompt final construido
- La respuesta del LLM
- Las tools disponibles
- El costo estimado
"""

import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TestPromptWizard(models.TransientModel):
    _name = 'ai.test.prompt.wizard'
    _description = 'Wizard: Testear Prompt del Agente AI'

    agent_id = fields.Many2one(
        'ai.agent',
        string='Agente',
        required=True,
    )

    test_message = fields.Text(
        string='Mensaje de Prueba',
        required=True,
        default='Hola, ¿cuánto debo?',
    )

    # Partner de contexto para el test (opcional)
    partner_id = fields.Many2one(
        'res.partner',
        string='Partner de contexto (opcional)',
        help='Si se especifica, el prompt incluirá los datos del partner.',
    )

    # Resultados
    prompt_result = fields.Text(string='Prompt enviado al LLM', readonly=True)
    tools_result = fields.Text(string='Tools disponibles (JSON)', readonly=True)
    llm_response = fields.Text(string='Respuesta del LLM', readonly=True)
    tool_called = fields.Char(string='Tool invocada', readonly=True)
    tool_result = fields.Text(string='Resultado de la Tool', readonly=True)
    tokens_used = fields.Char(string='Tokens usados', readonly=True)
    cost_usd = fields.Char(string='Costo estimado', readonly=True)
    execution_time = fields.Char(string='Tiempo de ejecución', readonly=True)
    error_message = fields.Text(string='Error', readonly=True)

    state = fields.Selection(
        selection=[
            ('draft', 'Sin ejecutar'),
            ('done', 'Ejecutado'),
            ('error', 'Error'),
        ],
        default='draft',
    )

    def action_run_test(self):
        """Ejecutar el test del agente AI."""
        import time
        from odoo.addons.isp_ai_agent.services.prompt_builder import PromptBuilder
        from odoo.addons.isp_ai_agent.services.llm_connectors.base import LLMProvider

        self.ensure_one()
        agent = self.agent_id

        if not agent:
            raise UserError(_('Debe seleccionar un agente.'))

        error = None
        start = time.time()

        try:
            # Obtener API key
            api_key = agent._get_api_key()
            if not api_key and agent.provider != 'custom':
                raise UserError(_(
                    'API Key no configurada. '
                    'Configurarla en: Ajustes > Parámetros técnicos > %s'
                ) % agent.api_key_param)

            # Construir prompt
            builder = PromptBuilder(self.env)
            tools_schema = agent._get_tools_schema()
            messages = builder.build(
                agent=agent,
                history=[],
                current_message=self.test_message,
                partner=self.partner_id or None,
            )

            # Llamar al LLM
            provider = LLMProvider.from_agent(agent)
            result = provider.send_message(
                messages=messages,
                tools=tools_schema or None,
            )

            elapsed = int((time.time() - start) * 1000)
            tokens_in = result.get('tokens_input', 0)
            tokens_out = result.get('tokens_output', 0)
            cost = provider.estimate_cost(tokens_in, tokens_out)

            update_vals = {
                'state': 'done',
                'prompt_result': json.dumps(messages, ensure_ascii=False, indent=2),
                'tools_result': json.dumps(tools_schema, ensure_ascii=False, indent=2),
                'tokens_used': f'Input: {tokens_in} | Output: {tokens_out}',
                'cost_usd': f'${cost:.6f} USD',
                'execution_time': f'{elapsed} ms',
                'error_message': False,
            }

            if result.get('tool_call'):
                tc = result['tool_call']
                update_vals['tool_called'] = tc['name']
                update_vals['tool_result'] = json.dumps(tc['arguments'], ensure_ascii=False, indent=2)
                update_vals['llm_response'] = f'[Tool Call] → {tc["name"]}'
            else:
                update_vals['llm_response'] = result.get('content', '')
                update_vals['tool_called'] = False
                update_vals['tool_result'] = False

            self.write(update_vals)

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            self.write({
                'state': 'error',
                'error_message': str(e),
                'execution_time': f'{elapsed} ms',
            })
            _logger.error('Test prompt error: %s', e, exc_info=True)

        # Mantener el wizard abierto para ver los resultados
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
