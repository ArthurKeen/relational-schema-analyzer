"""OSI (Open Semantic Interchange) connector: *.osi.yaml -> PhysicalSchema.

The second data-catalog source (DESIGN §9.3.1). OSI maps its structural constructs
(datasets/fields/primary_key/unique_keys/relationships) onto the physical model.
Unlike dbt, OSI carries no column SQL types, so types degrade to string/temporal —
these tests pin that documented limitation alongside the structural mapping.

Fixture: tests/fixtures/osi/shop.osi.yaml (canonical shop, OSI v0.2.0.dev0).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from relational_schema_analyzer import create_connector
from relational_schema_analyzer.connectors.osi import (
    OsiConnector,
    _load_yaml,
    _parse_source,
)

_MODEL = str(Path(__file__).resolve().parent / "fixtures" / "osi" / "shop.osi.yaml")


def _schema():
    return create_connector("osi", _MODEL).get_schema()


class TestSourceParsing:
    def test_parses_source_forms(self):
        assert _parse_source("shop.public.users") == ("shop", "public")
        assert _parse_source("public.users") == (None, "public")
        assert _parse_source("users") == (None, None)
        assert _parse_source(None) == (None, None)


class TestIntrospection:
    def test_datasets_become_tables(self):
        assert sorted(_schema().tables) == ["orders", "users"]

    def test_provenance(self):
        source = _schema().source
        assert source.dialect == "osi"
        assert source.server_version == "0.2.0.dev0"
        assert source.database == "shop"  # from "shop.public.users"

    def test_primary_key_and_uniqueness(self):
        users = _schema().tables["users"]
        assert users.primary_key == ["id"]
        idc = next(c for c in users.columns if c.name == "id")
        assert idc.is_primary_key is True
        assert idc.is_nullable is False
        assert idc.is_unique is True  # single-column PK

    def test_unique_keys_distinct_from_pk(self):
        users = _schema().tables["users"]
        assert users.unique_constraints == [["email"]]
        email = next(c for c in users.columns if c.name == "email")
        assert email.is_unique is True
        assert email.is_nullable is True  # not part of the PK

    def test_descriptions_map_to_comments(self):
        users = _schema().tables["users"]
        assert users.comment == "people"
        email = next(c for c in users.columns if c.name == "email")
        assert email.comment == "contact email"

    def test_source_captured(self):
        users = _schema().tables["users"]
        assert users.schema_name == "public"
        assert users.extra["osiSource"] == "shop.public.users"

    def test_field_ordinals_follow_declaration(self):
        users = _schema().tables["users"]
        ordinals = {c.name: c.ordinal for c in users.columns}
        assert ordinals == {"id": 0, "email": 1, "status": 2, "created_at": 3}

    def test_relationship_becomes_foreign_key(self):
        orders = _schema().tables["orders"]
        assert len(orders.foreign_keys) == 1
        fk = orders.foreign_keys[0]
        assert fk.columns == ["user_id"]
        assert fk.foreign_table == "users"
        assert fk.foreign_columns == ["id"]
        assert fk.constraint_name == "orders_to_users"
        assert fk.is_unique is False  # user_id not unique -> many:1

    def test_metrics_are_ignored(self):
        # Model-level metrics are aggregate expressions, not physical columns.
        orders = _schema().tables["orders"]
        assert {c.name for c in orders.columns} == {"id", "user_id", "total"}


class TestTypeLimitation:
    """OSI declares no SQL types; is_time -> temporal, everything else -> string."""

    def test_is_time_maps_to_temporal(self):
        users = _schema().tables["users"]
        created = next(c for c in users.columns if c.name == "created_at")
        assert created.type_category == "temporal"

    def test_typeless_fields_degrade_to_string(self):
        users = _schema().tables["users"]
        cats = {c.name: c.type_category for c in users.columns}
        assert cats["id"] == "string"      # no type in OSI (would be integer live)
        assert cats["email"] == "string"
        assert cats["status"] == "string"


class TestResolutionAndDeps:
    def test_accepts_directory(self, tmp_path):
        (tmp_path / "shop.osi.yaml").write_text(
            Path(_MODEL).read_text(encoding="utf-8"), encoding="utf-8"
        )
        schema = OsiConnector(str(tmp_path)).get_schema()
        assert "users" in schema.tables

    def test_missing_pyyaml_raises_helpful_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yaml", None)  # force ImportError
        with pytest.raises(ImportError, match=r"\[osi\]"):
            _load_yaml(Path(_MODEL))
