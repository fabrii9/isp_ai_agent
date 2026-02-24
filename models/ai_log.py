# -*- coding: utf-8 -*-
"""
ai.log — Registro de auditoría de cada interacción del agente AI.

Registra todo el ciclo de vida: mensaje → decisión → prompt → tool → respuesta.
Permite análisis de costos, tiempos, errores y calidad.
"""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AiLog(models.Model):
    _name = 'ai.log'
    _description = 'Log de Interacción AI'
    _order = 'create_date desc'
    _rec_name = 'create_date'

    # -------------------------------------------------------------------------
    # Referencia al agente
    # -------------------------------------------------------------------------
    agent_id = fields.Many2one(
        'ai.agent',
        string='Agente',
        required=True,
        ondelete='restrict',
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        related='agent_id.company_id',
        store=True,
    )

    # -------------------------------------------------------------------------
    # Contexto del mensaje
    # -------------------------------------------------------------------------
    channel_id = fields.Many2one(
        'discuss.channel',
        string='Canal',
        index=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Partner',
        index=True,
    )
    mobile_number = fields.Char(string='Número de teléfono')

    # -------------------------------------------------------------------------
    # Mensaje recibido
    # -------------------------------------------------------------------------
    inbound_message = fields.Text(string='Mensaje Recibido')

    # -------------------------------------------------------------------------
    # Decisión de activación
    # -------------------------------------------------------------------------
    activation_decision = fields.Selection(
        selection=[
            ('activated', 'Activado'),
            ('skipped_state', 'Agente inactivo'),
            ('skipped_schedule', 'Fuera de horario'),
            ('skipped_phone', 'Número no permitido'),
            ('skipped_rules', 'Ninguna regla coincidió'),
            ('skipped_assigned', 'Chat asignado a humano'),
            ('error', 'Error de activación'),
        ],
        string='Decisión de Activación',
    )
    matched_rule_id = fields.Many2one(
        'ai.activation.rule',
        string='Regla que activó',
    )

    # -------------------------------------------------------------------------
    # Prompt enviado al LLM
    # -------------------------------------------------------------------------
    prompt_sent = fields.Text(string='Prompt Final Enviado')
    tools_available = fields.Text(
        string='Tools disponibles (JSON)',
        help='Esquema JSON de las tools enviadas al LLM.',
    )

    # -------------------------------------------------------------------------
    # Respuesta del LLM
    # -------------------------------------------------------------------------
    llm_raw_response = fields.Text(string='Respuesta Raw del LLM')
    tool_called = fields.Char(string='Tool Invocada')
    tool_params = fields.Text(string='Parámetros de la Tool (JSON)')
    tool_result = fields.Text(string='Resultado de la Tool')
    final_response = fields.Text(string='Respuesta Final al Usuario')

    # -------------------------------------------------------------------------
    # Métricas
    # -------------------------------------------------------------------------
    execution_time_ms = fields.Integer(string='Tiempo de ejecución (ms)')
    tokens_input = fields.Integer(string='Tokens de entrada')
    tokens_output = fields.Integer(string='Tokens de salida')
    estimated_cost_usd = fields.Float(
        string='Costo estimado (USD)',
        digits=(10, 6),
    )
    retry_count = fields.Integer(string='Reintentos', default=0)

    # -------------------------------------------------------------------------
    # Estado y errores
    # -------------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ('ok', 'Éxito'),
            ('tool_error', 'Error en Tool'),
            ('llm_error', 'Error LLM'),
            ('confirmation_pending', 'Pendiente de Confirmación'),
            ('escalated', 'Escalado a Humano'),
            ('skipped', 'Omitido'),
        ],
        string='Estado del Log',
        default='ok',
        index=True,
    )
    error_message = fields.Text(string='Detalle del Error')

    # -------------------------------------------------------------------------
    # GC automático
    # -------------------------------------------------------------------------
    @api.autovacuum
    def _gc_old_logs(self):
        """Eliminar logs de más de 90 días para no saturar la BD."""
        from datetime import timedelta
        threshold = fields.Datetime.now() - timedelta(days=90)
        old_logs = self.search([
            ('create_date', '<', threshold),
            ('state', 'in', ['ok', 'skipped']),
        ])
        _logger.info('AI Log GC: eliminando %d logs antiguos.', len(old_logs))
        old_logs.unlink()

    # -------------------------------------------------------------------------
    # Helper de creación
    # -------------------------------------------------------------------------
    @api.model
    def _create_log(self, agent, channel=None, partner=None, **kwargs):
        """
        Método helper para crear un log de manera simple.

        :param agent: ai.agent recordset
        :param channel: discuss.channel | None
        :param partner: res.partner | None
        :param kwargs: campos adicionales del log
        :return: ai.log recordset
        """
        vals = {
            'agent_id': agent.id,
            'channel_id': channel.id if channel else False,
            'partner_id': partner.id if partner else False,
        }
        vals.update(kwargs)
        return self.create(vals)
