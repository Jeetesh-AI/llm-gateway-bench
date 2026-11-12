"""Upstream LLM engine backends (mock load-model & OpenAI-compatible proxy)."""

import asyncio
import json
import os
from typing import AsyncIterator, Callable, Dict, List

import httpx


class MockLLMEngine:
    """Simulates realistic token streaming with concurrency-sensitive prefill/decode."""

    def __init__(self, get_inflight: Callable[[], int]) -> None:
        self.get_inflight = get_inflight
        self.base_prefill_ms = float(os.getenv("MOCK_PREFILL_MS", "120"))
        self.per_slot_prefill_ms = float(os.getenv("MOCK_SLOT_PREFILL_MS", "28"))
        self.per_token_ms = float(os.getenv("MOCK_TOKEN_MS", "18"))

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
    ) -> AsyncIterator[str]:
        inflight = max(1, self.get_inflight())
        prefill_s = (self.base_prefill_ms + inflight * self.per_slot_prefill_ms) / 1000.0
        await asyncio.sleep(prefill_s)

        user_text = messages[-1]["content"] if messages else "hello"
        words = [
            "Measured", "gateway", "response", "under", "bounded", "admission",
            "control", "for", "query:", user_text[:24].replace("\n", " "),
            "with", "predictable", "tail", "latency.",
        ]
        target_tokens = min(max_tokens, max(8, len(words)))
        for idx in range(target_tokens):
            token = words[idx % len(words)] + (" " if idx < target_tokens - 1 else "")
            await asyncio.sleep((self.per_token_ms + inflight * 1.2) / 1000.0)
            yield token


class OpenAICompatibleEngine:
    """Streams chat completions from any OpenAI-compatible server (Ollama, Groq, vLLM)."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
    ) -> AsyncIterator[str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
