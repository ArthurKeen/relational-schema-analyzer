"""LLM provider interface (Phase 4).

Mirrors ``arango-schema-mapper``'s provider protocol: a synchronous ``generate``
that sends a prompt and returns an :class:`LLMResponse`. Provider SDKs are optional
and imported lazily behind extras.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Provider / refinement error carrying a short machine code."""

    def __init__(self, message: str, *, code: str = "PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class LLMResponse:
    text: str
    raw: Any | None = None


def import_optional_sdk(module_name: str, extra_name: str) -> Any:
    """Import an optional provider SDK or raise ``LLMError(PROVIDER_MISSING)``."""
    try:
        return importlib.import_module(module_name)
    except Exception as e:  # pragma: no cover - exercised via providers
        raise LLMError(
            f"{module_name} SDK not installed. "
            f"Install extra: pip install 'relational-schema-analyzer[{extra_name}]'",
            code="PROVIDER_MISSING",
        ) from e


@contextmanager
def wrap_provider_call(label: str) -> Iterator[None]:
    """Translate any exception raised in the block to ``LLMError(PROVIDER_ERROR)``."""
    try:
        yield
    except LLMError:
        raise
    except Exception as e:  # pragma: no cover - exercised via providers
        raise LLMError(f"{label} failed: {e}", code="PROVIDER_ERROR") from e


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous LLM provider protocol."""

    def generate(
        self, *, model: str, system: str, prompt: str, timeout_ms: int
    ) -> LLMResponse: ...
