"""OpenAI-compatible HTTP provider.

Works with any backend that speaks the ``/chat/completions`` protocol:
OpenAI itself, Azure OpenAI (with a custom base URL), Ollama (which exposes
an OpenAI-compatible endpoint), LocalAI, vLLM, llama.cpp server, LM Studio,
and so on.

Uses only the standard library (``urllib``), so no extra dependency is
required. Streaming runs the blocking HTTP read in a worker thread and hands
chunks back to the event loop through a queue — this keeps the public API
fully async without pulling in an async HTTP client for Phase 1.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator
from typing import Any

from app.core.errors import ProviderError, ProviderUnavailableError
from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest,
    ChatResponse,
    ModelInfo,
    Role,
    StreamChunk,
    ToolCallRequest,
    Usage,
)


class OpenAICompatibleProvider(LLMProvider):
    """Chat-completions client for any OpenAI-compatible endpoint."""

    name = "openai_compatible"

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        return bool(self._base_url) and bool(self._model)

    # -- core operations ---------------------------------------------------
    async def chat(self, request: ChatRequest) -> ChatResponse:
        if not self.is_available():
            raise ProviderUnavailableError(
                "openai_compatible provider needs LLM_BASE_URL and LLM_MODEL"
            )

        started = time.perf_counter()
        body = self._build_body(request, stream=False)
        payload = await asyncio.to_thread(self._post_json, body)
        latency_ms = (time.perf_counter() - started) * 1000
        return self._parse_chat_response(payload, request, latency_ms)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        if not self.is_available():
            raise ProviderUnavailableError(
                "openai_compatible provider needs LLM_BASE_URL and LLM_MODEL"
            )

        body = self._build_body(request, stream=True)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[StreamChunk | BaseException | None] = asyncio.Queue()

        def produce() -> None:
            try:
                for chunk in self._iter_sse_sync(body):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except BaseException as exc:  # noqa: BLE001 - re-raised on consumer
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(
            target=produce, name="azmuth-llm-sse", daemon=True
        ).start()

        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.model or "(unset)",
            context_window=None,
            supports_streaming=True,
            extra={"base_url": self._base_url or "(unset)"},
        )

    # -- HTTP --------------------------------------------------------------
    def _chat_url(self) -> str:
        assert self._base_url is not None
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare in front of Groq blocks the default Python-urllib
            # UA and any UA using the bot-convention "+suffix". A plain
            # curl UA avoids the false positive without pretending to be
            # a browser.
            "User-Agent": "curl/8.5.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers
    

    def _build_body(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            entry: dict[str, Any] = {"role": m.role.value, "content": m.content}
            if m.role is Role.TOOL and m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            messages.append(entry)

        body: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.stop:
            body["stop"] = list(request.stop)
        if request.tools:
            body["tools"] = [t.to_openai() for t in request.tools]
            body["tool_choice"] = "auto"
        return body

    def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._chat_url(), data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"LLM request failed: {exc.reason}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("LLM returned malformed JSON") from exc

    def _iter_sse_sync(self, body: dict[str, Any]) -> Iterator[StreamChunk]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._chat_url(), data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                index = 0
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line or not line.startswith("data:"):
                        continue
                    payload_text = line[5:].strip()
                    if payload_text == "[DONE]":
                        return
                    try:
                        payload = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    for chunk in self._chunks_from_event(payload, index):
                        yield chunk
                        index += 1
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"LLM request failed: {exc.reason}") from exc

    def _parse_chat_response(
        self, payload: dict[str, Any], request: ChatRequest, latency_ms: float
    ) -> ChatResponse:
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError("LLM response contained no choices")

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""

        usage_payload = payload.get("usage") or {}
        usage = (
            Usage(
                prompt_tokens=int(usage_payload.get("prompt_tokens", 0)),
                completion_tokens=int(usage_payload.get("completion_tokens", 0)),
                total_tokens=int(usage_payload.get("total_tokens", 0)),
            )
            if usage_payload
            else None
        )

        raw_calls = message.get("tool_calls") or []
        tool_calls: list[ToolCallRequest] = []
        for rc in raw_calls:
            if not isinstance(rc, dict):
                continue
            fn = rc.get("function") or {}
            name = fn.get("name") or ""
            if not isinstance(name, str) or not name.strip():
                continue
            args_raw = fn.get("arguments")
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCallRequest(
                    id=str(rc.get("id") or ""),
                    name=name.strip(),
                    arguments=args,
                )
            )

        return ChatResponse(
            content=content,
            model=payload.get("model") or (request.model or self.model),
            provider=self.name,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            latency_ms=latency_ms,
            tool_calls=tuple(tool_calls),
        )

    @staticmethod
    def _chunks_from_event(
        payload: dict[str, Any], index: int
    ) -> Iterator[StreamChunk]:
        for choice in payload.get("choices") or []:
            delta = (choice.get("delta") or {}).get("content") or ""
            finish = choice.get("finish_reason")
            if delta or finish:
                yield StreamChunk(delta=delta, index=index, finish_reason=finish)