from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

import httpx


class OllamaAuthError(RuntimeError):
    pass


class OllamaRateLimitError(RuntimeError):
    pass


class OllamaServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class OllamaResponse:
    content: str
    raw: dict[str, Any]


def _extract_content(data: dict[str, Any]) -> str:
    # Matches the common Ollama schema: { message: { content: "..." } }
    msg = data.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"]

    # Fallbacks (just in case the hosted API differs)
    if isinstance(data.get("content"), str):
        return data["content"]

    # Some APIs return {choices:[{message:{content}}]}
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            m = first.get("message")
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                return m["content"]

    raise RuntimeError("Unexpected Ollama response schema")


async def chat_with_key_rotation(
    *,
    api_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_keys: Iterable[str],
    timeout_s: float = 60.0,
) -> OllamaResponse:
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for api_key in api_keys:
            try:
                resp = await client.post(
                    api_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                    },
                )

                if resp.status_code in (401, 403):
                    raise OllamaAuthError(f"Auth failed ({resp.status_code})")
                if resp.status_code == 429:
                    raise OllamaRateLimitError("Rate limited (429)")
                if resp.status_code >= 500:
                    raise OllamaServerError(f"Server error ({resp.status_code})")

                resp.raise_for_status()
                data = resp.json()
                content = _extract_content(data)
                return OllamaResponse(content=content, raw=data)

            except (OllamaAuthError, OllamaRateLimitError, OllamaServerError, httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                continue

    raise RuntimeError(f"All API keys failed; last error: {last_error!r}")
