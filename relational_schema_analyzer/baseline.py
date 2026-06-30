"""Deterministic baseline inference (no LLM).

Turns a :class:`~relational_schema_analyzer.types.PhysicalSchema` into a
``conceptualSchema`` + relational ``physicalMapping`` using the explicit rules in
DESIGN §4. Because relational schemas are explicit, this produces a high-quality
conceptual model with no LLM; ambiguous constructs are flagged for review rather
than guessed silently (success criterion S3).

Rules
-----
- Table (non-join) → Entity (``owl:Class``); style ``TABLE``.
- Column → datatype property (SQL type → JSON type via the shared type map).
- Primary key → entity identity (PK columns marked ``indexed``; a lone PK column
  marked ``unique``).
- Foreign key → ``FOREIGN_KEY`` relationship; cardinality ``1:1`` when the FK
  columns are exactly the local PK, else ``1:N``.
- Associative / join table (2 FKs, per ``is_likely_join_table``) → ``JOIN_TABLE``
  relationship (``N:M``); non-structural columns become relationship properties.
- Shared-PK-is-also-FK (single-col PK that is itself an FK) → ``rdfs:subClassOf``
  candidate, review-flagged.
- No declared FKs anywhere → run name-based ``fk_inference``; emitted relationships
  are marked ``inferred`` with their confidence and review-flagged.
"""

from __future__ import annotations

from typing import Any

from .fk_inference import infer_foreign_keys
from .naming import convert_identifier
from .typemap import pg_type_to_json_type
from .types import ForeignKey, PhysicalSchema, Table

_SOURCE_BASELINE = "baseline"


def _entity_name_for(table_name: str) -> str:
    """Derive an OWL-class-style (PascalCase) entity name from a table name.

    Matches the ArangoDB analyzer, which uses ``pascal_case(name)`` without
    singularizing (English singularization is unreliable — ``courses`` →
    ``cours`` — and reversibility is preserved via ``tableName`` in the mapping).
    """
    return convert_identifier(table_name, "pascal") or table_name


def _is_join_table(table: Table) -> bool:
    """DESIGN §4 associative table: exactly 2 FKs whose columns form the PK.

    Stricter and more principled than r2g's ``is_likely_join_table`` (which
    rejects junctions carrying arbitrary attribute columns): here any extra
    non-structural columns become relationship properties.
    """
    if len(table.foreign_keys) != 2 or not table.primary_key:
        return False
    fk_cols: set[str] = set()
    for fk in table.foreign_keys:
        fk_cols.update(fk.columns)
    return set(table.primary_key) == fk_cols


def _build_entity_name_map(schema: PhysicalSchema) -> dict[str, str]:
    """Map every table name to a unique entity name (deterministic across runs)."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for table_name in sorted(schema.tables):
        base = _entity_name_for(table_name)
        name = base
        suffix = 2
        while name in used:
            name = f"{base}{suffix}"
            suffix += 1
        used.add(name)
        mapping[table_name] = name
    return mapping


def _is_key_column(col: Any, pk: list[str]) -> bool:
    """A column is a key when it's a single-column PK or a declared unique column."""
    return bool(getattr(col, "is_unique", False)) or (col.name in pk and len(pk) == 1)


def _property_def(col: Any, pk: list[str]) -> dict[str, Any]:
    prop: dict[str, Any] = {
        "name": col.name,
        "type": pg_type_to_json_type(col.data_type),
        "nullable": bool(col.is_nullable),
    }
    if col.name in pk or _is_key_column(col, pk):
        prop["indexed"] = True
    if _is_key_column(col, pk):
        prop["unique"] = True
    return prop


def _physical_property(col: Any, pk: list[str]) -> dict[str, Any]:
    # ``field`` is the contract-required back-reference to the physical column;
    # ``sqlType`` / ``nullable`` are relational additions carried alongside.
    entry: dict[str, Any] = {
        "field": col.name,
        "sqlType": col.data_type,
        "nullable": bool(col.is_nullable),
    }
    if col.name in pk or _is_key_column(col, pk):
        entry["indexed"] = True
    if _is_key_column(col, pk):
        entry["unique"] = True
    return entry


