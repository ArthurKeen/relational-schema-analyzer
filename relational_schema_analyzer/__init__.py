"""relational-schema-analyzer.

Analyze a relational database schema into a conceptual model (entities /
relationships / properties) with a mapping back to the source relational schema,
plus optional OWL (Turtle / JSON-LD) exports.

This is the relational analogue of ``arangodb-schema-analyzer`` and emits the same
tool-contract bundle shape ``{conceptualSchema, physicalMapping, metadata}``.

See ``docs/DESIGN.md`` and ``docs/IMPLEMENTATION-PLAN.md``. The implementation is
delivered in phases; the public API below is the target surface (Phase 1+).
"""

from __future__ import annotations

from .connectors import (
    SUPPORTED_SOURCE_TYPES,
    SourceConnector,
    create_connector,
    create_source_connector,
)
from .fk_inference import (
    InferenceOptions,
    InferredForeignKey,
    create_value_sampler,
    infer_foreign_keys,
)
from .schema_diff import diff_schemas
from .topo_sort import topological_sort_tables
from .types import Column, ForeignKey, PhysicalSchema, Schema, Table

__version__ = "0.1.0"

# Phase 1 (physical core, extracted from r2g) — implemented.
# Phase 2+ names (RelationalSchemaAnalyzer, ConceptualSchema, export_bundle,
# export_owl_turtle, export_owl_jsonld) land in later phases per IMPLEMENTATION-PLAN.md.
__all__ = [
    "__version__",
    "create_connector",
    "create_source_connector",
    "SourceConnector",
    "SUPPORTED_SOURCE_TYPES",
    "PhysicalSchema",
    "Schema",
    "Table",
    "Column",
    "ForeignKey",
    "diff_schemas",
    "topological_sort_tables",
    "infer_foreign_keys",
    "InferredForeignKey",
    "InferenceOptions",
    "create_value_sampler",
]
