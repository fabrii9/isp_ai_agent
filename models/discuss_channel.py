# -*- coding: utf-8 -*-
"""
discuss.channel — Hook de integración con el canal de WhatsApp.

IMPORTANTE: Este archivo EXTIENDE (no modifica) discuss.channel.
Escucha mensajes entrantes via override de message_post
y los redirige al AgentRouter sin tocar el módulo whatsapp.
"""

import logging
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class DiscussChannelAiHook(models.Model):
    """Extension de discuss.channel para integrarse con el AI Agent."""
    _inherit = 'discuss.channel'

    # Campo de control: si el agente AI está activo en este canal
    ai_agent_enabled = fields.Boolean(
        string='Agente AI Activo',
        default=True,
        help='Si False, el agente no interferirá en este canal.',
    )
    # Última respuesta del agente (referencia al log)
    ai_last_log_id = fields.Many2one(
        'ai.log',
        string='Último Log AI',
        readonly=True,
    )

    def message_post(self, **kwargs):
        """
        Override de message_post para interceptar mensajes entrantes de WhatsApp.

        Solo actúa cuando:
        1. El canal es de tipo WhatsApp (channel_type == 'whatsapp')
        2. El mensaje es entrante (no del bot / agente)
        3. El agente AI está habilitado en el canal
        """
        # Ejecutar el post original primero
        message = super().message_post(**kwargs)

        # Evaluar si debemos activar el agente
        try:
            self._maybe_trigger_ai_agent(message, kwargs)
        except Exception as e:
            # Nunca bloquear el flujo de mensajes por un error del agente
            _logger.error(
                'AI Agent hook error en canal [%s]: %s',
                self.id, e, exc_info=True
            )

        return message

    def _maybe_trigger_ai_agent(self, message, kwargs):
        """
        Evaluar si el agente AI debe responder al mensaje.

        Condiciones:
        - Canal de tipo WhatsApp
        - Mensaje es inbound (viene del cliente, no del sistema/agente)
        - Canal tiene ai_agent_enabled = True
        - Hay al menos un agente activo configurado
        """
        # Evitar loop: si es la respuesta del propio agente, salteamos
        if self.env.context.get('ai_agent_response'):
            return

        # Solo canales WhatsApp
        if self.channel_type != 'whatsapp':
            return

        # Solo mensajes inbound (del cliente, no del sistema)
        # message_type 'comment' de un autor no interno = cliente
        if not message:
            return
        if message.message_type not in ('comment', 'whatsapp_message'):
            return

        # La cuenta de WhatsApp del canal
        wa_account = getattr(self, 'wa_account_id', False)

        # Obtener texto limpio del mensaje
        body_html = kwargs.get('body', '') or message.body or ''
        message_text = html2plaintext(body_html).strip()
        if not message_text:
            return

        # Identificar partner
        partner = message.author_id
        if not partner:
            return

        # Verificar si el autor es interno (empleado/usuario) → no procesar
        if partner.user_ids.filtered(lambda u: u._is_internal()):
            return

        # Deduplicación: evitar reintentos de Meta dentro de 10 segundos
        # Meta reintenta el webhook si Odoo tarda en responder (LLM lento)
        threshold = datetime.now() - timedelta(seconds=10)
        recent_log = self.env['ai.log'].sudo().search([
            ('channel_id', '=', self.id),
            ('partner_id', '=', partner.id),
            ('create_date', '>=', threshold.strftime('%Y-%m-%d %H:%M:%S')),
        ], limit=1)
        if recent_log:
            _logger.info('AI Agent dedup: ignorando reintento de Meta para canal [%s]', self.id)
            return

        # Buscar agentes activos para este canal
        domain = [
            ('state', '=', 'active'),
            ('channel', '=', 'whatsapp'),
        ]
        if wa_account:
            # Filtrar por cuenta de WA específica o sin restricción
            domain_with_account = domain + [
                '|',
                ('allowed_wa_account_ids', '=', False),
                ('allowed_wa_account_ids', 'in', [wa_account.id]),
            ]
            agents = self.env['ai.agent'].sudo().search(domain_with_account, order='sequence')
        else:
            agents = self.env['ai.agent'].sudo().search(domain, order='sequence')

        if not agents:
            return

        # Invocar el router
        from odoo.addons.isp_ai_agent.services.agent_router import AgentRouter
        router = AgentRouter(self.env)
        router.route(
            message_text=message_text,
            channel=self,
            partner=partner,
            available_agents=agents,
            wa_account=wa_account,
        )
