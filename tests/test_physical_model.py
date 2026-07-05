from __future__ import annotations

import pytest

from relational_schema_analyzer.baseline import infer_baseline
from relational_schema_analyzer.types import (
    Column,
    ForeignKey,
    PhysicalSchema,
    SourceProvenance,
    Table,
)

pytest.importorskip("polars")

from relational_schema_analyzer.connectors.csv_source import CsvConnector  # noqa: E402


class TestTypeCategory:
    @pytest.mark.parametrize(
        "sql_type, expected",
        [
            ("integer", "integer"),
            ("bigint", "integer"),
            ("numeric(10,2)", "decimal"),
            ("double precision", "decimal"),
            ("boolean", "boolean"),
            ("varchar(255)", "string"),
            ("text", "string"),
            ("timestamp with time zone", "temporal"),
            ("date", "temporal"),
            ("bytea", "binary"),
            ("uuid", "uuid"),
            ("jsonb", "json"),
            ("integer[]", "array"),
        ],
    )
    def test_normalized_category(self, sql_type, expected):
        col = Column(name="c", data_type=sql_type)
        assert col.type_category == expected

    def test_type_category_is_serialized(self):
        dumped = Column(name="c", data_type="integer").model_dump()
        assert dumped["type_category"] == "integer"


class TestExtraPassthrough:
    """The consumer-owned ``extra`` metadata passthrough (Column + Table)."""

    def test_column_extra_defaults_empty_and_omitted(self):
        col = Column(name="c", data_type="text")
        assert col.extra == {}
        assert "extra" not in col.model_dump()
        assert "extra" not in col.model_dump(mode="json")

    def test_table_extra_defaults_empty_and_omitted(self):
        tbl = Table(name="t", columns=[])
        assert tbl.extra == {}
        assert "extra" not in tbl.model_dump()

    def test_column_extra_round_trips(self):
        col = Column(
            name="email",
            data_type="text",
            extra={"classification": {"tags": ["PII.Sensitive"], "tier": "Tier.Tier1"}},
        )
        dumped = col.model_dump()
        assert dumped["extra"]["classification"]["tags"] == ["PII.Sensitive"]
        # type_category is still serialized alongside extra.
        assert dumped["type_category"] == "string"
        restored = Column.model_validate(dumped)
        assert restored.extra["classification"]["tier"] == "Tier.Tier1"

    def test_table_extra_round_trips_via_schema_file(self, tmp_path):
        schema = PhysicalSchema(
            tables={
                "users": Table(
                    name="users",
                    columns=[
                        Column(name="id", data_type="integer", is_primary_key=True),
                        Column(name="ssn", data_type="text", extra={"pii": True}),
                    ],
                    primary_key=["id"],
                    extra={"owner": "identity-team"},
                )
            }
        )
        path = str(tmp_path / "schema.json")
        schema.save_to_file(path)
        loaded = PhysicalSchema.load_from_file(path)
        assert loaded.tables["users"].extra == {"owner": "identity-team"}
        ssn = next(c for c in loaded.tables["users"].columns if c.name == "ssn")
        assert ssn.extra == {"pii": True}

    def test_empty_extra_does_not_change_fingerprint(self):
        # Backward compatibility: a schema that doesn't use `extra` fingerprints
        # identically to how it did before the field existed.
        from relational_schema_analyzer.metadata import fingerprint_physical_schema

        schema = PhysicalSchema(
            tables={
                "users": Table(
                    name="users",
                    columns=[Column(name="id", data_type="integer", is_primary_key=True)],
                    primary_key=["id"],
                )
            }
        )
        dumped = schema.model_dump_json()
        assert '"extra"' not in dumped
        # Deterministic + stable.
        assert fingerprint_physical_schema(schema) == fingerprint_physical_schema(schema)


class TestForeignKeyUniqueHint:
    def test_is_unique_round_trips(self):
        fk = ForeignKey(column="user_id", foreign_table="users", foreign_column="id",
                        is_unique=True)
        dumped = fk.model_dump()
        assert dumped["is_unique"] is True
        assert ForeignKey.model_validate(dumped).is_unique is True

    def test_is_unique_omitted_when_false(self):
        fk = ForeignKey(column="user_id", foreign_table="users", foreign_column="id")
        assert "is_unique" not in fk.model_dump()


class TestProvenanceRoundTrip:
    def test_source_provenance_survives_serialization(self):
        schema = PhysicalSchema(
            tables={},
            source=SourceProvenance(dialect="postgresql", server_version="16.1",
                                    database="shop", namespace="public"),
        )
        loaded = PhysicalSchema.model_validate_json(schema.model_dump_json())
        assert loaded.source.dialect == "postgresql"
        assert loaded.source.server_version == "16.1"


class TestCsvEnrichment:
    def test_provenance_and_ordinal_and_unique(self, tmp_path):
        d = tmp_path / "csvs"
        d.mkdir()
        (d / "authors.csv").write_text("id,name\n1,A\n2,B\n", encoding="utf-8")
        schema = CsvConnector(str(d)).get_schema()
        assert schema.source.dialect == "csv"
        cols = {c.name: c for c in schema.tables["authors"].columns}
        assert cols["id"].ordinal == 0
        assert cols["name"].ordinal == 1
        assert cols["id"].is_unique is True
        assert cols["name"].is_unique is False

    def test_enum_sampling_is_opt_in(self, tmp_path):
        d = tmp_path / "csvs"
        d.mkdir()
        (d / "items.csv").write_text(
            "id,status\n1,active\n2,inactive\n3,active\n4,inactive\n", encoding="utf-8"
        )
        # Off by default.
        off = CsvConnector(str(d)).get_schema()
        assert off.tables["items"].check_constraints == []
        # On → low-cardinality 'status' becomes an enum candidate.
        on = CsvConnector(str(d), sample_enums=True).get_schema()
        checks = on.tables["items"].check_constraints
        assert len(checks) == 1
        assert checks[0].columns == ["status"]
        assert checks[0].enum_values == ["active", "inactive"]


class TestBaselineUsesUniqueHints:
    def test_unique_fk_is_one_to_one(self):
        users = Table(name="users", columns=[Column(name="id", data_type="integer",
                      is_primary_key=True)], primary_key=["id"])
        # A surrogate-PK table whose FK column is itself unique → 1:1.
        accounts = Table(
            name="accounts",
            columns=[Column(name="id", data_type="integer", is_primary_key=True),
                     Column(name="user_id", data_type="integer", is_unique=True)],
            primary_key=["id"],
            foreign_keys=[ForeignKey(column="user_id", foreign_table="users",
                                     foreign_column="id", is_unique=True)],
        )
        result = infer_baseline(PhysicalSchema(tables={"users": users, "accounts": accounts}))
        rel = next(r for r in result["conceptualSchema"]["relationships"]
                   if r["type"] == "Accounts_Users")
        assert rel["cardinality"] == "1:1"

    def test_declared_unique_column_marked_key(self):
        users = Table(
            name="users",
            columns=[Column(name="id", data_type="integer", is_primary_key=True),
                     Column(name="email", data_type="text", is_unique=True)],
            primary_key=["id"],
        )
        result = infer_baseline(PhysicalSchema(tables={"users": users}))
        props = {p["name"]: p for p in result["conceptualSchema"]["entities"][0]["properties"]}
        assert props["email"]["unique"] is True
        assert props["email"]["indexed"] is True
