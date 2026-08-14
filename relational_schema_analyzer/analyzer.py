"""Top-level analyzer: PhysicalSchema -> Analysis (conceptual + mapping + metadata).

The deterministic baseline always runs and produces a complete bundle with no LLM
(DESIGN §2). An optional ``llm_provider`` enables additive refinement (Phase 4):
better semantic naming + embed/n-ary hints. Refinement never fails the analysis —
any provider/validation error falls back to the baseline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
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
    # ConceptualSchema keeps only entities/relationships/properties, so the abstraction
    # blocks are carried here rather than being silently dropped on the way through it.
    abstract_classes: list[dict[str, Any]] = field(default_factory=list)
    subclass_proposals: list[dict[str, Any]] = field(default_factory=list)

    def to_bundle(self) -> dict[str, Any]:
        """Render the tool-contract bundle ``{conceptualSchema, physicalMapping, metadata}``."""
        conceptual = self.conceptual.to_json()
        if self.abstract_classes:
            conceptual["abstractClasses"] = list(self.abstract_classes)
        if self.subclass_proposals:
            conceptual["subClassOfProposals"] = list(self.subclass_proposals)
        return {
            "conceptualSchema": conceptual,
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
        discover_taxonomy: bool = False,
        specialization_counter: Any | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.model = model
        self.api_key = api_key
        self.timeout_ms = timeout_ms
        self.max_repair_attempts = max_repair_attempts
        # Class-abstraction discovery via the shared conceptual-taxonomy library. Off by
        # default like every other optional capability here. Deterministic and database-free:
        # discriminators come from declared CHECK constraints and containment from declared
        # FK constraints, so nothing is sampled. ``specialization_counter`` is the one part
        # that needs the database — supply it to earn disjointness/completeness instead of
        # leaving them null.
        self.discover_taxonomy = discover_taxonomy
        self.specialization_counter = specialization_counter

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

    def _discover_taxonomy(
        self,
        conceptual: dict[str, Any],
        physical_mapping: dict[str, Any],
        physical: PhysicalSchema,
    ) -> dict[str, Any] | None:
        """Merge class-abstraction proposals into ``conceptual``, in place."""
        if not self.discover_taxonomy:
            return None
        from .taxonomy import TAXONOMY_AVAILABLE, discover, merge_into_bundle

        if not TAXONOMY_AVAILABLE:
            return {"status": "unavailable", "reason": "conceptual-taxonomy is not installed"}
        try:
            bundle = {"conceptualSchema": conceptual, "physicalMapping": physical_mapping}
            proposals = discover(bundle, physical, counter=self.specialization_counter)
            merge_into_bundle(bundle, proposals)
        except Exception as err:  # noqa: BLE001 - enrichment is additive; never fail
            logger.warning("abstraction discovery failed: %s", err)
            return {"status": "degraded", "reason": str(err)}

        return {
            "status": "ok",
            "abstractClasses": len((proposals or {}).get("abstractClasses") or []),
        }

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

        # After refinement: the LLM may rename entities, and taxonomy proposals reference
        # entity names, so discovering before refinement would leave dangling edges.
        taxonomy_status = self._discover_taxonomy(conceptual, physical_mapping, physical)

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
        if taxonomy_status is not None:
            metadata["taxonomyStatus"] = taxonomy_status

        return Analysis(
            conceptual=ConceptualSchema.from_json(conceptual),
            physical_mapping=PhysicalMapping.from_json(physical_mapping),
            metadata=metadata,
            abstract_classes=list(conceptual.get("abstractClasses") or []),
            subclass_proposals=list(conceptual.get("subClassOfProposals") or []),
        )
