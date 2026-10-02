"""Primary-key candidates from data, for sources that declare none.

FK inference targets only declared candidate keys, so a source that declares no
primary keys -- Snowflake by habit, lakes and warehouses by design -- yields no
relationships at all. This module proposes keys from the data. It never applies
them: the result is evidence for a person (or a draft key overlay) to review,
because a wrong identity key creates duplicate records that surface much later.

**Method.** For each table without a declared primary key:

1. *Screen on a sample.* One bounded query gives, per column, rows / non-null /
   distinct over the first ``probe.limit`` rows. A duplicate or a NULL in the
   sample is proof the column is not a key, so this rejection is sound.
2. *Confirm exactly.* Survivors are re-counted over the whole table -- unless the
   sample already covered every row. A candidate is never reported on sample
   evidence alone.
3. *Composite keys.* Only when no single column qualifies: pairs of
   identifier-like columns, bounded by ``max_pair_checks``.
4. *Rank.* Uniqueness alone does not make a key -- an email or a name can be
   unique today by accident. Candidates are scored on generic conventions
   (identifier-like names, a name derived from the table, type, position; a
   column named after *another* table counts against it, being the foreign-key
   convention) and every score carries the reasons behind it.

Queries go through a :class:`KeyProbe`, so cost governance is the probe's job;
:class:`~relational_schema_analyzer.fk_inference.SnowflakeValueSampler` is one,
with a query budget and statement timeout. A probe that declines (budget spent,
query failed) leaves the table in ``not_evaluated`` rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Optional, Protocol, Sequence

from .naming import singularize
from .types import Schema, Table

ColumnStats = tuple[int, int, int]
"""``(rows, non_null, distinct)`` for one column over the probed rows."""


class KeyProbe(Protocol):
    """What the profiler needs from an engine. ``None`` means "declined"."""

    limit: int

    def column_stats(
        self, table: str, columns: Sequence[str], *, sample: bool
    ) -> Optional[dict[str, ColumnStats]]: ...

    def combo_stats(
        self, table: str, columns: Sequence[str], *, sample: bool
    ) -> Optional[ColumnStats]: ...


@dataclass(frozen=True)
class KeyCandidate:
    """A column set that is non-null and unique over the whole table."""

    table: str
    columns: tuple[str, ...]
    score: float
    rows: int
    distinct: int
    reasons: tuple[str, ...]

    def as_evidence(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "score": round(self.score, 3),
            "rows": self.rows,
            "distinct": self.distinct,
            "reasons": list(self.reasons),
        }


@dataclass
class KeyProfile:
    """Ranked candidates per table (best first), and what could not be decided."""

    candidates: dict[str, list[KeyCandidate]] = field(default_factory=dict)
    not_evaluated: dict[str, str] = field(default_factory=dict)
    declared: list[str] = field(default_factory=list)

    def best(self, table: str) -> Optional[KeyCandidate]:
        found = self.candidates.get(table) or []
        return found[0] if found else None


# Types that are never sensible identity keys. Matched on the raw type name
# because Snowflake reports NUMBER without scale, so the category cannot tell
# an integer from an amount.
_UNSUITABLE_CATEGORIES = {"boolean", "json", "array", "binary"}
_FLOAT_TYPE = re.compile(r"^(float|double|real|binary_double|binary_float)", re.I)
# ACCOUNT_ID / account_key / ORDER_NO, or camelCase accountId -- but not PAID or GRID.
_CAMEL_ID = re.compile(r"[a-z0-9]Id$")


def _id_like(name: str) -> bool:
    if _CAMEL_ID.search(name):
        return True
    return bool(re.search(r"_(?:id|key|no|num|number|code)$|^uuid$", name, re.I))


_DESCRIPTIVE = re.compile(r"(name|email|title|description|url|phone|address)", re.I)

#: Below this many rows, "every value is distinct" is weak evidence.
MIN_CONVINCING_ROWS = 10


def _suitable(col: Any) -> bool:
    if col.type_category in _UNSUITABLE_CATEGORIES:
        return False
    return not _FLOAT_TYPE.match((col.data_type or "").strip())


def _is_unique_non_null(stats: ColumnStats) -> bool:
    rows, non_null, distinct = stats
    return rows > 0 and non_null == rows and distinct == rows


def _score_single(
    table: Table, col: Any, position: int, rows: int, other_tables: dict[str, str]
) -> tuple[float, list[str]]:
    name = col.name
    stem = singularize(table.name).lower()
    score, reasons = 0.5, ["non-null and unique over every row"]
    # <other_table>_ID is the foreign-key naming convention FK inference itself
    # relies on. Unique here usually means "one row per referenced thing" (one
    # event per contact in a small table), not that it identifies this table.
    referenced = other_tables.get(name.lower())
    if referenced is not None:
        score -= 0.2
        reasons.append(f"named after another table ({referenced}), so likely a reference to it")
    if name.lower() in (f"{stem}_id", f"{stem}id"):
        score += 0.3
        reasons.append(f"named after its table ({table.name} -> {name})")
    elif name.lower() == "id":
        score += 0.3
        reasons.append("named ID")
    elif _id_like(name):
        score += 0.15
        reasons.append("named like an identifier")
    if _DESCRIPTIVE.search(name) and not _id_like(name):
        score -= 0.15
        reasons.append("looks like a descriptive attribute, which can be unique by accident")
    if position == 0:
        score += 0.05
        reasons.append("first column")
    if col.type_category in ("integer", "uuid"):
        score += 0.05
        reasons.append(f"{col.type_category} type")
    elif col.type_category == "temporal":
        score -= 0.2
        reasons.append("timestamps are often unique by accident")
    if rows < MIN_CONVINCING_ROWS:
        score *= 0.5
        reasons.append(f"only {rows} rows: uniqueness is weak evidence")
    return max(0.0, min(1.0, score)), reasons


def profile_primary_keys(
    schema: Schema,
    probe: KeyProbe,
    *,
    tables: Optional[Sequence[str]] = None,
    max_pair_checks: int = 10,
) -> KeyProfile:
    """Propose primary keys for tables that declare none. Never mutates ``schema``."""
    profile = KeyProfile()
    names = list(tables) if tables is not None else list(schema.tables)
    # "<singular>_id" / "<singular>id" -> table, for every table in the schema.
    stems: dict[str, str] = {}
    for other in schema.tables:
        singular = singularize(other).lower()
        stems[f"{singular}_id"] = other
        stems[f"{singular}id"] = other
    for tname in names:
        table = schema.tables.get(tname)
        if table is None:
            profile.not_evaluated[tname] = "not in schema"
            continue
        if table.primary_key:
            profile.declared.append(tname)
            continue
        if getattr(table, "is_view", False):
            profile.not_evaluated[tname] = "view: uniqueness of a view is not a key"
            continue
        others = {k: v for k, v in stems.items() if v != tname}
        _profile_table(table, probe, profile, max_pair_checks, others)
    return profile


def _profile_table(
    table: Table,
    probe: KeyProbe,
    profile: KeyProfile,
    max_pair_checks: int,
    other_tables: dict[str, str],
) -> None:
    usable = [(i, c) for i, c in enumerate(table.columns) if _suitable(c)]
    if not usable:
        profile.not_evaluated[table.name] = "no column of a type suitable for a key"
        return

    # 1. Screen on a sample: a duplicate or NULL here is conclusive.
    sample = probe.column_stats(table.name, [c.name for _, c in usable], sample=True)
    if sample is None:
        profile.not_evaluated[table.name] = "probe declined (budget spent or query failed)"
        return
    sampled_rows = next(iter(sample.values()))[0] if sample else 0
    if sampled_rows == 0:
        profile.not_evaluated[table.name] = "empty table"
        return
    survivors = [(i, c) for i, c in usable if _is_unique_non_null(sample[c.name])]

    # 2. Confirm over the whole table, unless the sample already was the whole table.
    whole_table_seen = sampled_rows < probe.limit
    exact: dict[str, ColumnStats] = sample
    if survivors and not whole_table_seen:
        confirmed = probe.column_stats(table.name, [c.name for _, c in survivors], sample=False)
        if confirmed is None:
            profile.not_evaluated[table.name] = "probe declined during confirmation"
            return
        exact = confirmed

    found: list[KeyCandidate] = []
    for position, col in survivors:
        stats = exact[col.name]
        if not _is_unique_non_null(stats):
            continue  # unique in the sample only
        score, reasons = _score_single(table, col, position, stats[0], other_tables)
        found.append(
            KeyCandidate(table.name, (col.name,), score, stats[0], stats[2], tuple(reasons))
        )

    # 3. Composite keys, only when no single column qualifies.
    if not found:
        id_like = [c.name for _, c in usable if _id_like(c.name)]
        for pair in list(combinations(id_like, 2))[:max_pair_checks]:
            screened = probe.combo_stats(table.name, pair, sample=not whole_table_seen)
            if screened is None or not _is_unique_non_null(screened):
                continue
            stats = screened
            if not whole_table_seen:
                full = probe.combo_stats(table.name, pair, sample=False)
                if full is None or not _is_unique_non_null(full):
                    continue
                stats = full
            reasons = [
                "no single column is a key",
                "pair is non-null and unique over every row",
                "both columns named like identifiers",
            ]
            score = 0.6 if stats[0] >= MIN_CONVINCING_ROWS else 0.3
            found.append(KeyCandidate(table.name, pair, score, stats[0], stats[2], tuple(reasons)))

    found.sort(key=lambda k: (-k.score, len(k.columns), k.columns))
    profile.candidates[table.name] = found
