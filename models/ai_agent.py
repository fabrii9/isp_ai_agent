# -*- coding: utf-8 -*-
"""
ai.agent — Modelo central del agente AI.

Representa un agente configurado con:
- Proveedor LLM (OpenAI, Gemini, etc.)
- Herramientas habilitadas
- Reglas de activación
- Modo de ejecución y seguridad
- Horarios de actividad
"""

import json
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

DAYS_OF_WEEK = [
    ('0', 'Lunes'),
    ('1', 'Martes'),
    ('2', 'Miércoles'),
    ('3', 'Jueves'),
    ('4', 'Viernes'),
    ('5', 'Sábado'),
    ('6', 'Domingo'),
]


class AiAgent(models.Model):
    _name = 'ai.agent'
    _description = 'Agente AI'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    # -------------------------------------------------------------------------
    # Identificación
    # -------------------------------------------------------------------------
    name = fields.Char(
        string='Nombre del Agente',
        required=True,
        tracking=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    state = fields.Selection(
        selection=[
            ('draft', 'Borrador'),
            ('active', 'Activo'),
            ('paused', 'Pausado'),
        ],
        string='Estado',
        default='draft',
        tracking=True,
        required=True,
    )

    description = fields.Text(string='Descripción interna')

    # -------------------------------------------------------------------------
    # Canal
    # -------------------------------------------------------------------------
    channel = fields.Selection(
        selection=[
            ('whatsapp', 'WhatsApp'),
            ('webchat', 'Webchat'),
            ('api', 'API Externa'),
        ],
        string='Canal',
        default='whatsapp',
        required=True,
    )

    # Cuenta de WhatsApp específica permitida (None = todas)
    allowed_wa_account_ids = fields.Many2many(
        comodel_name='whatsapp.account',
        string='Cuentas WhatsApp Permitidas',
        help='Dejar vacío para permitir todas las cuentas de WhatsApp.',
    )

    # Números de whitelist (texto libre separado por comas o líneas)
    allowed_phone_numbers = fields.Text(
        string='Números Permitidos (whitelist)',
        help='Opcional. Un número por línea con prefijo internacional. '
             'Vacío = sin restricción.',
    )

    # -------------------------------------------------------------------------
    # Compañía / multi-tenant
    # -------------------------------------------------------------------------
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Compañía',
        default=lambda self: self.env.company,
        required=True,
    )
    allowed_company_ids = fields.Many2many(
        comodel_name='res.company',
        relation='ai_agent_res_company_rel',
        column1='agent_id',
        column2='company_id',
        string='Compañías Permitidas',
    )

    # -------------------------------------------------------------------------
    # Horario de actividad
    # -------------------------------------------------------------------------
    schedule_enabled = fields.Boolean(
        string='Usar Horario Activo',
        default=False,
    )
    schedule_ids = fields.One2many(
        comodel_name='ai.agent.schedule',
        inverse_name='agent_id',
        string='Horarios',
    )

    # -------------------------------------------------------------------------
    # Proveedor LLM
    # -------------------------------------------------------------------------
    provider = fields.Selection(
        selection=[
            ('openai', 'OpenAI'),
            ('gemini', 'Google Gemini'),
            ('anthropic', 'Anthropic Claude'),
            ('custom', 'Endpoint Personalizado'),
        ],
        string='Proveedor LLM',
        default='openai',
        required=True,
    )
    model_name = fields.Char(
        string='Nombre del Modelo',
        default='gpt-4o-mini',
        required=True,
    )
    # La API key se almacena en ir.config_parameter por seguridad
    # La clave del parámetro sigue el patrón: ai_agent.{provider}.api_key.{company_id}
    api_key_param = fields.Char(
        string='Clave de parámetro API Key',
        compute='_compute_api_key_param',
        store=False,
    )
    endpoint = fields.Char(
        string='Endpoint personalizado',
        help='Solo para provider=custom. Ej: http://localhost:11434/v1',
    )
    temperature = fields.Float(
        string='Temperature',
        default=0.3,
        help='0.0 = respuestas deterministas. 1.0 = más creativas.',
    )
    max_tokens = fields.Integer(
        string='Max Tokens',
        default=1024,
    )

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------
    system_prompt = fields.Text(
        string='System Prompt',
        required=True,
        default="""Sos un asistente virtual de un proveedor de internet (ISP).
Tu objetivo es ayudar a los clientes a:
- Consultar su deuda
- Generar links de pago
- Crear reclamos técnicos
- Consultar el estado de su servicio

Respondé siempre de forma clara, concisa y en español rioplatense.
No inventes información. Si no podés ayudar, escalá al equipo humano.
""",
    )
    style_prompt = fields.Text(
        string='Style Prompt (opcional)',
        help='Instrucciones adicionales de tono y estilo.',
    )

    # -------------------------------------------------------------------------
    # Memoria conversacional
    # -------------------------------------------------------------------------
    memory_mode = fields.Selection(
        selection=[
            ('last_n', 'Últimos N mensajes'),
            ('per_ticket', 'Por Ticket'),
            ('per_partner', 'Por Partner'),
        ],
        string='Modo de Memoria',
        default='last_n',
        required=True,
    )
    memory_limit = fields.Integer(
        string='Límite de Memoria (N mensajes)',
        default=10,
    )

    # -------------------------------------------------------------------------
    # Modo de ejecución
    # -------------------------------------------------------------------------
    execution_mode = fields.Selection(
        selection=[
            ('suggest_only', 'Solo Sugerir (sin ejecutar)'),
            ('confirm_sensitive', 'Confirmar acciones sensibles'),
            ('fully_automatic', 'Totalmente Automático'),
        ],
        string='Modo de Ejecución',
        default='confirm_sensitive',
        required=True,
    )

    auto_escalate_mode = fields.Selection(
        selection=[
            ('immediate', 'Escalar de inmediato'),
            ('ask_once', 'Preguntar una vez, luego escalar'),
            ('retry', 'Reintentar antes de escalar'),
        ],
        string='Modo de Escalamiento',
        default='ask_once',
    )
    escalate_after_minutes = fields.Integer(
        string='Escalar después de (min)',
        default=30,
        help='Si no hubo respuesta humana en estos minutos, el agente retoma.',
    )

    # -------------------------------------------------------------------------
    # Herramientas habilitadas
    # -------------------------------------------------------------------------
    enabled_tool_ids = fields.Many2many(
        comodel_name='ai.tool',
        relation='ai_agent_tool_rel',
        column1='agent_id',
        column2='tool_id',
        string='Herramientas Habilitadas',
    )

    # -------------------------------------------------------------------------
    # Reglas de activación
    # -------------------------------------------------------------------------
    activation_rule_ids = fields.One2many(
        comodel_name='ai.activation.rule',
        inverse_name='agent_id',
        string='Reglas de Activación',
    )

    # -------------------------------------------------------------------------
    # Estadísticas
    # -------------------------------------------------------------------------
    log_count = fields.Integer(
        string='Logs',
        compute='_compute_log_count',
    )
    total_interactions = fields.Integer(
        string='Interacciones',
        compute='_compute_log_count',
    )

    # -------------------------------------------------------------------------
    # Computes
    # -------------------------------------------------------------------------
    @api.depends('provider', 'company_id')
    def _compute_api_key_param(self):
        for rec in self:
            rec.api_key_param = f'ai_agent.{rec.provider}.api_key.{rec.company_id.id}'

    def _compute_log_count(self):
        for rec in self:
            logs = self.env['ai.log'].search_count([('agent_id', '=', rec.id)])
            rec.log_count = logs
            rec.total_interactions = logs

    # -------------------------------------------------------------------------
    # Acciones de botones
    # -------------------------------------------------------------------------
    def action_activate(self):
        """Activar el agente."""
        for rec in self:
            if not rec.system_prompt:
                raise ValidationError(_('Debe definir un System Prompt antes de activar.'))
            if not rec.provider:
                raise ValidationError(_('Debe seleccionar un proveedor LLM.'))
            api_key = self.env['ir.config_parameter'].sudo().get_param(rec.api_key_param)
            if not api_key and rec.provider != 'custom':
                raise ValidationError(_(
                    'No se encontró la API Key para el proveedor %s.\n'
                    'Configurar en: Ajustes > Parámetros técnicos > %s'
                ) % (rec.provider, rec.api_key_param))
            rec.state = 'active'
            _logger.info('AI Agent [%s] activado.', rec.name)

    def action_pause(self):
        """Pausar el agente (kill switch)."""
        self.write({'state': 'paused'})
        _logger.info('AI Agent [%s] pausado.', self.mapped('name'))

    def action_draft(self):
        """Volver a borrador."""
        self.write({'state': 'draft'})

    def action_view_logs(self):
        """Abrir vista de logs filtrada por este agente."""
        return {
            'name': _('Logs — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ai.log',
            'view_mode': 'list,form',
            'domain': [('agent_id', '=', self.id)],
            'context': {'default_agent_id': self.id},
        }

    def action_test_prompt(self):
        """Abrir wizard para testear el prompt."""
        return {
            'name': _('Testear Prompt — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ai.test.prompt.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_agent_id': self.id},
        }

    # -------------------------------------------------------------------------
    # Helpers internos
    # -------------------------------------------------------------------------
    def _get_api_key(self):
        """Obtener la API Key del parámetro seguro."""
        self.ensure_one()
        return self.env['ir.config_parameter'].sudo().get_param(self.api_key_param, '')

    def _set_api_key(self, key):
        """Guardar la API Key en parámetros del sistema."""
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_param(self.api_key_param, key)

    def _is_phone_allowed(self, phone_formatted):
        """Verificar si un número está en la whitelist del agente."""
        self.ensure_one()
        if not self.allowed_phone_numbers:
            return True
        allowed = [
            p.strip() for p in self.allowed_phone_numbers.replace(',', '\n').splitlines()
            if p.strip()
        ]
        return phone_formatted in allowed

    def _is_within_schedule(self):
        """Verificar si el agente debe estar activo ahora según su horario."""
        self.ensure_one()
        if not self.schedule_enabled or not self.schedule_ids:
            return True
        from datetime import datetime
        now = datetime.now()
        day = str(now.weekday())
        for sched in self.schedule_ids:
            if sched.day_of_week == day:
                if sched.hour_from <= now.hour < sched.hour_to:
                    return True
        return False

    def _is_active_now(self):
        """Agente activo + dentro de horario."""
        self.ensure_one()
        return self.state == 'active' and self._is_within_schedule()

    def _get_tools_schema(self):
        """Devolver lista de tools en formato JSON Schema para el LLM."""
        self.ensure_one()
        return [tool._to_llm_schema() for tool in self.enabled_tool_ids]


class AiAgentSchedule(models.Model):
    """Horario de actividad del agente (por día de semana + rango horario)."""
    _name = 'ai.agent.schedule'
    _description = 'Horario del Agente AI'

    agent_id = fields.Many2one('ai.agent', required=True, ondelete='cascade')
    day_of_week = fields.Selection(DAYS_OF_WEEK, string='Día', required=True)
    hour_from = fields.Integer(string='Desde (hora)', default=8)
    hour_to = fields.Integer(string='Hasta (hora)', default=20)

    @api.constrains('hour_from', 'hour_to')
    def _check_hours(self):
        for rec in self:
            if not (0 <= rec.hour_from < 24 and 0 <= rec.hour_to <= 24):
                raise ValidationError(_('Las horas deben estar entre 0 y 24.'))
            if rec.hour_from >= rec.hour_to:
                raise ValidationError(_('La hora "desde" debe ser menor que "hasta".'))
