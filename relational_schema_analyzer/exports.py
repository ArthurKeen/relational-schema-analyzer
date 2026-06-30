"""Bundle export — the canonical tool-contract JSON view.

``export_bundle`` renders the ``{conceptualSchema, physicalMapping, metadata}``
bundle from an :class:`~relational_schema_analyzer.analyzer.Analysis` (or passes a
bundle dict through unchanged). OWL views live in :mod:`.owl_export`; relational
physical "views" for SQL-native tooling are a future addition (DESIGN §1).
"""

from __future__ import annotations

from typing import Any


def export_bundle(analysis: Any) -> dict[str, Any]:
    """Return the tool-contract bundle ``{conceptualSchema, physicalMapping, metadata}``.

    Accepts an ``Analysis`` (anything with ``to_bundle()``) or an already-built
    bundle dict.
    """
    if hasattr(analysis, "to_bundle"):
        return analysis.to_bundle()
    if isinstance(analysis, dict):
        return analysis
    raise TypeError("export_bundle expects an Analysis or a bundle dict")
