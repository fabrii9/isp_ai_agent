# -*- coding: utf-8 -*-
"""
RuleEngine — Evalúa las reglas de activación de un agente.

Retorna la decisión de activación y la regla que coincidió.
"""

import logging

_logger = logging.getLogger(__name__)


class RuleEngine:
    """Motor de evaluación de reglas de activación."""

    def __init__(self, env):
        self.env = env

    def evaluate(self, agent, message_text: str, channel, partner):
        """
        Evaluar si el agente debe activarse para este mensaje.

        :param agent: ai.agent recordset
        :param message_text: texto del mensaje
        :param channel: discuss.channel | None
        :param partner: res.partner | None
        :return: tuple(decision: str, matched_rule: ai.activation.rule | None)

        Posibles decisiones:
          'activated'         — el agente debe responder
          'skipped_state'     — agente no activo
          'skipped_schedule'  — fuera de horario
          'skipped_phone'     — número no en whitelist
          'skipped_rules'     — ninguna regla coincidió
          'skipped_assigned'  — chat asignado a humano
        """
        # 1. Verificar estado del agente
        if not agent._is_active_now():
            if agent.state != 'active':
                return 'skipped_state', None
            return 'skipped_schedule', None

        # 2. Verificar número permitido
        if partner:
            phone = partner.phone or partner.mobile or ''
            # Normalizar removiendo espacios y guiones
            phone_clean = ''.join(c for c in phone if c.isdigit() or c == '+')
            if not agent._is_phone_allowed(phone_clean):
                return 'skipped_phone', None

        # 3. Sin reglas configuradas → activar siempre
        if not agent.activation_rule_ids:
            return 'activated', None

        # 4. Evaluar reglas en orden de prioridad
        rules = agent.activation_rule_ids.filtered('active').sorted(
            key=lambda r: (r.priority, r.sequence)
        )

        for rule in rules:
            if rule._matches(message_text=message_text, channel=channel):
                _logger.debug(
                    'RuleEngine: Regla [%s] coincidió para agente [%s].',
                    rule.name, agent.name
                )
                return 'activated', rule

        return 'skipped_rules', None
