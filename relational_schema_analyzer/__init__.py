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

__version__ = "0.1.0"

# Target public API (implemented incrementally — see IMPLEMENTATION-PLAN.md).
__all__ = [
    "__version__",
    # Phase 1 (physical core, extracted from r2g):
    "create_connector",
    "PhysicalSchema",
    # Phase 2 (conceptual model + baseline):
    "RelationalSchemaAnalyzer",
    "ConceptualSchema",
    # Phase 3 (exports):
    "export_bundle",
    "export_owl_turtle",
    "export_owl_jsonld",
]
