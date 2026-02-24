# -*- coding: utf-8 -*-
"""
ai.workflow — Sistema de automatizaciones tipo N8N simplificado.

Permite encadenar triggers y acciones sin escribir código.
"""

import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

TRIGGER_TYPES = [
    ('inbound_message', 'Mensaje Entrante (WhatsApp)'),
    ('invoice_overdue', 'Factura Vencida'),
    ('ticket_created', 'Ticket Creado'),
    ('payment_approved', 'Pago Aprobado'),
    ('cron', 'Tarea periódica'),
    ('manual', 'Manual / API'),
]

ACTION_TYPES = [
    ('call_agent', 'Llamar al Agente AI'),
    ('run_tool', 'Ejecutar Tool Específica'),
    ('send_message', 'Enviar Mensaje'),
    ('escalate', 'Escalar a Humano'),
    ('create_ticket', 'Crear Ticket'),
    ('update_field', 'Actualizar Campo'),
]


class AiWorkflow(models.Model):
    _name = 'ai.workflow'
    _description = 'Workflow AI'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    # -------------------------------------------------------------------------
    # Identificación
    # -------------------------------------------------------------------------
    name = fields.Char(string='Nombre del Workflow', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    state = fields.Selection(
        selection=[
            ('draft', 'Borrador'),
            ('active', 'Activo'),
            ('paused', 'Pausado'),
        ],
        default='draft',
        tracking=True,
    )

    description = fields.Text(string='Descripción')
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        required=True,
    )

    # -------------------------------------------------------------------------
    # Trigger
    # -------------------------------------------------------------------------
    trigger_type = fields.Selection(
        selection=TRIGGER_TYPES,
        string='Disparador',
        required=True,
    )
    trigger_model_id = fields.Many2one(
        'ir.model',
        string='Modelo del Trigger',
        help='Filtrar eventos en registros de este modelo.',
    )
    trigger_domain = fields.Char(
        string='Dominio de filtrado',
        default='[]',
        help='Expresión de dominio Odoo para filtrar qué registros activan el workflow.',
    )
    trigger_cron_interval = fields.Integer(
        string='Intervalo Cron (min)',
        default=60,
        help='Solo para trigger_type = cron.',
    )

    # -------------------------------------------------------------------------
    # Agente vinculado
    # -------------------------------------------------------------------------
    agent_id = fields.Many2one(
        'ai.agent',
        string='Agente AI',
        help='Agente a invocar cuando el trigger se cumple.',
    )

    # -------------------------------------------------------------------------
    # Acciones encadenadas
    # -------------------------------------------------------------------------
    action_ids = fields.One2many(
        'ai.workflow.action',
        'workflow_id',
        string='Acciones',
    )

    # -------------------------------------------------------------------------
    # Ejecución
    # -------------------------------------------------------------------------
    last_run = fields.Datetime(string='Última ejecución', readonly=True)
    run_count = fields.Integer(string='Ejecuciones', default=0, readonly=True)

    def action_activate(self):
        self.write({'state': 'active'})

    def action_pause(self):
        self.write({'state': 'paused'})

    def execute(self, context_data=None):
        """
        Ejecutar el workflow manualmente o desde trigger.

        :param context_data: dict con datos del contexto (mensaje, partner, etc.)
        """
        self.ensure_one()
        if self.state != 'active':
            return

        _logger.info('Workflow [%s] ejecutando. Trigger: %s', self.name, self.trigger_type)
        ctx = context_data or {}

        for action in self.action_ids.sorted('sequence'):
            try:
                action._execute(ctx)
            except Exception as e:
                _logger.error('Error en acción [%s] del workflow [%s]: %s',
                              action.name, self.name, e, exc_info=True)
                if action.stop_on_error:
                    break

        self.write({
            'last_run': fields.Datetime.now(),
            'run_count': self.run_count + 1,
        })


class AiWorkflowAction(models.Model):
    """Acción individual dentro de un workflow."""
    _name = 'ai.workflow.action'
    _description = 'Acción de Workflow AI'
    _order = 'sequence'

    workflow_id = fields.Many2one('ai.workflow', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Descripción de la Acción', required=True)

    action_type = fields.Selection(
        selection=ACTION_TYPES,
        string='Tipo de Acción',
        required=True,
    )

    # Para run_tool
    tool_id = fields.Many2one('ai.tool', string='Tool a ejecutar')
    tool_params_json = fields.Text(
        string='Parámetros de la Tool (JSON)',
        default='{}',
    )

    # Para send_message
    message_template = fields.Text(
        string='Mensaje a enviar',
        help='Soporta variables: {partner_name}, {debt}, {ticket_number}',
    )

    # Para update_field
    target_model_id = fields.Many2one('ir.model', string='Modelo objetivo')
    target_field = fields.Char(string='Campo a actualizar')
    target_value = fields.Char(string='Nuevo valor')

    stop_on_error = fields.Boolean(string='Detener si falla', default=True)

    def _execute(self, ctx):
        """Ejecutar esta acción con el contexto dado."""
        self.ensure_one()
        _logger.info('Ejecutando acción [%s] tipo=%s', self.name, self.action_type)

        if self.action_type == 'call_agent':
            agent = self.workflow_id.agent_id
            if agent and agent._is_active_now():
                from odoo.addons.isp_ai_agent.services.agent_router import AgentRouter
                router = AgentRouter(self.env)
                router.handle_message(
                    message_text=ctx.get('message_text', ''),
                    channel=ctx.get('channel'),
                    partner=ctx.get('partner'),
                    agent=agent,
                )

        elif self.action_type == 'run_tool':
            if self.tool_id:
                import json as jsonlib
                params = {}
                try:
                    params = jsonlib.loads(self.tool_params_json or '{}')
                except Exception:
                    pass
                from odoo.addons.isp_ai_agent.services.tool_executor import ToolExecutor
                executor = ToolExecutor(self.env)
                executor.execute(self.tool_id.name, params, ctx.get('partner'))

        elif self.action_type == 'send_message':
            channel = ctx.get('channel')
            if channel and self.message_template:
                msg = self.message_template.format(**ctx)
                channel.sudo().message_post(
                    body=msg,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )

        elif self.action_type == 'escalate':
            channel = ctx.get('channel')
            if channel:
                channel.sudo().message_post(
                    body=_('Este chat ha sido escalado al equipo de soporte.'),
                    message_type='notification',
                    subtype_xmlid='mail.mt_comment',
                )
