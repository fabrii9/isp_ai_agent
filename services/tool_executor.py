# -*- coding: utf-8 -*-
"""
ToolExecutor — Ejecutor de herramientas del agente AI.

Mapea el nombre técnico de la tool a su implementación Python en IspTools.
Valida parámetros, permisos y registra resultados.
"""

import logging
import json

_logger = logging.getLogger(__name__)


class ToolExecutor:
    """Ejecutor de tools para el agente AI."""

    def __init__(self, env):
        self.env = env
        from odoo.addons.isp_ai_agent.tools.isp_tools import IspTools
        self._isp_tools = IspTools(env)

    def execute(self, tool_name: str, params: dict, partner=None) -> dict:
        """
        Ejecutar una tool por su nombre técnico.

        :param tool_name: nombre snake_case de la tool (ej: 'check_debt')
        :param params: dict de parámetros del LLM
        :param partner: res.partner | None

        :return: dict con result y status:
            {"status": "ok"|"error", "result": ..., "message": str}
        """
        _logger.info('ToolExecutor: ejecutando tool=%s params=%s', tool_name, params)

        # Buscar la tool en la BD para validar restricciones
        tool_record = self.env['ai.tool'].sudo().search(
            [('name', '=', tool_name), ('active', '=', True)], limit=1
        )

        if not tool_record:
            return {
                'status': 'error',
                'message': f'Tool desconocida: {tool_name}',
                'result': None,
            }

        # Validar partner si es requerido
        if tool_record.requires_validated_partner and not partner:
            return {
                'status': 'error',
                'message': 'No se pudo identificar al cliente para ejecutar esta acción.',
                'result': None,
            }

        # Delegar a IspTools
        method_name = tool_record.python_method
        if not hasattr(self._isp_tools, method_name):
            _logger.error('Método %s no encontrado en IspTools', method_name)
            return {
                'status': 'error',
                'message': 'Esta herramienta no está implementada todavía.',
                'result': None,
            }

        try:
            method = getattr(self._isp_tools, method_name)
            result = method(partner=partner, **params)
            return {
                'status': 'ok',
                'result': result,
                'message': 'Ejecutado correctamente.',
            }
        except Exception as e:
            _logger.error(
                'Error ejecutando tool [%s]: %s', tool_name, e, exc_info=True
            )
            return {
                'status': 'error',
                'message': str(e),
                'result': None,
            }
