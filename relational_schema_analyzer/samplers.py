"""Concrete implementations of the injected seams — the parts that need a database.

Three detectors delegate their only DB-touching step to a callable so the logic around them
stays paradigm-neutral and testable:

* ``fk_inference.Sampler`` — value overlap (already implemented per connector)
* ``discriminator.ValueEnumerator`` — distinct values of a candidate type column
* ``taxonomy.SpecializationCounter`` — how many parent rows appear in one, several, or no
  subtype tables

The latter two had no implementation at all, which meant that in practice discriminator
detection ran only on declared ``CHECK`` constraints and specialization constraints were
always ``null``. Both are implemented here against the DB-API cursor every connector
already exposes, so they work on any source whose driver speaks it.

SQL is kept to the intersection of the dialects RSA supports — no window functions, no
lateral joins, no ``FILTER``. Identifiers are quoted, never interpolated raw.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Anything exposing DB-API ``cursor()``, or a callable returning rows for a SQL string.
Executor = Callable[[str, tuple[Any, ...]], Optional[list[tuple[Any, ...]]]]


def _quote(identifier: str, *, schema: str | None = None) -> str:
    """Double-quote an identifier, escaping embedded quotes.

    Table and column names cannot travel as bind parameters, so this is the guard. Doubling
    an embedded quote is the SQL standard escape and is accepted by every dialect here.
    """
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("identifier must be a non-empty string")
    quoted = '"' + identifier.replace('"', '""') + '"'
    if schema:
        return '"' + schema.replace('"', '""') + '".' + quoted
    return quoted


def executor_from_connection(connection: Any) -> Executor:
    """Adapt a DB-API connection to the ``Executor`` shape.

    Returns ``None`` on failure rather than raising: a detector that cannot measure must
    degrade to "unmeasured", never to a wrong answer or a failed analysis.
    """

    def execute(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]] | None:
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params) if params else cursor.execute(sql)
                return list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as err:  # noqa: BLE001
            logger.warning("sampler query failed: %s", err)
            return None

    return execute


def make_value_enumerator(
    executor: Executor, *, schema: str | None = None
) -> Callable[[str, str, int], list[str] | None]:
    """Distinct values of a column, for ``discriminator.detect_discriminators``.

    Bounded by ``limit + 1`` so a high-cardinality column is rejected on cardinality without
    reading it — the detector's own gate then drops anything above ``max_distinct``.
    """

    def enumerate_values(table: str, column: str, limit: int) -> list[str] | None:
        sql = (
            f"SELECT DISTINCT {_quote(column)} FROM {_quote(table, schema=schema)} "
            f"WHERE {_quote(column)} IS NOT NULL"
        )
        rows = executor(f"{sql} LIMIT {int(limit) + 1}", ())
        if rows is None:
            # Not every dialect spells the row cap the same way; fall back to an unbounded
            # DISTINCT, which is still bounded in practice by the column's cardinality.
            rows = executor(sql, ())
        if rows is None:
            return None
        return [str(r[0]) for r in rows if r and r[0] is not None]

    return enumerate_values


def make_specialization_counter(
    executor: Executor, *, schema: str | None = None
) -> Callable[[str, str, list[str]], dict[str, int] | None]:
    """Parent-key membership across subtype tables, for the ER specialization constraints.

    Counts, over a bounded sample of parent rows, how many appear in more than one subtype
    (overlapping specialization) and how many appear in none (partial specialization). The
    shared library turns those into ``disjoint`` / ``complete``; absent this, both stay
    ``null`` — which is correct but uninformative.

    One query, using scalar subqueries rather than joins so the row count cannot be inflated
    by a subtype with duplicate keys.
    """

    def count(parent: str, pk: str, children: list[str]) -> dict[str, int] | None:
        if not children:
            return None
        parent_ref = _quote(parent, schema=schema)
        pk_ref = _quote(pk)
        memberships = " + ".join(
            "(SELECT COUNT(*) FROM {child} c WHERE c.{pk} = p.{pk})".format(
                child=_quote(child, schema=schema), pk=pk_ref
            )
            for child in children
        )
        sql = (
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END) AS in_multiple, "
            "SUM(CASE WHEN n = 0 THEN 1 ELSE 0 END) AS in_none FROM ("
            f"SELECT ({memberships}) AS n FROM {parent_ref} p"
            ") s"
        )
        rows = executor(sql, ())
        if not rows or not rows[0]:
            return None
        total, in_multiple, in_none = (rows[0] + (None, None, None))[:3]
        if total is None:
            return None
        return {
            "total": int(total),
            "inMultiple": int(in_multiple or 0),
            "inNone": int(in_none or 0),
        }

    return count
