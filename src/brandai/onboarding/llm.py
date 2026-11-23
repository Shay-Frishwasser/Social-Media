from __future__ import annotations

import json
from typing import Protocol

import httpx


class StructuredLLMProvider(Protocol):
    def complete_json(self, *, system: str, user: str, schema: dict) -> dict: ...


class LlamaCppProvider:
    def __init__(self, base_url: str, timeout_seconds: float = 900):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        payload = {
            "model": "local", "temperature": 0.1, "max_tokens": 2200, "seed": 20261122,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "brand_extraction", "strict": True, "schema": schema}},
        }
        with httpx.Client(timeout=self.timeout, trust_env=False) as client:
            response = client.post(f"{self.base_url}/v1/chat/completions", json=payload)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not content.strip(): raise ValueError("local LLM returned empty structured content")
        return json.loads(content)
