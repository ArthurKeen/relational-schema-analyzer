"""Tunable defaults for optional LLM refinement (Phase 4)."""

from __future__ import annotations

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"

LLM_TEMPERATURE = 0.0
ANTHROPIC_MAX_TOKENS = 4096
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_ERROR_BODY_MAX_CHARS = 500

DEFAULT_LLM_TIMEOUT_MS = 60_000
MAX_REPAIR_ATTEMPTS = 2
