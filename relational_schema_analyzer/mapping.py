"""Relational physical mapping model.

The relational analogue of ``arango-schema-mapper``'s ``schema_analyzer/mapping.py``.
Records the back-reference from each conceptual element to the relational source so
downstream consumers (and OWL ``phys:*`` annotations) can resolve a concept to the
exact table / column / FK it came from (DESIGN §3.3).

Style enums diverge from the ArangoDB analyzer (the documented relational variant):

    Entity:        TABLE
    Relationship:  FOREIGN_KEY | JOIN_TABLE

Entity mapping dict shape:
    {"style": "TABLE", "tableName": str, "schema"?: str,
     "primaryKey": [str],
     "properties": {conceptualName: {"columnName": str, "sqlType": str,
                                     "nullable": bool, "unique"?: bool}}}
Relationship mapping dict shapes:
    FOREIGN_KEY: {"style": "FOREIGN_KEY", "fromTable", "fromColumns",
                  "toTable", "toColumns"}
    JOIN_TABLE:  {"style": "JOIN_TABLE", "joinTable", "joinFromColumns",
                  "joinToColumns", "attributeColumns"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EntityMappingStyle = Literal["TABLE"]
RelationshipMappingStyle = Literal["FOREIGN_KEY", "JOIN_TABLE"]


@dataclass
class PhysicalMapping:
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "PhysicalMapping":
        return cls()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PhysicalMapping":
        ent = data.get("entities", {})
        rel = data.get("relationships", {})
        return cls(
            entities=dict(ent) if isinstance(ent, dict) else {},
            relationships=dict(rel) if isinstance(rel, dict) else {},
        )

    def to_json(self) -> dict[str, Any]:
        return {"entities": self.entities, "relationships": self.relationships}

    def get_entity_mapping(self, entity_name: str) -> dict[str, Any] | None:
        return self.entities.get(entity_name)

    def get_relationship_mapping(self, rel_type: str) -> dict[str, Any] | None:
        return self.relationships.get(rel_type)
