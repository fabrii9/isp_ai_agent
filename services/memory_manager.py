# -*- coding: utf-8 -*-
"""
MemoryManager — Gestión de memoria conversacional del agente.

Modos soportados:
- last_n: últimos N mensajes del canal como contexto
- per_ticket: mensajes asociados al ticket activo del partner
- per_partner: todos los mensajes del partner en el canal
"""

import logging
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class MemoryManager:
    """Gestión de memoria conversacional del agente AI."""

    def __init__(self, env):
        self.env = env

    def get_history(self, agent, channel, partner) -> list:
        """
        Obtener el historial de la conversación según el memory_mode del agente.

        :return: Lista de dicts [{"role": "user"|"assistant", "content": str}]
        """
        mode = agent.memory_mode
        limit = agent.memory_limit or 10

        if mode == 'last_n':
            return self._get_last_n(channel=channel, limit=limit)
        elif mode == 'per_partner':
            return self._get_per_partner(partner=partner, limit=limit)
        elif mode == 'per_ticket':
            return self._get_per_ticket(partner=partner, limit=limit)

        return []

    def _get_last_n(self, channel, limit: int) -> list:
        """Últimos N mensajes del canal (excluyendo el mensaje actual)."""
        if not channel:
            return []

        messages = self.env['mail.message'].sudo().search([
            ('res_id', '=', channel.id),
            ('model', '=', 'discuss.channel'),
            ('message_type', 'in', ('comment', 'whatsapp_message')),
        ], order='date desc', limit=limit + 1)

        # Reordenar cronológicamente y excluir el último (ya es el mensaje actual)
        messages = messages.sorted(key=lambda m: m.date)
        if len(messages) > 0:
            messages = messages[:-1]  # Excluir el más reciente
        if len(messages) > limit:
            messages = messages[-limit:]

        return self._messages_to_history(messages, channel)

    def _get_per_partner(self, partner, limit: int) -> list:
        """Historial completo del partner en cualquier canal."""
        if not partner:
            return []

        channels = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'whatsapp'),
            ('channel_member_ids.partner_id', 'in', [partner.id]),
        ])

        messages = self.env['mail.message'].sudo().search([
            ('res_id', 'in', channels.ids),
            ('model', '=', 'discuss.channel'),
            ('message_type', 'in', ('comment', 'whatsapp_message')),
        ], order='date desc', limit=limit)

        return self._messages_to_history(messages.sorted('date'))

    def _get_per_ticket(self, partner, limit: int) -> list:
        """Mensajes del ticket activo del partner."""
        if not partner:
            return []

        ticket = self.env['helpdesk.ticket'].sudo().search([
            ('partner_id', '=', partner.id),
            ('stage_id.is_close', '=', False),
        ], order='create_date desc', limit=1)

        if not ticket:
            return []

        messages = self.env['mail.message'].sudo().search([
            ('res_id', '=', ticket.id),
            ('model', '=', 'helpdesk.ticket'),
            ('message_type', 'in', ('comment', 'email')),
        ], order='date desc', limit=limit)

        return self._messages_to_history(messages.sorted('date'))

    def _messages_to_history(self, messages, channel=None) -> list:
        """Convertir mail.message a formato de historial para el LLM."""
        history = []
        for msg in messages:
            text = html2plaintext(msg.body or '').strip()
            if not text:
                continue

            # Determinar rol: si el autor es un usuario interno → assistant
            is_internal = msg.author_id.user_ids.filtered(lambda u: u._is_internal())
            role = 'assistant' if is_internal else 'user'
            history.append({'role': role, 'content': text})

        return history
