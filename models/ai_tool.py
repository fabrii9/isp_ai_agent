# -*- coding: utf-8 -*-
"""
ai.tool — Herramienta ejecutable por el agente AI.

Cada tool representa una función Python que el LLM puede invocar
via "function calling" / "tool use".

La descripción y parámetros se exponen al LLM en formato JSON Schema.
"""

import json
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class AiTool(models.Model):
    _name = 'ai.tool'
    _description = 'Herramienta AI'
    _order = 'sequence, name'

    # -------------------------------------------------------------------------
    # Identificación
    # -------------------------------------------------------------------------
    name = fields.Char(
        string='Nombre Técnico',
        required=True,
        help='snake_case. Ej: check_debt. Es el nombre que ve el LLM.',
    )
    display_name_custom = fields.Char(
        string='Nombre para mostrar',
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    description = fields.Text(
        string='Descripción para el LLM',
        required=True,
        help='El LLM lee esta descripción para decidir cuándo llamar esta herramienta.',
    )

    # Categoría funcional
    category = fields.Selection(
        selection=[
            ('billing', 'Facturación'),
            ('payment', 'Pagos'),
            ('support', 'Soporte / Reclamos'),
            ('service', 'Servicios'),
            ('crm', 'CRM / Leads'),
            ('info', 'Información'),
            ('escalation', 'Escalamiento'),
        ],
        string='Categoría',
        required=True,
    )

    # Modelo Odoo afectado
    model_id = fields.Many2one(
        comodel_name='ir.model',
        string='Modelo Odoo afectado',
        ondelete='set null',
    )

    # -------------------------------------------------------------------------
    # Implementación Python
    # -------------------------------------------------------------------------
    python_method = fields.Char(
        string='Método Python',
        required=True,
        help='Nombre del método en tools/isp_tools.py. '
             'Ej: execute_check_debt',
    )

    # Parámetros en formato JSON Schema (para exponer al LLM)
    parameters_json = fields.Text(
        string='Parámetros (JSON Schema)',
        default='{\n  "type": "object",\n  "properties": {},\n  "required": []\n}',
        help='JSON Schema de los parámetros que acepta esta tool.',
    )

    # -------------------------------------------------------------------------
    # Seguridad
    # -------------------------------------------------------------------------
    requires_confirmation = fields.Boolean(
        string='Requiere Confirmación Humana',
        default=False,
        help='Si True, el agente en modo confirm_sensitive pedirá confirmación.',
    )
    requires_validated_partner = fields.Boolean(
        string='Requiere Partner Validado',
        default=True,
        help='Si True, verificar que el número esté asociado a un partner antes de ejecutar.',
    )
    is_readonly = fields.Boolean(
        string='Solo Lectura',
        default=True,
        help='Si True, la tool solo consulta datos (no modifica).',
    )

    # -------------------------------------------------------------------------
    # Agentes que usan esta tool
    # -------------------------------------------------------------------------
    agent_ids = fields.Many2many(
        comodel_name='ai.agent',
        relation='ai_agent_tool_rel',
        column1='tool_id',
        column2='agent_id',
        string='Agentes que la usan',
    )

    # -------------------------------------------------------------------------
    # Validaciones
    # -------------------------------------------------------------------------
    @api.constrains('parameters_json')
    def _check_json(self):
        for rec in self:
            try:
                json.loads(rec.parameters_json)
            except (ValueError, TypeError):
                raise ValidationError(_(
                    'El campo "Parámetros" no es un JSON válido en la tool: %s'
                ) % rec.name)

    @api.constrains('name')
    def _check_name_format(self):
        import re
        for rec in self:
            if not re.match(r'^[a-z][a-z0-9_]*$', rec.name):
                raise ValidationError(_(
                    'El nombre técnico debe ser snake_case (solo minúsculas, números y _): %s'
                ) % rec.name)

    # -------------------------------------------------------------------------
    # Helper público
    # -------------------------------------------------------------------------
    def _to_llm_schema(self):
        """
        Devolver la representación JSON Schema que el LLM recibirá
        para decidir cuándo llamar esta herramienta.

        Formato compatible con OpenAI function calling y Gemini function declarations.
        """
        self.ensure_one()
        try:
            params = json.loads(self.parameters_json)
        except Exception:
            params = {'type': 'object', 'properties': {}, 'required': []}

        return {
            'name': self.name,
            'description': self.description,
            'parameters': params,
        }