def _cardinality_for_fk(fk: ForeignKey, local: Table) -> str:
    """``1:1`` when the FK is itself unique (or equals the local PK), else ``1:N``."""
    if getattr(fk, "is_unique", False):
        return "1:1"
    if local.primary_key and set(fk.columns) == set(local.primary_key):
        return "1:1"
    return "1:N"


def _is_shared_pk_fk(fk: ForeignKey, local: Table) -> bool:
    """True when the table's single-column PK is itself this FK (inheritance hint)."""
    return (
        len(local.primary_key) == 1
        and len(fk.columns) == 1
        and fk.columns[0] == local.primary_key[0]
    )


def _unique_rel_type(base: str, fk_columns: list[str], used: set[str]) -> str:
    """Return a relationship type unique within ``used`` (disambiguate by columns)."""
    if base not in used:
        used.add(base)
        return base
    candidate = base + "_" + "_".join(fk_columns)
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{'_'.join(fk_columns)}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def infer_baseline(schema: PhysicalSchema) -> dict[str, Any]:
    """Deterministic baseline inference. Returns a dict with keys
    ``conceptualSchema``, ``physicalMapping``, ``detectedPatterns``,
    ``reviewRequired``, ``assumptions``."""
    entity_name_by_table = _build_entity_name_map(schema)
    join_tables = {
        name for name, table in schema.tables.items() if _is_join_table(table)
    }

    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    pm_entities: dict[str, dict[str, Any]] = {}
    pm_relationships: dict[str, dict[str, Any]] = {}
    patterns: list[str] = []
    assumptions: list[str] = []
    review_required = False
    used_rel_types: set[str] = set()

    def _add_pattern(p: str) -> None:
        if p not in patterns:
            patterns.append(p)

    # ── Entities (non-join tables) ────────────────────────────────────
    for table_name in sorted(schema.tables):
        if table_name in join_tables:
            continue
        table = schema.tables[table_name]
        entity_name = entity_name_by_table[table_name]
        pk = list(table.primary_key)

        props = [_property_def(c, pk) for c in table.columns]
        entity: dict[str, Any] = {
            "name": entity_name,
            "labels": [entity_name],
            "properties": props,
            "source": _SOURCE_BASELINE,
        }
        entities.append(entity)

        pm_props = {c.name: _physical_property(c, pk) for c in table.columns}
        entity_map: dict[str, Any] = {
            "style": "TABLE",
            "tableName": table_name,
            "primaryKey": pk,
            "properties": pm_props,
        }
        if schema.tables[table_name].partition_of:
            entity_map["partitionOf"] = schema.tables[table_name].partition_of
        pm_entities[entity_name] = entity_map

        if not pk:
            review_required = True
            assumptions.append(
                f"Table '{table_name}' has no primary key; entity identity is unresolved."
            )
            _add_pattern("missing_primary_key")

    # ── Relationships from declared FKs + join tables ──────────────────
    declared_fk_count = sum(len(t.foreign_keys) for t in schema.tables.values())

    # Join tables → N:M relationships.
    for table_name in sorted(join_tables):
        table = schema.tables[table_name]
        _add_pattern("join_table")
        fk1, fk2 = table.foreign_keys[0], table.foreign_keys[1]
        from_entity = entity_name_by_table[fk1.foreign_table]
        to_entity = entity_name_by_table[fk2.foreign_table]

        structural = set(table.primary_key)
        for fk in table.foreign_keys:
            structural.update(fk.columns)
        attribute_cols = [c for c in table.columns if c.name not in structural]
        rel_props = [_property_def(c, []) for c in attribute_cols]

        base = f"{from_entity}_{to_entity}"
        rel_type = _unique_rel_type(base, table.primary_key or [table_name], used_rel_types)

        relationships.append(
            {
                "type": rel_type,
                "fromEntity": from_entity,
                "toEntity": to_entity,
                "properties": rel_props,
                "cardinality": "N:M",
                "source": _SOURCE_BASELINE,
            }
        )
        pm_relationships[rel_type] = {
            "style": "JOIN_TABLE",
            "joinTable": table_name,
            "joinFromColumns": list(fk1.columns),
            "joinToColumns": list(fk2.columns),
            "attributeColumns": [c.name for c in attribute_cols],
        }

    # Declared FKs on entity tables → FOREIGN_KEY relationships.
    for table_name in sorted(schema.tables):
        if table_name in join_tables:
            continue
        table = schema.tables[table_name]
        from_entity = entity_name_by_table[table_name]
        for fk in table.foreign_keys:
            if fk.foreign_table not in entity_name_by_table:
                continue
            to_entity = entity_name_by_table[fk.foreign_table]
            cardinality = _cardinality_for_fk(fk, table)

            base = f"{from_entity}_{to_entity}"
            rel_type = _unique_rel_type(base, fk.columns, used_rel_types)

            rel: dict[str, Any] = {
                "type": rel_type,
                "fromEntity": from_entity,
                "toEntity": to_entity,
                "properties": [],
                "cardinality": cardinality,
                "source": _SOURCE_BASELINE,
            }
            relationships.append(rel)
            pm_relationships[rel_type] = {
                "style": "FOREIGN_KEY",
                "fromTable": table_name,
                "fromColumns": list(fk.columns),
                "toTable": fk.foreign_table,
                "toColumns": list(fk.foreign_columns),
            }

            if _is_shared_pk_fk(fk, table):
                _add_pattern("inheritance_via_shared_pk")
                review_required = True
                entity = next(e for e in entities if e["name"] == from_entity)
                entity["subClassOf"] = to_entity
                assumptions.append(
                    f"Entity '{from_entity}' shares its primary key with FK → "
                    f"'{to_entity}'; candidate rdfs:subClassOf (review)."
                )

    # ── Fallback: infer FKs when none are declared ─────────────────────
    if declared_fk_count == 0 and len(schema.tables) > 1:
        inferred = infer_foreign_keys(schema)
        for cand in inferred:
            if cand.table in join_tables or cand.foreign_table not in entity_name_by_table:
                continue
            if cand.table not in entity_name_by_table:
                continue
            from_entity = entity_name_by_table[cand.table]
            to_entity = entity_name_by_table[cand.foreign_table]
            local = schema.tables[cand.table]
            cardinality = (
                "1:1"
                if local.primary_key and set(cand.columns) == set(local.primary_key)
                else "1:N"
            )
            base = f"{from_entity}_{to_entity}"
            rel_type = _unique_rel_type(base, cand.columns, used_rel_types)
            relationships.append(
                {
                    "type": rel_type,
                    "fromEntity": from_entity,
                    "toEntity": to_entity,
                    "properties": [],
                    "cardinality": cardinality,
                    "source": _SOURCE_BASELINE,
                    "inferred": True,
                    "confidence": cand.confidence,
                }
            )
            pm_relationships[rel_type] = {
                "style": "FOREIGN_KEY",
                "fromTable": cand.table,
                "fromColumns": list(cand.columns),
                "toTable": cand.foreign_table,
                "toColumns": list(cand.foreign_columns),
                "inferred": True,
            }
        if inferred:
            _add_pattern("inferred_foreign_keys")
            review_required = True
            assumptions.append(
                f"No foreign keys were declared; {len(inferred)} relationship(s) "
                f"were inferred from naming heuristics (review)."
            )

    return {
        "conceptualSchema": {
            "entities": entities,
            "relationships": relationships,
            "properties": [],
        },
        "physicalMapping": {
            "entities": pm_entities,
            "relationships": pm_relationships,
        },
        "detectedPatterns": patterns,
        "reviewRequired": review_required,
        "assumptions": assumptions,
    }
