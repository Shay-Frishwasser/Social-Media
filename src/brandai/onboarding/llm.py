from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from typing import Protocol

import httpx


class StructuredLLMProvider(Protocol):
    def complete_json(self, *, system: str, user: str, schema: dict) -> dict: ...


class DeterministicEvidenceProvider:
    """Propose verbatim site excerpts for human review without a model."""

    def __init__(self, website_url: str):
        self.host = (urlsplit(website_url).hostname or "").removeprefix("www.")

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        evidence = json.loads(user.split("\n", 1)[1])
        claims = []
        seen = set()
        for source in evidence:
            host = (urlsplit(source["url"] or "").hostname or "").removeprefix("www.")
            if source.get("kind") != "website" or host != self.host:
                continue
            for candidate in re.split(r"(?<=[.!?])\s+|\n+", source["text"]):
                quote = candidate.strip()
                if not 24 <= len(quote) <= 240 or quote in seen or sum(char.isalpha() for char in quote) < 12:
                    continue
                seen.add(quote)
                claims.append({"field_key": f"website_statement_{len(claims) + 1}", "value": quote,
                               "origin": "observed", "source_id": source["source_id"], "quoted_span": quote,
                               "confidence": 0.5})
                if len(claims) == 5:
                    break
            if len(claims) == 5:
                break
        if not claims:
            raise ValueError("the site has no suitable verbatim excerpts for review")
        return {"claims": claims, "knowledge_gaps": [], "conflicts": []}


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
