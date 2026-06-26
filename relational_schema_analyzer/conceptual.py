"""Conceptual schema model.

Mirrors ``arango-schema-mapper``'s ``schema_analyzer/conceptual.py`` so the
``conceptualSchema`` portion of the tool-contract bundle is **identical in shape**
to the ArangoDB analyzer (entities / relationships / properties as lists of plain
dicts). That shape-compatibility is what lets one downstream consumer handle both
relational and ArangoDB sources (DESIGN §7, success criterion S2).

Entity dict shape:
    {"name": str, "labels": [str], "properties": [PropertyDef], "source": "baseline"}
Relationship dict shape:
    {"type": str, "fromEntity": str, "toEntity": str,
     "properties": [PropertyDef], "source": "baseline", ...}
PropertyDef dict shape:
    {"name": str, "type": str, "nullable": bool, "indexed"?: bool, "unique"?: bool}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConceptualSchema:
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    properties: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "ConceptualSchema":
        return cls()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ConceptualSchema":
        def _as_list(value: Any) -> list[dict[str, Any]]:
            return list(value) if isinstance(value, list) else []

        return cls(
            entities=_as_list(data.get("entities", [])),
            relationships=_as_list(data.get("relationships", [])),
            properties=_as_list(data.get("properties", [])),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "relationships": self.relationships,
            "properties": self.properties,
        }

    def get_entity_by_name(self, name: str) -> dict[str, Any] | None:
        for e in self.entities:
            if isinstance(e, dict) and e.get("name") == name:
                return e
        return None

    def has_relationship_type(self, rel_type: str) -> bool:
        return any(
            isinstance(r, dict) and r.get("type") == rel_type for r in self.relationships
        )
