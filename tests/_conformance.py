"""Capability-aware connector conformance harness.

One canonical "shop" schema and one shared assertion set, run against whichever
backends are available — embedded emulators (fakesnow), live Docker engines
(Postgres/MySQL/SQL Server), or opt-in cloud — so every dialect is held to the
same expectations. Backends declare a ``capabilities`` set; only supported
features are asserted (e.g. fakesnow can't introspect FKs/uniques/views, so those
are skipped for it, while the always-present core is still checked).

The canonical schema (create it however each dialect requires):

    users(id PK, email NOT NULL UNIQUE, status DEFAULT 'active', created_at timestamp)
        table comment "people"; column comment on email
    orders(id PK, user_id NOT NULL FK -> users(id), total numeric)
    active_users VIEW  (optional; only asserted when VIEWS capability is set)
"""

from __future__ import annotations

from typing import Any

# Capability flags.
ORDINAL = "ordinal"
DEFAULTS = "defaults"
COMMENTS = "comments"
UNIQUE = "unique"  # non-PK unique constraints introspected
FOREIGN_KEYS = "foreign_keys"
VIEWS = "views"
PROVENANCE_VERSION = "provenance_version"

# integer-family categories (Snowflake INT -> NUMBER -> "decimal").
_INT_CATEGORIES = {"integer", "decimal"}


def _find_table(schema: Any, name: str) -> Any:
    for key, table in schema.tables.items():
        if key.lower() == name.lower():
            return table
    raise AssertionError(f"table {name!r} not found in {sorted(schema.tables)}")


def _has_table(schema: Any, name: str) -> bool:
    return any(k.lower() == name.lower() for k in schema.tables)


def _find_col(table: Any, name: str) -> Any:
    for col in table.columns:
        if col.name.lower() == name.lower():
            return col
    raise AssertionError(f"column {name!r} not found in {[c.name for c in table.columns]}")


def _lower(values: Any) -> set[str]:
    return {str(v).lower() for v in values}


def assert_shop_conformance(
    schema: Any, *, dialect: str, capabilities: set[str]
) -> None:
    """Assert the canonical shop schema was introspected correctly.

    Core structure (tables, columns, types, PK) is always checked; each feature in
    ``capabilities`` adds its assertions. Name-casing is normalized so the same
    checks pass for upper-casing (Snowflake) and lower-casing (Postgres) engines.
    """
    # ── Core: entities + columns + normalized types + PK ──────────────
    users = _find_table(schema, "users")
    orders = _find_table(schema, "orders")

    ucols = {c.name.lower(): c for c in users.columns}
    assert {"id", "email", "status", "created_at"} <= set(ucols)
    assert _find_col(users, "id").type_category in _INT_CATEGORIES
    assert _find_col(users, "email").type_category == "string"
    assert _find_col(users, "created_at").type_category == "temporal"
    assert _lower(users.primary_key) == {"id"}
    assert _lower(orders.primary_key) == {"id"}

    # ── Gated features ────────────────────────────────────────────────
    if ORDINAL in capabilities:
        ordinals = [c.ordinal for c in users.columns]
        assert all(o is not None for o in ordinals)
        assert ordinals == sorted(ordinals)

    if DEFAULTS in capabilities:
        assert _find_col(users, "status").default is not None

    if COMMENTS in capabilities:
        assert users.comment == "people"
        assert _find_col(users, "email").comment == "contact email"

    if UNIQUE in capabilities:
        email_unique = _find_col(users, "email").is_unique or any(
            _lower(u) == {"email"} for u in users.unique_constraints
        )
        assert email_unique, "expected email to be introspected as unique"

    if FOREIGN_KEYS in capabilities:
        assert len(orders.foreign_keys) == 1
        fk = orders.foreign_keys[0]
        assert _lower(fk.columns) == {"user_id"}
        assert fk.foreign_table.lower() == "users"
        assert _lower(fk.foreign_columns) == {"id"}
        # many:1 (user_id is not unique) → not a 1:1 hint.
        assert fk.is_unique is False

    if VIEWS in capabilities:
        assert _has_table(schema, "active_users")
        assert _find_table(schema, "active_users").is_view is True

    if PROVENANCE_VERSION in capabilities:
        assert schema.source is not None
        assert schema.source.dialect == dialect
        assert schema.source.server_version
