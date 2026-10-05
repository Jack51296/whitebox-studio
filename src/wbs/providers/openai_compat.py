"""OpenAI-compatible HTTP adapters (internal model gateway or any compatible endpoint).

Endpoint and credential come only from environment variables named in configs/providers.yaml;
they are never logged or written anywhere. Skill v3 internal fields are mapped onto the gateway's
own parameters here — they are not forwarded as if they were vendor parameters.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from ..errors import ProviderNotConfigured, TransientError, WbsError
from .base import ImageProvider, ImageResult, LLMProvider, LLMResult, Usage

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def _endpoint(cfg: dict[str, Any], url_key: str, what: str) -> tuple[str, str, str]:
    url_env, key_env = cfg.get(url_key), cfg.get("api_key_env")
    base_url = os.environ.get(url_env or "", "").rstrip("/")
    api_key = os.environ.get(key_env or "", "")
    model = cfg.get("model")
    missing = [name for name, value in ((url_env, base_url), (key_env, api_key)) if not value]
    if missing or not model:
        needs = ", ".join(str(m) for m in missing) + (", providers.yaml 中的 model" if not model else "")
        raise ProviderNotConfigured(f"{what} 未配置：请设置 {needs}")
    return base_url, api_key, str(model)


def _raise_for(response: httpx.Response) -> None:
    if response.status_code in RETRYABLE:
        raise TransientError(f"HTTP {response.status_code}")
    if response.is_error:
        raise WbsError(f"HTTP {response.status_code}: {response.text[:300]}")


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class OpenAICompatLLM(LLMProvider):
    name = "openai_compatible"
    is_paid = True

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url, self._key, self.model = _endpoint(cfg, "base_url_env", "LLM 接口")
        self.timeout = float(cfg.get("timeout_s", 180))
        self.max_output_tokens = int(cfg.get("max_output_tokens", 16000))

    def complete(self, *, system: str, user: str, images: list[Path] | None = None, json_mode: bool = False,
                 task: str | None = None, payload: dict[str, Any] | None = None) -> LLMResult:
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": _data_url(p)}} for p in images]
        body: dict[str, Any] = {"model": self.model, "max_tokens": self.max_output_tokens,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": content}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException as exc:
            raise TransientError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise TransientError(f"network: {exc}") from exc
        _raise_for(response)
        data = response.json()
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        return LLMResult(
            text=data["choices"][0]["message"]["content"] or "",
            usage=Usage(input_tokens=int(usage.get("prompt_tokens", 0)), cached_input_tokens=int(cached),
                        output_tokens=int(usage.get("completion_tokens", 0))),
            model=str(data.get("model") or self.model), request_id=data.get("id"))


class OpenAICompatImage(ImageProvider):
    name = "openai_compatible"
    is_paid = True

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url, self._key, self.model = _endpoint(cfg, "base_url_env", "生图接口")
        self.size = str(cfg.get("size", "1536x864"))
        self.timeout = float(cfg.get("timeout_s", 300))

    def generate(self, *, prompt: str, input_files: list[Path], out_path: Path, image_id: str) -> ImageResult:
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            if input_files:
                files = [("image[]", (p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png"))
                         for p in input_files]
                response = httpx.post(f"{self.base_url}/images/edits", headers=headers, timeout=self.timeout,
                                      data={"model": self.model, "prompt": prompt, "size": self.size, "n": "1"},
                                      files=files)
            else:
                response = httpx.post(f"{self.base_url}/images/generations", headers=headers, timeout=self.timeout,
                                      json={"model": self.model, "prompt": prompt, "size": self.size, "n": 1})
        except httpx.TimeoutException as exc:
            raise TransientError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise TransientError(f"network: {exc}") from exc
        _raise_for(response)
        data = response.json()
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            blob = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            fetched = httpx.get(item["url"], timeout=self.timeout)
            _raise_for(fetched)
            blob = fetched.content
        else:
            raise WbsError(f"生图接口未返回图片（{image_id}）")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(blob)
        request_id = data.get("id") or data.get("created")
        return ImageResult(path=out_path, usage=Usage(images=1), model=str(data.get("model") or self.model),
                           request_id=str(request_id) if request_id else None)
