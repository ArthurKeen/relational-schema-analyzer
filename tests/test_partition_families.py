"""A partition and its parent are the same table, not a relationship (#27).

PostgreSQL declarative partitioning gives each partition its own name, its own
columns and its own copy of the parent's key. To name-based inference that looks
exactly like a child table carrying its parent's key — which is the strongest
signal it has — so it proposes a reference from parent to partition, from partition
back to parent, and between every pair of siblings.

Found by the corpus harness on its first run: Pagila's ``payment`` has seven
partitions, and **56 of the 60 wrong proposals across the whole corpus came from
that one table**. Measured over Chinook, Northwind and Pagila, excluding them took
precision from 0.492 to 0.935 with recall unchanged.

The rule has to be narrow. A partition's references to *other* tables are genuine —
PostgreSQL records them on the child, which is why the connector rolls them up to
the parent. Excluding partition children outright destroys those: measured, it cut
recall on Pagila from 0.974 to 0.513. Only references *within* a family are
meaningless.
"""

from __future__ import annotations

from relational_schema_analyzer.fk_inference import InferenceOptions, infer_foreign_keys
from relational_schema_analyzer.types import Column, PhysicalSchema, Table


def _col(name: str, data_type: str = "integer", nullable: bool = True) -> Column:
    return Column(name=name, data_type=data_type, is_nullable=nullable)


def _partitioned_schema() -> PhysicalSchema:
    """A partitioned parent, two partitions, and an unrelated table they reference."""
    cols = [
        _col("payment_id", nullable=False),
        _col("payment_date", "timestamp", nullable=False),
        _col("customer_id"),
    ]
    return PhysicalSchema(
        tables={
            "customer": Table(
                name="customer",
                columns=[_col("customer_id", nullable=False), _col("name", "text")],
                primary_key=["customer_id"],
            ),
            "payment": Table(
                name="payment",
                columns=list(cols),
                primary_key=["payment_date", "payment_id"],
                is_partitioned=True,
            ),
            "payment_p01": Table(
                name="payment_p01",
                columns=list(cols),
                primary_key=["payment_date", "payment_id"],
                partition_of="payment",
            ),
            "payment_p02": Table(
                name="payment_p02",
                columns=list(cols),
                primary_key=["payment_date", "payment_id"],
                partition_of="payment",
            ),
        }
    )


def _refs(schema: PhysicalSchema) -> set[tuple[str, str]]:
    return {
        (c.table, c.foreign_table)
        for c in infer_foreign_keys(schema, options=InferenceOptions())
    }


class TestAPartitionIsNotARelationship:
    def test_a_parent_does_not_reference_its_own_partition(self):
        assert not {r for r in _refs(_partitioned_schema()) if r[0] == "payment"} & {
            ("payment", "payment_p01"),
            ("payment", "payment_p02"),
        }

    def test_a_partition_does_not_reference_its_parent(self):
        refs = _refs(_partitioned_schema())
        assert ("payment_p01", "payment") not in refs
        assert ("payment_p02", "payment") not in refs

    def test_siblings_do_not_reference_each_other(self):
        refs = _refs(_partitioned_schema())
        assert ("payment_p01", "payment_p02") not in refs
        assert ("payment_p02", "payment_p01") not in refs

    def test_no_reference_anywhere_inside_the_family(self):
        family = {"payment", "payment_p01", "payment_p02"}
        inside = {r for r in _refs(_partitioned_schema()) if r[0] in family and r[1] in family}
        assert not inside, f"a partition family referenced itself: {sorted(inside)}"


class TestRealReferencesSurvive:
    def test_a_partition_still_references_other_tables(self):
        """The narrowness of the rule, asserted.

        Excluding partition children outright would also drop this, and it is a real
        declared key — which is what cost 46 points of recall when I tried it.
        """
        refs = _refs(_partitioned_schema())
        assert ("payment_p01", "customer") in refs
        assert ("payment_p02", "customer") in refs

    def test_the_parent_still_references_other_tables(self):
        assert ("payment", "customer") in _refs(_partitioned_schema())

    def test_an_unpartitioned_schema_is_unaffected(self):
        """Two tables that merely share a key name must still be linked."""
        schema = PhysicalSchema(
            tables={
                "customer": Table(
                    name="customer",
                    columns=[_col("customer_id", nullable=False)],
                    primary_key=["customer_id"],
                ),
                "orders": Table(
                    name="orders",
                    columns=[_col("order_id", nullable=False), _col("customer_id")],
                    primary_key=["order_id"],
                ),
            }
        )
        assert ("orders", "customer") in _refs(schema)
