"""Top-level analyzer: PhysicalSchema -> Analysis (conceptual + mapping + metadata).

The deterministic baseline always runs and produces a complete bundle with no LLM
(DESIGN §2). An optional ``llm_provider`` enables additive refinement (Phase 4):
better semantic naming + embed/n-ary hints. Refinement never fails the analysis —
any provider/validation error falls back to the baseline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from . import __version__
from .baseline import infer_baseline
from .conceptual import ConceptualSchema
from .defaults import DEFAULT_LLM_TIMEOUT_MS, DEFAULT_OPENAI_MODEL, MAX_REPAIR_ATTEMPTS
from .mapping import PhysicalMapping
from .metadata import build_metadata
from .refine import refine
from .types import PhysicalSchema

logger = logging.getLogger(__name__)


@dataclass
class Analysis:
    """Result of analyzing a physical schema."""

    conceptual: ConceptualSchema
    physical_mapping: PhysicalMapping
    metadata: dict[str, Any]

    def to_bundle(self) -> dict[str, Any]:
        """Render the tool-contract bundle ``{conceptualSchema, physicalMapping, metadata}``."""
        return {
            "conceptualSchema": self.conceptual.to_json(),
            "physicalMapping": self.physical_mapping.to_json(),
            "metadata": self.metadata,
        }


class RelationalSchemaAnalyzer:
    """Analyze a relational :class:`PhysicalSchema` into a conceptual bundle.

    ``llm_provider`` may be ``None`` (deterministic baseline only), a provider name
    (``"openai"`` / ``"anthropic"`` / ``"openrouter"``, resolved via the registry with
    ``api_key`` or the provider's env var), or a provider object implementing
    ``generate(...)``. The baseline path is always complete and contract-valid.
    """

    def __init__(
        self,
        llm_provider: Any | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_ms: int = DEFAULT_LLM_TIMEOUT_MS,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> None:
        self.llm_provider = llm_provider
        self.model = model
        self.api_key = api_key
        self.timeout_ms = timeout_ms
        self.max_repair_attempts = max_repair_attempts

    def _provider_and_model(self) -> tuple[Any | None, str | None]:
        if self.llm_provider is None:
            return None, None
        if isinstance(self.llm_provider, str):
            from .providers import create_provider, get_default_model, get_provider_env_var

            env_var = get_provider_env_var(self.llm_provider) or ""
            key = self.api_key or os.environ.get(env_var, "")
            provider = create_provider(self.llm_provider, api_key=key)
            return provider, (self.model or get_default_model(self.llm_provider))
        return self.llm_provider, (self.model or DEFAULT_OPENAI_MODEL)

    def analyze(self, physical: PhysicalSchema) -> Analysis:
        result = infer_baseline(physical)
        conceptual = result["conceptualSchema"]
        physical_mapping = result["physicalMapping"]

        llm_info: dict[str, Any] | None = None
        provider, model = self._provider_and_model()
        if provider is not None:
            try:
                conceptual, physical_mapping, info = refine(
                    conceptual,
                    physical_mapping,
                    provider=provider,
                    model=model or DEFAULT_OPENAI_MODEL,
                    timeout_ms=self.timeout_ms,
                    max_repair_attempts=self.max_repair_attempts,
                )
                llm_info = {"applied": True, "model": model, **info}
            except Exception as err:  # noqa: BLE001 - refinement is additive; never fail
                logger.warning("LLM refinement failed; using baseline: %s", err)
                llm_info = {"applied": False, "error": str(err)}

        metadata = build_metadata(
            physical,
            conceptual=conceptual,
            detected_patterns=result["detectedPatterns"],
            review_required=result["reviewRequired"],
            assumptions=result["assumptions"],
            version=__version__,
        )
        if llm_info is not None:
            metadata["llm"] = llm_info

        return Analysis(
            conceptual=ConceptualSchema.from_json(conceptual),
            physical_mapping=PhysicalMapping.from_json(physical_mapping),
            metadata=metadata,
        )
