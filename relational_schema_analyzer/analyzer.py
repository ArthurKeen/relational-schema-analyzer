"""Top-level analyzer: PhysicalSchema -> Analysis (conceptual + mapping + metadata).

The deterministic baseline always runs and produces a complete bundle with no LLM
(DESIGN §2). An optional ``llm_provider`` is accepted for forward-compatibility with
Phase 4 refinement; it is not used yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import __version__
from .baseline import infer_baseline
from .conceptual import ConceptualSchema
from .mapping import PhysicalMapping
from .metadata import build_metadata
from .types import PhysicalSchema


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

    ``llm_provider=None`` (the default) runs the deterministic baseline only. The
    baseline path is always complete and contract-valid; the LLM is additive
    refinement (Phase 4) and not yet wired in.
    """

    def __init__(self, llm_provider: Any | None = None) -> None:
        self.llm_provider = llm_provider

    def analyze(self, physical: PhysicalSchema) -> Analysis:
        result = infer_baseline(physical)
        conceptual = ConceptualSchema.from_json(result["conceptualSchema"])
        physical_mapping = PhysicalMapping.from_json(result["physicalMapping"])
        metadata = build_metadata(
            physical,
            conceptual=result["conceptualSchema"],
            detected_patterns=result["detectedPatterns"],
            review_required=result["reviewRequired"],
            assumptions=result["assumptions"],
            version=__version__,
        )
        return Analysis(
            conceptual=conceptual,
            physical_mapping=physical_mapping,
            metadata=metadata,
        )
