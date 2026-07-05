from __future__ import annotations

from dataclasses import dataclass

from ..defaults import ANTHROPIC_MAX_TOKENS, LLM_TEMPERATURE
from .base import LLMResponse, import_optional_sdk, wrap_provider_call


def _import_anthropic() -> object:
    return import_optional_sdk("anthropic", "anthropic")


def _extract_text(resp: object) -> str:
    text = ""
    try:
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
    except Exception:  # noqa: BLE001
        text = str(resp)
    return text


@dataclass
class AnthropicProvider:
    api_key: str

    def generate(
        self, *, model: str, system: str, prompt: str, timeout_ms: int
    ) -> LLMResponse:
        anthropic = _import_anthropic()
        client = anthropic.Anthropic(api_key=self.api_key)
        with wrap_provider_call("Anthropic request"):
            resp = client.messages.create(
                model=model,
                system=system,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_ms / 1000.0,
            )
        return LLMResponse(text=_extract_text(resp), raw=resp)
