from __future__ import annotations

from dataclasses import dataclass

from ..defaults import LLM_TEMPERATURE
from .base import LLMResponse, import_optional_sdk, wrap_provider_call


def _import_openai() -> object:
    return import_optional_sdk("openai", "openai")


@dataclass
class OpenAIProvider:
    api_key: str

    def generate(
        self, *, model: str, system: str, prompt: str, timeout_ms: int
    ) -> LLMResponse:
        openai = _import_openai()
        client = openai.OpenAI(api_key=self.api_key)
        with wrap_provider_call("OpenAI request"):
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=LLM_TEMPERATURE,
                timeout=timeout_ms / 1000.0,
            )
        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)
