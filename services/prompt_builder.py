# -*- coding: utf-8 -*-
"""
PromptBuilder — Construye dinámicamente el prompt final para el LLM.

Combina:
- system_prompt del agente
- style_prompt
- Información del partner / contexto ISP
- Historial de conversación
- Mensaje actual
- Reglas de seguridad
"""

import logging
from datetime import datetime

_logger = logging.getLogger(__name__)

# Reglas de seguridad que siempre se agregan al prompt
SECURITY_RULES = """
REGLAS DE SEGURIDAD (OBLIGATORIAS):
1. NUNCA inventes información. Si no tenés los datos, decilo claramente.
2. NUNCA ejecutes acciones críticas (suspensión, pagos) sin confirmación explícita del usuario.
3. Si el cliente parece molesto o fuera de contexto ISP, escalá al humano.
4. Validá siempre que el número pertenece a un cliente registrado antes de mostrar datos.
5. No respondas sobre temas fuera del contexto de un ISP (internet, servicios, facturación).
6. Mantené tus respuestas cortas y al punto para un chat por WhatsApp.
"""


class PromptBuilder:
    """Constructor de prompts para el agente AI."""

    def __init__(self, env):
        self.env = env

    def build(self, agent, history: list, current_message: str, partner=None) -> list:
        """
        Construir la lista de mensajes para enviar al LLM.

        :param agent: ai.agent recordset
        :param history: lista de {'role', 'content'} → historial previo
        :param current_message: mensaje actual del usuario
        :param partner: res.partner | None

        :return: Lista de mensajes en formato OpenAI messages
        """
        messages = []

        # 1 — Mensaje de sistema principal
        system_content = self._build_system_prompt(agent=agent, partner=partner)
        messages.append({'role': 'system', 'content': system_content})

        # 2 — Historial de conversación (no más que memory_limit)
        for entry in history:
            messages.append(entry)

        # 3 — Mensaje actual del usuario
        messages.append({'role': 'user', 'content': current_message})

        return messages

    def _build_system_prompt(self, agent, partner=None) -> str:
        """Construir el system prompt completo."""
        parts = []

        # Fecha y hora actual
        now = datetime.now().strftime('%A %d/%m/%Y %H:%M')
        parts.append(f'Fecha y hora actual: {now}\n')

        # Prompt principal del agente
        parts.append(agent.system_prompt.strip())

        # Style prompt (si tiene)
        if agent.style_prompt:
            parts.append('\n--- ESTILO DE COMUNICACIÓN ---\n' + agent.style_prompt.strip())

        # Contexto del partner
        partner_context = self._build_partner_context(partner)
        if partner_context:
            parts.append('\n--- CONTEXTO DEL CLIENTE ---\n' + partner_context)

        # Reglas de seguridad
        parts.append(SECURITY_RULES)

        return '\n'.join(parts)

    def _build_partner_context(self, partner) -> str:
        """Construir bloque de contexto del partner para el prompt."""
        if not partner:
            return ''

        lines = []
        if partner.name:
            lines.append(f'Nombre: {partner.name}')
        if partner.email:
            lines.append(f'Email: {partner.email}')
        if partner.phone or partner.mobile:
            lines.append(f'Teléfono: {partner.phone or partner.mobile}')
        if partner.vat:
            lines.append(f'CUIT/DNI: {partner.vat}')

        # Información ISP si está disponible
        try:
            # isp360 contract
            contracts = self.env['isp360.contract'].sudo().search([
                ('partner_id', '=', partner.id),
                ('state', '=', 'active'),
            ], limit=3)
            if contracts:
                services = ', '.join(c.name for c in contracts)
                lines.append(f'Servicios activos: {services}')
        except Exception:
            pass  # Módulo ISP no disponible

        return '\n'.join(lines) if lines else ''
