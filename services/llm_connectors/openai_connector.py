# -*- coding: utf-8 -*-
"""
OpenAIConnector — Conector para OpenAI (GPT-4o, GPT-4o-mini, etc.)

Soporta:
- Chat completions
- Function calling / Tool use
- Retry automático con backoff exponencial
"""

import json
import logging
import time
from typing import Optional

import requests

from .base import LLMProvider

_logger = logging.getLogger(__name__)

OPENAI_BASE_URL = 'https://api.openai.com/v1'

# Costo por 1K tokens (USD) — actualizar según pricing de OpenAI
COST_TABLE = {
    'gpt-4o': (0.005, 0.015),
    'gpt-4o-mini': (0.00015, 0.0006),
    'gpt-4-turbo': (0.01, 0.03),
    'gpt-3.5-turbo': (0.0005, 0.0015),
}

MAX_RETRIES = 3
RETRY_DELAY = 2  # segundos base para backoff


class OpenAIConnector(LLMProvider):
    """Conector para la API de OpenAI."""

    def __init__(self, api_key, model_name='gpt-4o-mini', temperature=0.3,
                 max_tokens=1024, endpoint=None):
        super().__init__(api_key, model_name, temperature, max_tokens, endpoint)
        self.base_url = endpoint or OPENAI_BASE_URL
        cost_pair = COST_TABLE.get(model_name, (0.005, 0.015))
        self.cost_per_1k_input = cost_pair[0]
        self.cost_per_1k_output = cost_pair[1]

    def send_message(self, messages: list, tools: Optional[list] = None) -> dict:
        """
        Enviar chat completion a OpenAI.

        :param messages: Lista de mensajes (role + content)
        :param tools: Lista de tools en formato JSON Schema para function calling
        :return: dict normalizado (ver base.LLMProvider.send_message)
        """
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        payload = {
            'model': self.model_name,
            'messages': messages,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
        }

        # Agregar tools si existen
        if tools:
            payload['tools'] = [
                {'type': 'function', 'function': tool}
                for tool in tools
            ]
            payload['tool_choice'] = 'auto'

        url = f'{self.base_url}/chat/completions'
        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _logger.debug(
                    'OpenAI request: model=%s attempt=%d msgs=%d tools=%d',
                    self.model_name, attempt, len(messages), len(tools or [])
                )
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, 60))
                resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data)

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else 0
                if status == 429:  # Rate limit
                    wait = RETRY_DELAY * attempt
                    _logger.warning('OpenAI rate limit. Reintentando en %ss...', wait)
                    time.sleep(wait)
                    last_error = e
                elif status >= 500:
                    wait = RETRY_DELAY * attempt
                    _logger.warning('OpenAI error 5xx. Reintentando en %ss...', wait)
                    time.sleep(wait)
                    last_error = e
                else:
                    _logger.error('OpenAI HTTP error %s: %s', status, e.response.text)
                    raise
            except requests.exceptions.RequestException as e:
                _logger.error('OpenAI request exception: %s', e)
                last_error = e
                time.sleep(RETRY_DELAY * attempt)

        raise RuntimeError(f'OpenAI: se agotaron los reintentos. Último error: {last_error}')

    def _parse_response(self, data: dict) -> dict:
        """Normalizar respuesta de OpenAI al formato interno."""
        choice = data.get('choices', [{}])[0]
        message = choice.get('message', {})
        usage = data.get('usage', {})

        tokens_input = usage.get('prompt_tokens', 0)
        tokens_output = usage.get('completion_tokens', 0)

        result = {
            'content': None,
            'tool_call': None,
            'tokens_input': tokens_input,
            'tokens_output': tokens_output,
            'raw': data,
        }

        # Tool call
        tool_calls = message.get('tool_calls')
        if tool_calls:
            tc = tool_calls[0]
            try:
                arguments = json.loads(tc['function'].get('arguments', '{}'))
            except (ValueError, KeyError):
                arguments = {}
            result['tool_call'] = {
                'name': tc['function']['name'],
                'arguments': arguments,
                'id': tc.get('id'),
            }
        else:
            result['content'] = message.get('content', '')

        return result

    def estimate_cost(self, tokens_input: int, tokens_output: int) -> float:
        cost = (tokens_input / 1000 * self.cost_per_1k_input +
                tokens_output / 1000 * self.cost_per_1k_output)
        return round(cost, 8)
