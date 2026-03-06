# -*- coding: utf-8 -*-
"""
LLMProvider — Clase base abstracta para conectores de LLM.

Todos los conectores deben heredar de esta clase e implementar
los métodos abstractos.
"""

import abc
import logging
from typing import Optional

_logger = logging.getLogger(__name__)

# Precio estimado por 1000 tokens (USD). Se actualiza en cada conector.
DEFAULT_COST_PER_1K_INPUT = 0.0005
DEFAULT_COST_PER_1K_OUTPUT = 0.0015


class LLMProvider(abc.ABC):
    """
    Interfaz base para todos los proveedores de LLM.

    Uso:
        provider = OpenAIConnector(api_key, model_name, ...)
        result = provider.send_message(messages, tools)
    """

    def __init__(self, api_key: str, model_name: str, temperature: float = 0.3,
                 max_tokens: int = 1024, endpoint: Optional[str] = None):
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.endpoint = endpoint

    @abc.abstractmethod
    def send_message(
        self,
        messages: list,
        tools: Optional[list] = None,
    ) -> dict:
        """
        Enviar mensaje al LLM y retornar la respuesta.

        :param messages: Lista de mensajes en formato OpenAI:
            [
                {"role": "system", "content": "..."},
                {"role": "user",   "content": "..."},
                {"role": "assistant", "content": "..."},
            ]
        :param tools: Lista de tools en formato JSON Schema (optional).

        :return: dict con keys:
            - "content": str        → texto de la respuesta (si no hay tool call)
            - "tool_call": dict | None → {"name": str, "arguments": dict}
            - "tokens_input": int
            - "tokens_output": int
            - "raw": dict           → respuesta cruda de la API
        """
        raise NotImplementedError

    def estimate_cost(self, tokens_input: int, tokens_output: int) -> float:
        """Calcular costo estimado en USD."""
        cost = (tokens_input / 1000 * DEFAULT_COST_PER_1K_INPUT +
                tokens_output / 1000 * DEFAULT_COST_PER_1K_OUTPUT)
        return round(cost, 8)

    @staticmethod
    def from_agent(agent):
        """
        Factory method: crear el conector correcto para un ai.agent.

        :param agent: ai.agent Odoo recordset
        :return: LLMProvider instance
        """
        api_key = agent._get_api_key()
        provider_map = {
            'openai': 'odoo.addons.isp_ai_agent.services.llm_connectors.openai_connector.OpenAIConnector',
            'gemini': 'odoo.addons.isp_ai_agent.services.llm_connectors.gemini_connector.GeminiConnector',
        }
        import importlib
        module_path = provider_map.get(agent.provider)
        if not module_path:
            raise ValueError(f'Proveedor LLM no soportado: {agent.provider}')

        parts = module_path.rsplit('.', 1)
        module = importlib.import_module(parts[0])
        cls = getattr(module, parts[1])

        return cls(
            api_key=api_key,
            model_name=agent.model_name,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            endpoint=agent.endpoint,
        )
