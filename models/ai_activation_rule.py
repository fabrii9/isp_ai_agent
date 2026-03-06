# -*- coding: utf-8 -*-
"""
ai.activation.rule — Define cuándo y cómo se activa un agente.

El RuleEngine evalúa estas reglas en orden de prioridad
cuando llega un mensaje entrante.
"""

import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class AiActivationRule(models.Model):
    _name = 'ai.activation.rule'
    _description = 'Regla de Activación del Agente AI'
    _order = 'priority asc, sequence asc'

    # -------------------------------------------------------------------------
    # Identificación
    # -------------------------------------------------------------------------
    name = fields.Char(string='Nombre de la Regla', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    agent_id = fields.Many2one(
        comodel_name='ai.agent',
        string='Agente',
        required=True,
        ondelete='cascade',
    )

    priority = fields.Integer(
        string='Prioridad',
        default=10,
        help='Menor número = mayor prioridad.',
    )

    # -------------------------------------------------------------------------
    # Condiciones de activación
    # -------------------------------------------------------------------------

    # 1. Keywords en el mensaje
    keyword_filter = fields.Boolean(
        string='Filtrar por Keywords',
        default=False,
    )
    keywords = fields.Text(
        string='Keywords (una por línea)',
        help='El agente se activa si el mensaje contiene alguna de estas palabras.',
    )
    keyword_mode = fields.Selection(
        selection=[
            ('any', 'Cualquiera'),
            ('all', 'Todas'),
        ],
        string='Modo Keyword',
        default='any',
    )

    # 2. Solo si el chat no está asignado a un humano
    only_if_unassigned = fields.Boolean(
        string='Solo si chat no asignado',
        default=True,
        help='El agente no interviene si hay un agente humano asignado al canal.',
    )

    # 3. Solo si no hubo respuesta humana en X minutos
    no_human_reply_minutes = fields.Integer(
        string='Activar si sin respuesta humana (min)',
        default=0,
        help='0 = desactivado. Si > 0, el agente interviene solo si no hubo '
             'respuesta humana en estos minutos.',
    )

    # 4. Modelo/contexto específico
    context_model = fields.Selection(
        selection=[
            ('any', 'Cualquier contexto'),
            ('discuss.channel', 'Canal de chat'),
            ('account.move', 'Factura'),
            ('helpdesk.ticket', 'Ticket de soporte'),
            ('crm.lead', 'Lead/Oportunidad'),
        ],
        string='Contexto del modelo',
        default='any',
    )

    # 5. Horario específico para esta regla (complementa el del agente)
    schedule_enabled = fields.Boolean(
        string='Restricción horaria propia',
        default=False,
    )
    hour_from = fields.Integer(string='Desde (hora)', default=0)
    hour_to = fields.Integer(string='Hasta (hora)', default=24)

    # -------------------------------------------------------------------------
    # Acción fallback
    # -------------------------------------------------------------------------
    fallback_action = fields.Selection(
        selection=[
            ('escalate', 'Escalar a humano'),
            ('message', 'Enviar mensaje predefinido'),
            ('ignore', 'Ignorar'),
        ],
        string='Acción Fallback',
        default='escalate',
        help='Qué hacer si el agente no puede responder.',
    )
    fallback_message = fields.Text(
        string='Mensaje Fallback',
        help='Solo si fallback_action = message.',
        default='Nuestro equipo te atenderá a la brevedad. ¡Gracias!',
    )

    # -------------------------------------------------------------------------
    # Validaciones
    # -------------------------------------------------------------------------
    @api.constrains('hour_from', 'hour_to')
    def _check_hours(self):
        for rec in self:
            if rec.schedule_enabled:
                if not (0 <= rec.hour_from < 24 and 0 <= rec.hour_to <= 24):
                    raise ValidationError(_('Las horas deben estar entre 0 y 24.'))
                if rec.hour_from >= rec.hour_to:
                    raise ValidationError(_('La hora "desde" debe ser menor que "hasta".'))

    # -------------------------------------------------------------------------
    # Métodos de evaluación
    # -------------------------------------------------------------------------
    def _matches(self, message_text, channel=None):
        """
        Evaluar si esta regla coincide con el mensaje/contexto.

        :param message_text: str — texto del mensaje entrante
        :param channel: discuss.channel — canal del mensaje
        :return: bool
        """
        self.ensure_one()

        # Verificar horario
        if self.schedule_enabled:
            from datetime import datetime
            now = datetime.now()
            if not (self.hour_from <= now.hour < self.hour_to):
                return False

        # Verificar si canal asignado
        if self.only_if_unassigned and channel:
            if getattr(channel, 'livechat_operator_id', False):
                return False

        # Verificar respuesta humana reciente
        if self.no_human_reply_minutes > 0 and channel:
            from datetime import datetime, timedelta
            threshold = datetime.now() - timedelta(minutes=self.no_human_reply_minutes)
            # Buscar mensajes humanos recientes en el canal
            last_human = self.env['mail.message'].search([
                ('res_id', '=', channel.id),
                ('model', '=', 'discuss.channel'),
                ('message_type', '=', 'comment'),
                ('author_id.user_ids', '!=', False),
                ('date', '>=', threshold),
            ], limit=1)
            if last_human:
                return False

        # Verificar keywords
        if self.keyword_filter and self.keywords:
            words = [k.strip().lower() for k in self.keywords.splitlines() if k.strip()]
            msg_lower = message_text.lower()
            if self.keyword_mode == 'any':
                if not any(w in msg_lower for w in words):
                    return False
            else:  # all
                if not all(w in msg_lower for w in words):
                    return False

        return True
