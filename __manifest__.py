# -*- coding: utf-8 -*-
# Part of ISP360 AI Agent Framework
# Author: ISP360 Development Team
# License: LGPL-3

{
    'name': 'ISP AI Agent Framework',
    'version': '19.0.1.0.0',
    'category': 'ISP360/AI',
    'summary': 'AI Agent Framework — Motor conversacional con WhatsApp como canal',
    'description': """
        Módulo de agentes AI para Odoo 19 (ISP360).

        Características:
        - Múltiples agentes configurables
        - Integración con WhatsApp como canal de entrada/salida
        - Conectores LLM: OpenAI, Gemini
        - Tool Calling (funciones ejecutables por el LLM)
        - Reglas de activación configurables
        - Sistema de workflows tipo N8N simplificado
        - Logging estructurado y auditoría
        - Modo seguro con confirmación humana
        - Soporte multi-compañía y multi-tenant
    """,
    'author': 'ISP360',
    'website': 'https://isp360.com.ar',
    'depends': [
        'base',
        'mail',
        'whatsapp',
        # 'isp360',  # Descomentar si el módulo ISP base está instalado
    ],
    'data': [
        # Security
        'security/security.xml',
        'security/ir.model.access.csv',
        # Data
        'data/ai_tool_data.xml',
        'data/ai_workflow_data.xml',
        # Views
        'views/ai_agent_views.xml',
        'views/ai_tool_views.xml',
        'views/ai_activation_rule_views.xml',
        'views/ai_workflow_views.xml',
        'views/ai_log_views.xml',
        'views/menus.xml',
        # Wizards
        'wizard/test_prompt_wizard_view.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'images': [],
}
