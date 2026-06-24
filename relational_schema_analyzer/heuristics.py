"""Structural heuristics over a physical schema.

Extracted from ``r2g/src/r2g/config.py`` (``_is_likely_join_table``). Used by the
conceptual baseline (DESIGN §4) to classify associative / join tables as N:M
relationships rather than entities.
"""

from __future__ import annotations

from .types import Table

_JUNCTION_META = {
    "quantity",
    "qty",
    "count",
    "sort_order",
    "position",
    "rank",
    "created_at",
    "updated_at",
    "created",
    "updated",
}


def is_likely_join_table(table: Table) -> bool:
    """Heuristic: a join table has exactly 2 FKs and no non-FK, non-PK data columns
    (or only typical junction metadata like quantity, created_at, etc.)."""
    if len(table.foreign_keys) != 2:
        return False
    fk_cols: set[str] = set()
    for fk in table.foreign_keys:
        fk_cols.update(fk.columns)
    pk_cols = set(table.primary_key)
    structural = fk_cols | pk_cols
    data_cols = [c for c in table.columns if c.name not in structural]
    if not data_cols:
        return True
    return all(c.name.lower() in _JUNCTION_META for c in data_cols)


# Back-compat alias (r2g spelled this with a leading underscore).
_is_likely_join_table = is_likely_join_table
