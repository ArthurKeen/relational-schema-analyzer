from __future__ import annotations

import pytest
from pydantic import ValidationError

from relational_schema_analyzer.types import (
    Column,
    ForeignKey,
    PhysicalSchema,
    Schema,
    Table,
)


class TestSchemaAlias:
    def test_schema_is_physical_schema(self):
        assert Schema is PhysicalSchema


class TestSchemaSerializationRoundTrip:
    def test_save_and_load_preserves_tables(self, sample_schema, tmp_path):
        path = str(tmp_path / "schema.json")
        sample_schema.save_to_file(path)
        loaded = Schema.load_from_file(path)

        assert set(loaded.tables.keys()) == {"users", "orders"}
        assert loaded.tables["users"].primary_key == ["id"]
        assert len(loaded.tables["users"].columns) == 3
        assert loaded.tables["orders"].foreign_keys[0].foreign_table == "users"

    def test_round_trip_column_attributes(self, sample_schema, tmp_path):
        path = str(tmp_path / "schema.json")
        sample_schema.save_to_file(path)
        loaded = Schema.load_from_file(path)

        email_col = next(c for c in loaded.tables["users"].columns if c.name == "email")
        assert email_col.is_nullable is True
        assert email_col.data_type == "text"

        id_col = next(c for c in loaded.tables["users"].columns if c.name == "id")
        assert id_col.is_primary_key is True
        assert id_col.is_nullable is False


class TestPydanticValidation:
    def test_column_missing_name_raises(self):
        with pytest.raises(ValidationError):
            Column(data_type="text")  # type: ignore[call-arg]

    def test_column_missing_data_type_raises(self):
        with pytest.raises(ValidationError):
            Column(name="x")  # type: ignore[call-arg]

    def test_table_missing_name_raises(self):
        with pytest.raises(ValidationError):
            Table(columns=[])  # type: ignore[call-arg]

    def test_table_missing_columns_raises(self):
        with pytest.raises(ValidationError):
            Table(name="t")  # type: ignore[call-arg]

    def test_foreign_key_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            ForeignKey(column="x")  # type: ignore[call-arg]


class TestDefaultValues:
    def test_column_defaults(self):
        col = Column(name="x", data_type="text")
        assert col.is_nullable is False
        assert col.is_primary_key is False

    def test_table_defaults(self):
        tbl = Table(name="t", columns=[])
        assert tbl.primary_key == []
        assert tbl.foreign_keys == []
        assert tbl.is_partitioned is False
        assert tbl.partition_of is None

    def test_foreign_key_constraint_name_default(self):
        fk = ForeignKey(column="c", foreign_table="t", foreign_column="id")
        assert fk.constraint_name is None

    def test_schema_empty_default(self):
        s = Schema()
        assert s.tables == {}


class TestForeignKeyComposite:
    def test_singular_form_accepted(self):
        fk = ForeignKey(column="user_id", foreign_table="users", foreign_column="id")
        assert fk.columns == ["user_id"]
        assert fk.foreign_columns == ["id"]

    def test_plural_form_accepted(self):
        fk = ForeignKey(columns=["a", "b"], foreign_table="t", foreign_columns=["x", "y"])
        assert fk.columns == ["a", "b"]
        assert fk.foreign_columns == ["x", "y"]

    def test_backward_compat_properties(self):
        fk = ForeignKey(column="c", foreign_table="t", foreign_column="id")
        assert fk.column == "c"
        assert fk.foreign_column == "id"

    def test_is_composite_false_for_single(self):
        fk = ForeignKey(column="c", foreign_table="t", foreign_column="id")
        assert fk.is_composite is False

    def test_is_composite_true_for_multi(self):
        fk = ForeignKey(columns=["a", "b"], foreign_table="t", foreign_columns=["x", "y"])
        assert fk.is_composite is True

    def test_serialization_singular(self):
        fk = ForeignKey(column="c", foreign_table="t", foreign_column="id")
        d = fk.model_dump()
        assert "column" in d
        assert "columns" not in d
        assert d["column"] == "c"
        assert d["foreign_column"] == "id"

    def test_serialization_composite(self):
        fk = ForeignKey(columns=["a", "b"], foreign_table="t", foreign_columns=["x", "y"])
        d = fk.model_dump()
        assert "columns" in d
        assert "column" not in d
        assert d["columns"] == ["a", "b"]
        assert d["foreign_columns"] == ["x", "y"]

    def test_round_trip_via_schema_file(self, tmp_path):
        schema = Schema(tables={
            "shipments": Table(
                name="shipments",
                columns=[Column(name="id", data_type="integer", is_primary_key=True)],
                primary_key=["id"],
                foreign_keys=[
                    ForeignKey(
                        columns=["order_id", "product_id"],
                        foreign_table="order_items",
                        foreign_columns=["order_id", "product_id"],
                        constraint_name="fk_ship",
                    ),
                ],
            ),
        })
        path = str(tmp_path / "schema.json")
        schema.save_to_file(path)
        loaded = Schema.load_from_file(path)
        fk = loaded.tables["shipments"].foreign_keys[0]
        assert fk.columns == ["order_id", "product_id"]
        assert fk.foreign_columns == ["order_id", "product_id"]
        assert fk.is_composite is True
