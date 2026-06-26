"""Analysis metadata: fingerprint, confidence, contract-aligned metadata block.

Field names follow the tool contract (``confidence``, ``timestamp``,
``analyzedCollectionCounts``, ``detectedPatterns``) so emitted bundles validate
against ``docs/tool-contract/v1/response.schema.json`` (success criterion S2). The
relational-specific additions (``reviewRequired``, ``physicalSchemaFingerprint``,
``assumptions``) ride alongside via the contract's open ``metadata`` object.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .types import PhysicalSchema

GENERATOR = "relational-schema-analyzer"


def fingerprint_physical_schema(schema: PhysicalSchema) -> str:
    """Stable SHA-256 of the normalized physical schema, for drift detection.

    Uses a canonical JSON dump (sorted keys) so logically-identical schemas
    fingerprint identically regardless of table/column ordering noise.
    """
    payload = schema.model_dump_json()
    # Re-normalize via the pydantic model to ensure key ordering is canonical.
    canonical = PhysicalSchema.model_validate_json(payload).model_dump(mode="json")
    blob = _canonical_json(canonical)
    return "sha256-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _score_confidence(*, review_required: bool, relationships: list[dict[str, Any]]) -> float:
    """Deterministic baseline confidence in [0, 1].

    Starts at 0.9 (a clean, fully-declared relational schema is high-confidence
    without an LLM). Inferred relationships drag the score toward their own
    confidence; review flags apply a fixed penalty.
    """
    score = 0.9
    inferred = [r for r in relationships if r.get("inferred")]
    if inferred:
        avg_inferred = sum(float(r.get("confidence", 0.5)) for r in inferred) / len(inferred)
        score = min(score, 0.5 + 0.4 * avg_inferred)
    if review_required:
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 3)


def build_metadata(
    schema: PhysicalSchema,
    *,
    conceptual: dict[str, Any],
    detected_patterns: list[str],
    review_required: bool,
    assumptions: list[str],
    version: str,
) -> dict[str, Any]:
    entities = conceptual.get("entities", [])
    relationships = conceptual.get("relationships", [])
    confidence = _score_confidence(
        review_required=review_required, relationships=relationships
    )
    return {
        "confidence": confidence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analyzedCollectionCounts": {
            "documentCollections": len(entities),
            "edgeCollections": len(relationships),
        },
        "detectedPatterns": detected_patterns,
        "reviewRequired": review_required,
        "physicalSchemaFingerprint": fingerprint_physical_schema(schema),
        "generator": GENERATOR,
        "version": version,
        "assumptions": assumptions,
        "warnings": [],
    }
