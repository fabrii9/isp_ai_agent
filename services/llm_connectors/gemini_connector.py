# -*- coding: utf-8 -*-
"""
GeminiConnector — Conector para Google Gemini (gemini-1.5-flash, pro, etc.)

Soporta:
- generateContent API
- Function declarations (tool calling)
- Retry con backoff exponencial
"""

import json
import logging
import time
from typing import Optional

import requests

from .base import LLMProvider

_logger = logging.getLogger(__name__)

GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'

# Costo aproximado por 1K tokens (USD)
COST_TABLE = {
    'gemini-1.5-flash': (0.000075, 0.0003),
    'gemini-1.5-pro': (0.0035, 0.0105),
    'gemini-2.0-flash': (0.0001, 0.0004),
}

MAX_RETRIES = 3
RETRY_DELAY = 2


class GeminiConnector(LLMProvider):
    """Conector para la API de Google Gemini."""

    def __init__(self, api_key, model_name='gemini-1.5-flash', temperature=0.3,
                 max_tokens=1024, endpoint=None):
        super().__init__(api_key, model_name, temperature, max_tokens, endpoint)
        self.base_url = endpoint or GEMINI_BASE_URL
        cost_pair = COST_TABLE.get(model_name, (0.0001, 0.0004))
        self.cost_per_1k_input = cost_pair[0]
        self.cost_per_1k_output = cost_pair[1]

    def send_message(self, messages: list, tools: Optional[list] = None) -> dict:
        """
        Enviar mensaje a Gemini.

        Convierte el formato OpenAI de mensajes al formato de Gemini.
        """
        url = (f'{self.base_url}/models/{self.model_name}'
               f':generateContent?key={self.api_key}')

        # Convertir mensajes al formato Gemini
        system_instruction = None
        gemini_contents = []

        for msg in messages:
            role = msg.get('role')
            content = msg.get('content', '')
            if role == 'system':
                system_instruction = {'parts': [{'text': content}]}
            elif role == 'user':
                gemini_contents.append({'role': 'user', 'parts': [{'text': content}]})
            elif role == 'assistant':
                gemini_contents.append({'role': 'model', 'parts': [{'text': content}]})

        payload = {
            'contents': gemini_contents,
            'generationConfig': {
                'temperature': self.temperature,
                'maxOutputTokens': self.max_tokens,
            },
        }

        if system_instruction:
            payload['system_instruction'] = system_instruction

        # Agregar function declarations
        if tools:
            payload['tools'] = [{
                'function_declarations': [
                    self._openai_tool_to_gemini(t) for t in tools
                ]
            }]

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _logger.debug(
                    'Gemini request: model=%s attempt=%d', self.model_name, attempt
                )
                resp = requests.post(url, json=payload, timeout=(10, 60))
                resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data)

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else 0
                if status in (429, 500, 503):
                    wait = RETRY_DELAY * attempt
                    _logger.warning('Gemini error %s. Retry en %ss...', status, wait)
                    time.sleep(wait)
                    last_error = e
                else:
                    _logger.error('Gemini HTTP error %s: %s', status, e.response.text)
                    raise
            except requests.exceptions.RequestException as e:
                _logger.error('Gemini request error: %s', e)
                last_error = e
                time.sleep(RETRY_DELAY * attempt)

        raise RuntimeError(f'Gemini: se agotaron los reintentos. Último error: {last_error}')

    def _openai_tool_to_gemini(self, tool: dict) -> dict:
        """Convertir formato OpenAI tool a Gemini function_declaration."""
        return {
            'name': tool.get('name'),
            'description': tool.get('description', ''),
            'parameters': tool.get('parameters', {}),
        }

    def _parse_response(self, data: dict) -> dict:
        """Normalizar respuesta de Gemini al formato interno."""
        candidates = data.get('candidates', [{}])
        candidate = candidates[0] if candidates else {}
        content = candidate.get('content', {})
        parts = content.get('parts', [])

        # Tokens (Gemini los reporta diferente)
        usage = data.get('usageMetadata', {})
        tokens_input = usage.get('promptTokenCount', 0)
        tokens_output = usage.get('candidatesTokenCount', 0)

        result = {
            'content': None,
            'tool_call': None,
            'tokens_input': tokens_input,
            'tokens_output': tokens_output,
            'raw': data,
        }

        for part in parts:
            if 'functionCall' in part:
                fc = part['functionCall']
                result['tool_call'] = {
                    'name': fc.get('name'),
                    'arguments': fc.get('args', {}),
                    'id': None,
                }
            elif 'text' in part:
                result['content'] = part['text']

        return result

    def estimate_cost(self, tokens_input: int, tokens_output: int) -> float:
        cost = (tokens_input / 1000 * self.cost_per_1k_input +
                tokens_output / 1000 * self.cost_per_1k_output)
        return round(cost, 8)
