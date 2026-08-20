"""Declared-key overlay — human-supplied PK / FK / UNIQUE for constraint-poor sources.

Some sources describe their tables faithfully and their *keys* not at all. BigQuery's
`gdelt-bq.gdeltv2` declares no primary or foreign keys; AWS Glue, Hive Metastore and Iceberg
have no constraint vocabulary to declare them with. That is not a gap in the introspection —
the catalog genuinely does not know — but it is fatal to everything downstream, because
``fk_inference`` indexes its candidate *targets* by declared primary key
(``fk_inference._build_pk_index``). No PKs anywhere means no targets, which means no inferred
relationships, which means a conceptual schema of isolated entities. Correct, and useless.

The missing information exists; it just lives in a human's head. This module is where they
write it down:

    physical = create_connector("bigquery", url).get_schema()
    physical = apply_key_overlay(physical, load_key_overlay("keys.overlay.json"))

Three properties make this an artifact rather than a hack, and each one is a rule below:

1. **The catalog always wins.** An overlay fills gaps; it never overrides a constraint the
   source actually declared. If the two disagree, the source is right and the overlay is
   stale — we warn and keep the source's.
2. **Overlay keys are labelled, not laundered.** Every FK it adds carries ``enforced=False``
   and an ``overlay:``-prefixed constraint name, and every touched table is marked in
   ``Table.extra["overlay"]``. The baseline turns that into the ``overlay_declared_keys``
   pattern, so a bundle reader can always tell catalog-declared from human-declared from
   inferred. An overlay FK is *asserted*, not *verified* — the same epistemic status as a
   Unity Catalog constraint, and it should read that way downstream.
3. **A typo fails loudly.** Unknown tables, unknown columns, mismatched column counts and
   misspelled keys are all errors. An overlay that silently does nothing is worse than no
   overlay at all: the demo runs, the ontology comes out empty, and nothing says why.

Format (JSON, or YAML when PyYAML is installed — see :func:`load_key_overlay`)::

    {
      "version": 1,
      "description": "GDELT v2 keys, from the GDELT codebook",
      "tables": {
        "events": { "primaryKey": ["GLOBALEVENTID"] },
        "gkg":    { "primaryKey": ["GKGRECORDID"] },
        "eventmentions": {
          "foreignKeys": [
            {
              "columns": ["GLOBALEVENTID"],
              "references": { "table": "events", "columns": ["GLOBALEVENTID"] },
              "comment": "GDELT codebook: mentions reference their event"
            }
          ]
        }
      }
    }

``comment`` / ``description`` are permitted anywhere and ignored — recording *why* a key was
asserted is most of the value of keeping this in version control.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from .log import get_logger
from .types import ForeignKey, PhysicalSchema, Table

logger = get_logger(__name__)

#: Prefix on every constraint name this module synthesizes, so an overlay-supplied FK is
#: distinguishable from a catalog-declared one by inspection of a snapshot alone.
OVERLAY_CONSTRAINT_PREFIX = "overlay:"

#: Key under ``Table.extra`` recording what the overlay supplied for that table.
OVERLAY_EXTRA_KEY = "overlay"

#: The only schema version understood. Bumping is how a future incompatible format announces
#: itself; refusing an unknown one is how we avoid silently misreading it.
SUPPORTED_OVERLAY_VERSION = 1

_DOC_KEYS = frozenset({"comment", "description"})
_TABLE_KEYS = frozenset({"primaryKey", "foreignKeys", "uniqueConstraints"}) | _DOC_KEYS
_FK_KEYS = frozenset({"columns", "references", "constraintName"}) | _DOC_KEYS
_REF_KEYS = frozenset({"table", "columns"}) | _DOC_KEYS
_ROOT_KEYS = frozenset({"version", "tables"}) | _DOC_KEYS


class OverlayError(ValueError):
    """An overlay that cannot be applied as written.

    A subclass of ``ValueError`` so callers that only care that the input was bad need no
    new import, while the CLI can still report overlay problems distinctly.
    """


# ── Loading ──────────────────────────────────────────────────────────────


def load_key_overlay(path: str | Path) -> dict[str, Any]:
    """Read an overlay from a ``.json`` / ``.yaml`` / ``.yml`` file.

    YAML needs PyYAML (the ``[osi]`` extra already ships it); JSON never does. The suffix
    picks the parser, and anything unrecognized is parsed as JSON — a plain ``keys.overlay``
    is far more likely to be JSON than YAML in this codebase.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise OverlayError(f"Overlay file not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]  # optional dep, no stubs shipped
        except ImportError as err:  # pragma: no cover - exercised via monkeypatch
            raise OverlayError(
                f"Reading a YAML overlay ({p.name}) requires PyYAML. Install it with: "
                "pip install 'relational-schema-analyzer[osi]' — or write the overlay as JSON."
            ) from err
        try:
            data = yaml.safe_load(text)
        except Exception as err:  # noqa: BLE001 - surface the parser's own message
            raise OverlayError(f"Failed to parse overlay {p}: {err}") from err
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise OverlayError(f"Failed to parse overlay {p}: {err}") from err
    if not isinstance(data, dict):
        raise OverlayError(f"Overlay {p} must contain an object at the top level")
    return data


# ── Application ──────────────────────────────────────────────────────────


def apply_key_overlay(schema: PhysicalSchema, overlay: dict[str, Any]) -> PhysicalSchema:
    """Return a copy of *schema* with the overlay's declared keys merged in.

    The input schema is never mutated: callers frequently keep the raw snapshot around to
    show what the source actually said, and an in-place merge would quietly destroy that
    comparison.

    Raises :class:`OverlayError` for anything the overlay gets wrong — an unknown table or
    column, a reference whose column counts disagree, an unrecognized key. See the module
    docstring for why this is strict.
    """
    _validate_root(overlay)
    result = schema.model_copy(deep=True)
    table_index = _build_name_index(result.tables, kind="table")

    for raw_name, spec in (overlay.get("tables") or {}).items():
        if not isinstance(spec, dict):
            raise OverlayError(f"Overlay entry for table '{raw_name}' must be an object")
        _reject_unknown_keys(spec, _TABLE_KEYS, f"table '{raw_name}'")
        table = result.tables[_resolve(raw_name, table_index, kind="table")]
        applied: dict[str, Any] = {}

        _apply_primary_key(table, spec.get("primaryKey"), applied)
        _apply_unique_constraints(table, spec.get("uniqueConstraints"), applied)
        _apply_foreign_keys(table, spec.get("foreignKeys"), result, table_index, applied)

        if applied:
            # The provenance marker. ``extra`` is the paradigm-neutral passthrough the model
            # already guarantees round-trips, and it is omitted from serialization when
            # empty — so a schema with no overlay fingerprints exactly as it did before.
            table.extra[OVERLAY_EXTRA_KEY] = applied

    return result


def overlay_applied(schema: PhysicalSchema) -> bool:
    """True when any table in *schema* carries overlay-supplied keys."""
    return any(t.extra.get(OVERLAY_EXTRA_KEY) for t in schema.tables.values())


def overlay_summary(schema: PhysicalSchema) -> dict[str, int]:
    """Count what an overlay contributed, for assumptions text and reporting."""
    tables = primary_keys = foreign_keys = unique_constraints = 0
    for table in schema.tables.values():
        marker = table.extra.get(OVERLAY_EXTRA_KEY)
        if not marker:
            continue
        tables += 1
        primary_keys += 1 if marker.get("primaryKey") else 0
        foreign_keys += int(marker.get("foreignKeys") or 0)
        unique_constraints += int(marker.get("uniqueConstraints") or 0)
    return {
        "tables": tables,
        "primaryKeys": primary_keys,
        "foreignKeys": foreign_keys,
        "uniqueConstraints": unique_constraints,
    }


# ── Per-construct application ────────────────────────────────────────────


def _apply_primary_key(table: Table, columns: Any, applied: dict[str, Any]) -> None:
    if columns is None:
        return
    cols = _resolve_columns(table, columns, what=f"table '{table.name}' primaryKey")
    if table.primary_key:
        # Rule 1: the catalog wins. Worth a warning either way — an overlay that agrees is
        # redundant (delete it), and one that disagrees is stale (fix it).
        if [c.lower() for c in table.primary_key] != [c.lower() for c in cols]:
            logger.warning(
                "overlay_primary_key_ignored",
                table=table.name,
                declared=table.primary_key,
                overlay=cols,
            )
        return
    table.primary_key = cols
    pk_set = {c.lower() for c in cols}
    for col in table.columns:
        if col.name.lower() in pk_set:
            col.is_primary_key = True
            # A single-column PK is by definition unique; composite membership is not.
            # Matches what every connector does when building a table from its catalog.
            if len(cols) == 1:
                col.is_unique = True
    applied["primaryKey"] = cols


def _apply_unique_constraints(table: Table, sets: Any, applied: dict[str, Any]) -> None:
    if sets is None:
        return
    if not isinstance(sets, list):
        raise OverlayError(f"table '{table.name}' uniqueConstraints must be a list of lists")
    existing = [{c.lower() for c in u} for u in table.unique_constraints]
    added = 0
    for entry in sets:
        cols = _resolve_columns(
            table, entry, what=f"table '{table.name}' uniqueConstraints entry"
        )
        if {c.lower() for c in cols} in existing:
            continue
        table.unique_constraints.append(cols)
        existing.append({c.lower() for c in cols})
        added += 1
        if len(cols) == 1:
            for col in table.columns:
                if col.name.lower() == cols[0].lower():
                    col.is_unique = True
    if added:
        applied["uniqueConstraints"] = added


def _apply_foreign_keys(
    table: Table,
    entries: Any,
    schema: PhysicalSchema,
    table_index: dict[str, str],
    applied: dict[str, Any],
) -> None:
    if entries is None:
        return
    if not isinstance(entries, list):
        raise OverlayError(f"table '{table.name}' foreignKeys must be a list")

    # Local columns already covered by a declared FK — rule 1 again, at FK granularity.
    declared = [{c.lower() for c in fk.columns} for fk in table.foreign_keys]
    added = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise OverlayError(f"table '{table.name}' foreignKeys entries must be objects")
        _reject_unknown_keys(entry, _FK_KEYS, f"table '{table.name}' foreignKeys entry")

        cols = _resolve_columns(
            table, entry.get("columns"), what=f"table '{table.name}' foreignKeys columns"
        )
        ref = entry.get("references")
        if not isinstance(ref, dict):
            raise OverlayError(
                f"table '{table.name}' foreign key on {cols} needs a 'references' object"
            )
        _reject_unknown_keys(ref, _REF_KEYS, f"table '{table.name}' foreignKeys references")

        target_name = _resolve(ref.get("table"), table_index, kind="table")
        target = schema.tables[target_name]
        ref_cols = _resolve_columns(
            target, ref.get("columns"), what=f"reference to table '{target_name}'"
        )
        if len(cols) != len(ref_cols):
            raise OverlayError(
                f"table '{table.name}' foreign key {cols} -> {target_name}{ref_cols} has "
                f"mismatched column counts ({len(cols)} vs {len(ref_cols)})"
            )
        if {c.lower() for c in cols} in declared:
            logger.warning(
                "overlay_foreign_key_ignored",
                table=table.name,
                columns=cols,
                reason="already declared by the source",
            )
            continue

        name = entry.get("constraintName") or f"{table.name}_{'_'.join(cols)}_fk"
        table.foreign_keys.append(
            ForeignKey(
                columns=cols,
                foreign_table=target_name,
                foreign_columns=ref_cols,
                constraint_name=(
                    name if name.startswith(OVERLAY_CONSTRAINT_PREFIX)
                    else OVERLAY_CONSTRAINT_PREFIX + name
                ),
                # Cardinality hint, on the same rule the connectors use: an FK whose columns
                # are unique on *this* table is 1:1, otherwise many:1. Computed here because
                # the overlay may have just supplied the uniqueness it depends on.
                is_unique=_is_unique_set(table, cols),
                # An asserted key is not a verified one. Nothing checked that the referenced
                # rows exist, which is exactly what ``enforced=False`` means everywhere else.
                enforced=False,
            )
        )
        declared.append({c.lower() for c in cols})
        added += 1
    if added:
        applied["foreignKeys"] = added


def _is_unique_set(table: Table, cols: Iterable[str]) -> bool:
    target = {c.lower() for c in cols}
    if target == {c.lower() for c in table.primary_key}:
        return True
    return any(target == {c.lower() for c in u} for u in table.unique_constraints)


# ── Validation helpers ───────────────────────────────────────────────────


def _validate_root(overlay: Any) -> None:
    if not isinstance(overlay, dict):
        raise OverlayError("Overlay must be an object")
    _reject_unknown_keys(overlay, _ROOT_KEYS, "overlay")
    version = overlay.get("version", SUPPORTED_OVERLAY_VERSION)
    if version != SUPPORTED_OVERLAY_VERSION:
        raise OverlayError(
            f"Unsupported overlay version {version!r}; this build understands "
            f"version {SUPPORTED_OVERLAY_VERSION}"
        )
    tables = overlay.get("tables")
    if tables is None:
        raise OverlayError("Overlay must declare a 'tables' object")
    if not isinstance(tables, dict):
        raise OverlayError("Overlay 'tables' must be an object keyed by table name")


def _reject_unknown_keys(obj: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Fail on a key we do not recognize.

    This is the guard that makes a typo loud: ``"primarykey"`` instead of ``"primaryKey"``
    would otherwise be accepted, applied to nothing, and discovered as an empty ontology.
    """
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise OverlayError(
            f"Unknown key(s) in {where}: {', '.join(unknown)}. "
            f"Expected one of: {', '.join(sorted(allowed))}"
        )


def _build_name_index(names: Iterable[str], *, kind: str) -> dict[str, str]:
    """Map lower-cased name → canonical name, for case-insensitive resolution.

    Dialects disagree about case (Snowflake upper-cases, Postgres lower-cases, BigQuery
    preserves), and an overlay is written by a human reading a codebook, not a catalog dump.
    Matching case-insensitively removes a whole class of false failures — but only while the
    fold is unambiguous, since silently picking one of two tables that differ by case would
    be exactly the quiet wrongness this module exists to prevent.
    """
    index: dict[str, str] = {}
    collisions: set[str] = set()
    for name in names:
        key = name.lower()
        if key in index:
            collisions.add(key)
        index[key] = name
    for key in collisions:
        del index[key]
    if collisions:
        logger.warning(
            "overlay_ambiguous_names",
            kind=kind,
            names=sorted(collisions),
            detail="differ only by case; reference them with exact case",
        )
    return index


def _resolve(name: Any, index: dict[str, str], *, kind: str) -> str:
    if not isinstance(name, str) or not name:
        raise OverlayError(f"Overlay {kind} name must be a non-empty string, got {name!r}")
    if name in index.values():
        return name
    resolved = index.get(name.lower())
    if resolved is None:
        known = ", ".join(sorted(index.values())[:10]) or "(none)"
        raise OverlayError(
            f"Overlay references unknown {kind} '{name}'. Known {kind}s: {known}"
            f"{' …' if len(index) > 10 else ''}"
        )
    return resolved


def _resolve_columns(table: Table, columns: Any, *, what: str) -> list[str]:
    if not isinstance(columns, list) or not columns:
        raise OverlayError(f"{what} must be a non-empty list of column names")
    index = _build_name_index((c.name for c in table.columns), kind="column")
    resolved: list[str] = []
    for name in columns:
        if not isinstance(name, str) or not name:
            raise OverlayError(f"{what} contains a non-string column name: {name!r}")
        match: Optional[str] = name if name in index.values() else index.get(name.lower())
        if match is None:
            known = ", ".join(sorted(index.values())[:10]) or "(none)"
            raise OverlayError(
                f"{what} references unknown column '{name}' on table '{table.name}'. "
                f"Known columns: {known}{' …' if len(index) > 10 else ''}"
            )
        resolved.append(match)
    return resolved


__all__ = [
    "OVERLAY_CONSTRAINT_PREFIX",
    "OVERLAY_EXTRA_KEY",
    "OverlayError",
    "apply_key_overlay",
    "load_key_overlay",
    "overlay_applied",
    "overlay_summary",
]
