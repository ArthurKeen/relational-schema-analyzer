"""dbt-manifest connector: manifest.json -> PhysicalSchema.

The first data-catalog source (DESIGN §9.3.1). dbt tests + contracts supply the
constraints/FKs the physical model prizes, so the connector reaches the full
conformance capability set (minus DEFAULTS, which dbt doesn't model) with no live
system. Fixture: tests/fixtures/dbt/manifest.json (shaped like the canonical shop).
"""

from __future__ import annotations

from pathlib import Path

from relational_schema_analyzer import create_connector
from relational_schema_analyzer.connectors.dbt_manifest import DbtManifestConnector, _ref_model_name
from tests import _conformance as conf

_MANIFEST = str(Path(__file__).resolve().parent / "fixtures" / "dbt" / "manifest.json")


def _schema():
    return create_connector("dbt", _MANIFEST).get_schema()


class TestRefParsing:
    def test_parses_ref_forms(self):
        assert _ref_model_name("ref('users')") == "users"
        assert _ref_model_name("ref('shop', 'users')") == "users"
        assert _ref_model_name("shop.public.users") == "users"
        assert _ref_model_name("users") == "users"
        assert _ref_model_name(None) is None


class TestIntrospection:
    def test_models_become_tables(self):
        schema = _schema()
        assert sorted(schema.tables) == ["active_users", "orders", "users"]

    def test_provenance(self):
        source = _schema().source
        assert source.dialect == "postgres"
        assert source.server_version == "1.8.0"
        assert source.database == "shop"

    def test_primary_key_from_contract(self):
        users = _schema().tables["users"]
        assert users.primary_key == ["id"]
        assert next(c for c in users.columns if c.name == "id").is_primary_key is True

    def test_not_null_and_unique_from_tests(self):
        users = _schema().tables["users"]
        email = next(c for c in users.columns if c.name == "email")
        assert email.is_nullable is False   # not_null test
        assert email.is_unique is True      # unique test
        assert email.comment == "contact email"
        assert users.comment == "people"

    def test_accepted_values_becomes_check_enum(self):
        users = _schema().tables["users"]
        checks = {c.columns[0]: c for c in users.check_constraints if c.columns}
        assert "status" in checks
        assert checks["status"].enum_values == ["active", "inactive"]

    def test_relationships_test_becomes_foreign_key(self):
        orders = _schema().tables["orders"]
        assert len(orders.foreign_keys) == 1
        fk = orders.foreign_keys[0]
        assert fk.columns == ["user_id"]
        assert fk.foreign_table == "users"
        assert fk.foreign_columns == ["id"]
        assert fk.is_unique is False  # user_id not unique -> many:1

    def test_view_materialization_flagged(self):
        assert _schema().tables["active_users"].is_view is True

    def test_type_categories(self):
        users = _schema().tables["users"]
        cats = {c.name: c.type_category for c in users.columns}
        assert cats["id"] == "integer"
        assert cats["email"] == "string"
        assert cats["created_at"] == "temporal"


def test_conformance_full_minus_defaults():
    caps = {
        conf.ORDINAL, conf.COMMENTS, conf.UNIQUE,
        conf.FOREIGN_KEYS, conf.VIEWS, conf.PROVENANCE_VERSION,
    }
    conf.assert_shop_conformance(_schema(), dialect="postgres", capabilities=caps)


class TestDirectoryResolution:
    def test_accepts_directory_with_target(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        (target / "manifest.json").write_text(Path(_MANIFEST).read_text(), encoding="utf-8")
        schema = DbtManifestConnector(str(tmp_path)).get_schema()
        assert "users" in schema.tables
