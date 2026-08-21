from __future__ import annotations

import pytest

from relational_schema_analyzer.fk_inference import (
    CsvValueSampler,
    DatabricksValueSampler,
    InferenceOptions,
    InferredForeignKey,
    MySQLValueSampler,
    PostgresValueSampler,
    SQLServerValueSampler,
    create_value_sampler,
    infer_foreign_keys,
)
from relational_schema_analyzer.types import Column, ForeignKey, Schema, Table

# ── Helpers ─────────────────────────────────────────────────────────


def _tbl(name: str, cols: list[tuple[str, str, bool, bool]], pk: list[str] | None = None,
         fks: list[ForeignKey] | None = None) -> Table:
    """`cols` entries: (name, data_type, is_nullable, is_primary_key)."""
    return Table(
        name=name,
        columns=[
            Column(name=n, data_type=t, is_nullable=nul, is_primary_key=pkf)
            for (n, t, nul, pkf) in cols
        ],
        primary_key=pk or [],
        foreign_keys=fks or [],
    )


def _schema(*tables: Table) -> Schema:
    return Schema(tables={t.name: t for t in tables})


# ── Name-based inference ────────────────────────────────────────────


class TestNameHeuristic:
    def test_single_column_user_id_points_to_users_id(self):
        s = _schema(
            _tbl("users", [("id", "integer", False, True), ("name", "text", True, False)], pk=["id"]),
            _tbl("orders", [("id", "integer", False, True), ("user_id", "integer", False, False)], pk=["id"]),
        )
        out = infer_foreign_keys(s)
        assert len(out) == 1
        c = out[0]
        assert c.table == "orders"
        assert c.columns == ["user_id"]
        assert c.foreign_table == "users"
        assert c.foreign_columns == ["id"]
        assert c.method == "name_suffix"
        assert c.confidence >= 0.75

    def test_plural_and_singular_table_names_both_match(self):
        s_plural = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl("orders", [("id", "integer", False, True), ("user_id", "integer", False, False)], pk=["id"]),
        )
        s_singular = _schema(
            _tbl("user", [("id", "integer", False, True)], pk=["id"]),
            _tbl("orders", [("id", "integer", False, True), ("user_id", "integer", False, False)], pk=["id"]),
        )
        out1 = infer_foreign_keys(s_plural)
        out2 = infer_foreign_keys(s_singular)
        assert any(c.foreign_table == "users" for c in out1)
        assert any(c.foreign_table == "user" for c in out2)

    def test_declared_fk_suppresses_suggestion_for_that_column(self):
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("user_id", "integer", False, False)],
                pk=["id"],
                fks=[ForeignKey(columns=["user_id"], foreign_table="users", foreign_columns=["id"])],
            ),
        )
        out = infer_foreign_keys(s)
        assert out == []

    def test_type_incompatibility_rejects_candidate(self):
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "flags",
                [("id", "integer", False, True), ("user_id", "boolean", False, False)],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s)
        assert out == []

    def test_integer_and_float_are_considered_compatible(self):
        # Snowflake NUMBER(38,0) ends up as "number" → float in the map,
        # but joining against integer PKs is a very real workflow.
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("user_id", "number", False, False)],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s)
        assert any(c.columns == ["user_id"] for c in out)

    def test_bare_id_column_does_not_match_random_pks(self):
        # Direct pk_name_match should skip generic names like `id`.
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl("sessions", [("id", "integer", False, True)], pk=["id"]),
        )
        out = infer_foreign_keys(s)
        assert out == []

    def test_non_generic_pk_name_triggers_direct_match(self):
        s = _schema(
            _tbl("products", [("sku", "text", False, True), ("name", "text", True, False)], pk=["sku"]),
            _tbl(
                "line_items",
                [("id", "integer", False, True), ("sku", "text", False, False)],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s)
        match = [c for c in out if c.foreign_table == "products" and c.columns == ["sku"]]
        assert len(match) == 1
        assert match[0].method in ("pk_name_match", "name_suffix")

    def test_no_underscore_id_gets_lower_confidence(self):
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("userid", "integer", False, False)],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s, options=InferenceOptions(min_confidence=0.3))
        # The "userid" → "users.id" suggestion should exist but be scored
        # below the clean "_id" form.
        assert any(c.columns == ["userid"] for c in out)
        conf = next(c for c in out if c.columns == ["userid"]).confidence
        assert conf < 0.6

    def test_min_confidence_filters_weak_candidates(self):
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("userid", "integer", False, False)],
                pk=["id"],
            ),
        )
        strict = infer_foreign_keys(s, options=InferenceOptions(min_confidence=0.9))
        assert strict == []

    def test_nullable_target_with_non_nullable_source_is_penalized(self):
        s = _schema(
            _tbl("users", [("id", "integer", True, True)], pk=["id"]),  # nullable PK (weird!)
            _tbl(
                "orders",
                [("id", "integer", False, True), ("user_id", "integer", False, False)],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s)
        conf = next(c for c in out if c.columns == ["user_id"]).confidence
        assert conf < 0.8

    def test_results_are_sorted_by_confidence_desc(self):
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl("customers", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [
                    ("id", "integer", False, True),
                    ("user_id", "integer", False, False),
                    ("customerid", "integer", False, False),
                ],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s, options=InferenceOptions(min_confidence=0.3))
        confs = [c.confidence for c in out]
        assert confs == sorted(confs, reverse=True)


# ── Candidate-key targets (surrogate PK + natural key) ─────────────


def _keyed_tbl(
    name: str,
    cols: list[tuple[str, str, bool, bool]],
    pk: list[str] | None = None,
    uniques: list[list[str]] | None = None,
    *,
    flag_unique: bool = True,
) -> Table:
    """`_tbl` plus UNIQUE constraints.

    ``flag_unique`` mirrors what the enriched connectors do — set both the declared
    constraint and the per-column flag — and can be turned off to prove each channel
    works on its own.
    """
    t = _tbl(name, cols, pk)
    t.unique_constraints = [list(u) for u in (uniques or [])]
    if flag_unique:
        single = {u[0] for u in t.unique_constraints if len(u) == 1}
        for c in t.columns:
            if c.name in single:
                c.is_unique = True
    return t


class TestCandidateKeyTargets:
    """A foreign key references a *candidate key*, not specifically the primary key.

    Warehouse-landed schemas routinely carry a surrogate integer PK beside the natural
    business key everything actually joins on. Targeting only the PK made the real
    referent invisible and the engine returned nothing on exactly the schemas it exists
    to serve.
    """

    def _crm(self) -> Schema:
        # accounts: surrogate bigint PK `id`, natural text key `account_id` UNIQUE.
        return _schema(
            _keyed_tbl(
                "accounts",
                [("id", "bigint", False, True), ("account_id", "text", False, False)],
                ["id"],
                [["account_id"]],
            ),
            _keyed_tbl(
                "contracts",
                [
                    ("id", "bigint", False, True),
                    ("contract_id", "text", False, False),
                    ("account_id", "text", False, False),
                ],
                ["id"],
                [["contract_id"]],
            ),
            _tbl(
                "opportunities",
                [
                    ("id", "bigint", False, True),
                    ("account_id", "text", False, False),
                    ("contract_id", "text", False, False),
                ],
                ["id"],
            ),
        )

    def test_unique_natural_key_is_a_valid_target(self):
        out = infer_foreign_keys(self._crm())
        found = {(c.table, c.columns[0], c.foreign_table, c.foreign_columns[0]) for c in out}
        assert ("contracts", "account_id", "accounts", "account_id") in found
        assert ("opportunities", "account_id", "accounts", "account_id") in found
        assert ("opportunities", "contract_id", "contracts", "contract_id") in found

    def test_surrogate_pk_is_not_proposed_for_a_mismatched_type(self):
        """The type check still does its job — `text -> bigint` is not a foreign key."""
        out = infer_foreign_keys(self._crm())
        assert not [c for c in out if c.foreign_columns == ["id"]]

    def test_primary_key_target_outranks_a_unique_target(self):
        """Ranking, not replacement: nothing regresses where the PK *is* the referent."""
        surrogate = infer_foreign_keys(self._crm())
        natural = self._crm()
        natural.tables["accounts"].primary_key = ["account_id"]
        natural.tables["contracts"].primary_key = ["contract_id"]

        def conf(out):
            return next(
                c.confidence for c in out
                if (c.table, c.foreign_table) == ("contracts", "accounts")
            )

        assert conf(infer_foreign_keys(natural)) > conf(surrogate)

    def test_evidence_names_the_unique_target(self):
        out = infer_foreign_keys(self._crm())
        cand = next(c for c in out if (c.table, c.foreign_table) == ("contracts", "accounts"))
        assert any("UNIQUE" in e for e in cand.evidence)

    def test_column_is_unique_flag_alone_is_enough(self):
        """Sources that populate the flag but not a constraint list still work."""
        s = self._crm()
        for t in s.tables.values():
            t.unique_constraints = []
        assert any(c.foreign_columns == ["account_id"] for c in infer_foreign_keys(s))

    def test_declared_unique_constraint_alone_is_enough(self):
        """...and vice versa."""
        s = _schema(
            _keyed_tbl(
                "accounts",
                [("id", "bigint", False, True), ("account_id", "text", False, False)],
                ["id"],
                [["account_id"]],
                flag_unique=False,
            ),
            _tbl(
                "contracts",
                [("id", "bigint", False, True), ("account_id", "text", False, False)],
                ["id"],
            ),
        )
        assert any(c.foreign_columns == ["account_id"] for c in infer_foreign_keys(s))

    def test_non_unique_column_is_never_a_target(self):
        """Uniqueness supplies direction; containment alone cannot.

        Without this, a schema where every table carries `account_id` proposes a
        relationship between every pair in both directions — the low-cardinality
        containment trap. Only the side that is unique may be the referent.
        """
        s = _schema(
            _tbl("contracts", [("id", "bigint", False, True),
                               ("account_id", "text", False, False)], ["id"]),
            _tbl("opportunities", [("id", "bigint", False, True),
                                   ("account_id", "text", False, False)], ["id"]),
        )
        # Neither `account_id` is unique, and there is no `accounts` table to target.
        out = infer_foreign_keys(s)
        assert not [c for c in out if c.foreign_columns == ["account_id"]]

    def test_composite_unique_is_not_a_single_column_target(self):
        s = _schema(
            _keyed_tbl(
                "accounts",
                [
                    ("id", "bigint", False, True),
                    ("tenant", "text", False, False),
                    ("account_id", "text", False, False),
                ],
                ["id"],
                [["tenant", "account_id"]],
            ),
            _tbl("contracts", [("id", "bigint", False, True),
                               ("account_id", "text", False, False)], ["id"]),
        )
        assert not [c for c in infer_foreign_keys(s) if c.foreign_columns == ["account_id"]]


# ── Composite inference ────────────────────────────────────────────


class TestCompositeInference:
    def test_composite_pk_gets_grouped_suggestion(self):
        s = _schema(
            _tbl(
                "order_products",
                [("order_id", "integer", False, True), ("product_id", "integer", False, True)],
                pk=["order_id", "product_id"],
            ),
            _tbl(
                "order_lines",
                [
                    ("id", "integer", False, True),
                    ("order_id", "integer", False, False),
                    ("product_id", "integer", False, False),
                ],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(s)
        composite = [c for c in out if c.method == "composite"]
        assert len(composite) == 1
        c = composite[0]
        assert c.table == "order_lines"
        assert c.foreign_table == "order_products"
        assert c.columns == ["order_id", "product_id"]
        assert c.foreign_columns == ["order_id", "product_id"]

    def test_composite_disabled_returns_only_singles(self):
        s = _schema(
            _tbl(
                "order_products",
                [("order_id", "integer", False, True), ("product_id", "integer", False, True)],
                pk=["order_id", "product_id"],
            ),
            _tbl(
                "order_lines",
                [
                    ("id", "integer", False, True),
                    ("order_id", "integer", False, False),
                    ("product_id", "integer", False, False),
                ],
                pk=["id"],
            ),
        )
        out = infer_foreign_keys(
            s,
            options=InferenceOptions(allow_composite=False, min_confidence=0.0),
        )
        assert all(c.method != "composite" for c in out)


# ── Sampler integration ────────────────────────────────────────────


class TestSamplerIntegration:
    def _schema(self) -> Schema:
        return _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("user_id", "integer", False, False)],
                pk=["id"],
            ),
        )

    def test_high_overlap_boosts_confidence(self):
        s = self._schema()

        def sampler(lt, lc, ft, fc):
            return 1.0

        out_no = infer_foreign_keys(s)
        out_yes = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True),
            sampler=sampler,
        )
        assert out_yes[0].confidence > out_no[0].confidence

    def test_zero_overlap_vetoes_when_enabled(self):
        s = self._schema()
        out = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True, overlap_veto_on_zero=True),
            sampler=lambda *args: 0.0,
        )
        assert out == []

    def test_zero_overlap_without_veto_still_lowers_score(self):
        s = self._schema()
        out = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True, overlap_veto_on_zero=False),
            sampler=lambda *args: 0.0,
        )
        assert len(out) == 1
        # 0.85 base minus 0.25 penalty, rounded = 0.6 — still above the default floor.
        assert out[0].confidence < 0.85

    def test_sampler_exception_keeps_candidate(self):
        s = self._schema()

        def angry(*args):
            raise RuntimeError("boom")

        out = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True),
            sampler=angry,
        )
        assert len(out) == 1

    def test_sampler_none_is_neutral(self):
        s = self._schema()
        out_no = infer_foreign_keys(s)
        out_neutral = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True),
            sampler=lambda *args: None,
        )
        assert out_neutral[0].confidence == out_no[0].confidence


# ── to_foreign_key ─────────────────────────────────────────────────


class TestForeignKeyConversion:
    def test_single_column_foreign_key_round_trip(self):
        c = InferredForeignKey(
            table="orders",
            columns=["user_id"],
            foreign_table="users",
            foreign_columns=["id"],
            confidence=0.85,
            method="name_suffix",
        )
        fk = c.to_foreign_key()
        assert isinstance(fk, ForeignKey)
        assert fk.columns == ["user_id"]
        assert fk.foreign_table == "users"
        assert fk.foreign_columns == ["id"]
        assert fk.is_composite is False
        assert fk.constraint_name is None

    def test_composite_foreign_key_keeps_column_order(self):
        c = InferredForeignKey(
            table="order_lines",
            columns=["order_id", "product_id"],
            foreign_table="order_products",
            foreign_columns=["order_id", "product_id"],
            confidence=0.82,
            method="composite",
        )
        fk = c.to_foreign_key(constraint_name="lines_to_order_products")
        assert fk.constraint_name == "lines_to_order_products"
        assert fk.columns == ["order_id", "product_id"]
        assert fk.foreign_columns == ["order_id", "product_id"]
        assert fk.is_composite is True


# ── PostgresValueSampler plumbing ──────────────────────────────────


class TestPostgresValueSampler:
    def test_query_failure_is_swallowed_and_returns_none(self, monkeypatch):
        sampler = PostgresValueSampler("postgresql://bogus@localhost/none")

        class _FakeCur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def execute(self, *a, **kw):
                raise RuntimeError("boom")

            def fetchone(self):
                return None

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

            def rollback(self):
                self.rolled_back = True

            def close(self):
                pass

        sampler._conn = _FakeConn()
        res = sampler("orders", "user_id", "users", "id")
        assert res is None
        assert sampler._conn.rolled_back is True

    def test_empty_result_returns_none(self):
        sampler = PostgresValueSampler("postgresql://bogus/none")

        class _FakeCur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def execute(self, *a, **kw):
                pass

            def fetchone(self):
                return None

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

            def close(self):
                pass

        sampler._conn = _FakeConn()
        assert sampler("a", "b", "c", "d") is None

    def test_successful_result_is_coerced_to_float(self):
        sampler = PostgresValueSampler("postgresql://bogus/none")

        class _FakeCur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def execute(self, *a, **kw):
                pass

            def fetchone(self):
                return (0.75,)

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

            def close(self):
                pass

        sampler._conn = _FakeConn()
        assert sampler("a", "b", "c", "d") == 0.75

    def test_limit_is_clamped_to_minimum(self):
        sampler = PostgresValueSampler("postgresql://x/y", limit=10)
        assert sampler.limit >= 100


# ── Parametric coverage ────────────────────────────────────────────


@pytest.mark.parametrize(
    "col_name,expected_prefix",
    [
        ("user_id", "user"),
        ("customer_id", "customer"),
        ("customerid", "customer"),
        ("uuid", None),  # generic UUID column should not auto-match
    ],
)
def test_prefix_extraction_produces_expected_tables(col_name, expected_prefix):
    parent = _tbl(
        expected_prefix or "unrelated",
        [("id", "integer", False, True)],
        pk=["id"],
    ) if expected_prefix else _tbl("foo", [("id", "integer", False, True)], pk=["id"])
    s = _schema(
        parent,
        _tbl(
            "child",
            [("id", "integer", False, True), (col_name, "integer", False, False)],
            pk=["id"],
        ),
    )
    out = infer_foreign_keys(s, options=InferenceOptions(min_confidence=0.3))
    if expected_prefix:
        assert any(c.foreign_table == expected_prefix for c in out)
    else:
        assert all(c.columns != [col_name] for c in out)


# ── CsvValueSampler ─────────────────────────────────────────────────


class TestCsvValueSampler:
    def _make_dir(self, tmp_path, files: dict[str, str]):
        for name, content in files.items():
            (tmp_path / name).write_text(content)
        return CsvValueSampler(str(tmp_path), limit=1000)

    def test_full_overlap_returns_one(self, tmp_path):
        sampler = self._make_dir(
            tmp_path,
            {
                "users.csv": "id,name\n1,a\n2,b\n3,c\n",
                "orders.csv": "id,user_id\n10,1\n11,2\n12,3\n",
            },
        )
        # Every distinct orders.user_id (1,2,3) exists in users.id.
        assert sampler("orders", "user_id", "users", "id") == 1.0

    def test_partial_overlap_is_fraction(self, tmp_path):
        sampler = self._make_dir(
            tmp_path,
            {
                "users.csv": "id\n1\n2\n",
                "orders.csv": "id,user_id\n10,1\n11,2\n12,99\n",
            },
        )
        # Distinct local values {1,2,99}; {1,2} present in foreign → 2/3.
        assert sampler("orders", "user_id", "users", "id") == pytest.approx(2 / 3)

    def test_zero_overlap_returns_zero(self, tmp_path):
        sampler = self._make_dir(
            tmp_path,
            {
                "users.csv": "id\n1\n2\n3\n",
                "orders.csv": "id,user_id\n10,7\n11,8\n",
            },
        )
        assert sampler("orders", "user_id", "users", "id") == 0.0

    def test_int_vs_float_tokens_compared_as_text(self, tmp_path):
        # Raw text comparison: "1" matches "1", "2.5" matches "2.5".
        sampler = self._make_dir(
            tmp_path,
            {
                "parent.csv": "key\n1\n2.5\n",
                "child.csv": "id,key\n10,1\n11,2.5\n",
            },
        )
        assert sampler("child", "key", "parent", "key") == 1.0

    def test_missing_file_returns_none(self, tmp_path):
        sampler = self._make_dir(tmp_path, {"users.csv": "id\n1\n"})
        assert sampler("orders", "user_id", "users", "id") is None
        assert sampler("users", "id", "ghost", "id") is None

    def test_empty_local_column_returns_none(self, tmp_path):
        sampler = self._make_dir(
            tmp_path,
            {
                "users.csv": "id\n1\n",
                "orders.csv": "id,user_id\n",  # header only, no rows
            },
        )
        assert sampler("orders", "user_id", "users", "id") is None

    def test_limit_is_clamped_to_minimum(self, tmp_path):
        sampler = CsvValueSampler(str(tmp_path), limit=10)
        assert sampler.limit >= 100

    def test_integration_vetoes_bad_candidate(self, tmp_path):
        (tmp_path / "users.csv").write_text("id\n1\n2\n3\n")
        (tmp_path / "orders.csv").write_text("id,user_id\n10,7\n11,8\n12,9\n")
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("user_id", "integer", False, False)],
                pk=["id"],
            ),
        )
        sampler = CsvValueSampler(str(tmp_path), limit=1000)
        out = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True, overlap_veto_on_zero=True),
            sampler=sampler,
        )
        # orders.user_id values never appear in users.id → vetoed.
        assert out == []

    def test_integration_boosts_good_candidate(self, tmp_path):
        (tmp_path / "users.csv").write_text("id\n1\n2\n3\n")
        (tmp_path / "orders.csv").write_text("id,user_id\n10,1\n11,2\n12,3\n")
        s = _schema(
            _tbl("users", [("id", "integer", False, True)], pk=["id"]),
            _tbl(
                "orders",
                [("id", "integer", False, True), ("user_id", "integer", False, False)],
                pk=["id"],
            ),
        )
        sampler = CsvValueSampler(str(tmp_path), limit=1000)
        out_no = infer_foreign_keys(s)
        out_yes = infer_foreign_keys(
            s,
            options=InferenceOptions(sample_overlap=True),
            sampler=sampler,
        )
        assert out_yes[0].confidence > out_no[0].confidence


# ── MySQLValueSampler + create_value_sampler dispatch ────────────────


class TestMySQLValueSampler:
    def test_database_taken_from_url(self):
        s = MySQLValueSampler("mysql://u:p@h/shop")
        assert s.schema_name == "shop"

    def test_explicit_schema_overrides_url_database(self):
        s = MySQLValueSampler("mysql://u:p@h/shop", schema_name="analytics")
        assert s.schema_name == "analytics"
        assert s._connect_params["database"] == "analytics"

    def test_public_sentinel_falls_back_to_url_database(self):
        # `infer-fks` forwards snap.pg_schema, which defaults to "public".
        s = MySQLValueSampler("mysql://u:p@h/shop", schema_name="public")
        assert s.schema_name == "shop"

    def test_connect_failure_returns_none(self):
        # Bogus host: the sampler swallows the driver error and yields None
        # so name-based scoring still wins. pymysql may be absent; either the
        # ImportError or a connection error is caught.
        s = MySQLValueSampler("mysql://nobody@127.0.0.1:1/none")
        assert s("orders", "user_id", "users", "id") is None
        s.close()


class TestSQLServerValueSampler:
    def test_schema_defaults_to_dbo(self):
        assert SQLServerValueSampler("mssql://u:p@h/shop").schema_name == "dbo"

    def test_public_sentinel_folds_to_dbo(self):
        assert SQLServerValueSampler("mssql://u:p@h/shop", schema_name="public").schema_name == "dbo"

    def test_explicit_schema_kept(self):
        assert SQLServerValueSampler("mssql://u:p@h/shop", schema_name="sales").schema_name == "sales"

    def test_connect_failure_returns_none(self):
        s = SQLServerValueSampler("mssql://nobody@127.0.0.1:1/none")
        assert s("orders", "user_id", "users", "id") is None
        s.close()


class TestDatabricksValueSampler:
    """Value overlap is the only FK evidence Unity Catalog can't fake — it never
    enforces constraints, so these queries are the verification step."""

    DSN = "databricks://:tok@host/sql/1.0/warehouses/x?catalog=main&schema=sales"

    @staticmethod
    def _fake_driver(monkeypatch, rows, captured):
        import sys
        import types

        class _Cur:
            def execute(self, sql, params=None):
                captured.append((" ".join(sql.split()), params))

            def fetchone(self):
                return rows

            def close(self):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                pass

        sql_mod = types.ModuleType("databricks.sql")
        sql_mod.connect = lambda **k: _Conn()  # type: ignore[attr-defined]
        dbx = types.ModuleType("databricks")
        dbx.sql = sql_mod  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "databricks", dbx)
        monkeypatch.setitem(sys.modules, "databricks.sql", sql_mod)

    def test_catalog_and_schema_from_url(self):
        s = DatabricksValueSampler(self.DSN)
        assert s.catalog == "main"
        assert s.schema_name == "sales"

    def test_public_sentinel_falls_back_to_url_schema(self):
        # `infer-fks` forwards snap.pg_schema, which defaults to "public".
        assert DatabricksValueSampler(self.DSN, schema_name="public").schema_name == "sales"

    def test_explicit_schema_overrides_url(self):
        s = DatabricksValueSampler(self.DSN, schema_name="analytics")
        assert s.schema_name == "analytics"

    def test_overlap_ratio_returned(self, monkeypatch):
        captured: list = []
        self._fake_driver(monkeypatch, (0.75,), captured)
        s = DatabricksValueSampler(self.DSN)
        assert s("orders", "user_id", "users", "id") == 0.75

    def test_query_uses_three_level_names_and_inlined_limit(self, monkeypatch):
        # Spark SQL requires a constant in LIMIT, so it must not be bound.
        captured: list = []
        self._fake_driver(monkeypatch, (1.0,), captured)
        DatabricksValueSampler(self.DSN, limit=500)("orders", "user_id", "users", "id")
        sql, params = captured[0]
        assert "`main`.`sales`.`orders`" in sql
        assert "`main`.`sales`.`users`" in sql
        assert "LIMIT 500" in sql
        assert not params

    def test_null_result_is_none(self, monkeypatch):
        self._fake_driver(monkeypatch, (None,), [])
        s = DatabricksValueSampler(self.DSN)
        assert s("orders", "user_id", "users", "id") is None

    def test_connect_failure_returns_none(self):
        # No driver installed / bogus host: the sampler swallows the error so
        # name-based scoring still wins.
        s = DatabricksValueSampler("databricks://:tok@127.0.0.1:1/sql/1.0/warehouses/x")
        assert s("orders", "user_id", "users", "id") is None
        s.close()

    def test_denormalization_probes_are_available(self, monkeypatch):
        captured: list = []
        self._fake_driver(monkeypatch, (0.5,), captured)
        s = DatabricksValueSampler(self.DSN)
        assert s.distinct_ratio("orders", "user_id") == 0.5
        assert s.group_single_valued("orders", ["user_id"], "city") == 0.5
        assert s.delimiter_rate("orders", "tags", ",") == 0.5
        assert captured[-1][1] == (",",)


class TestCreateValueSamplerDispatch:
    def test_postgres(self):
        s = create_value_sampler("postgresql", "postgresql://u:p@h/db")
        assert isinstance(s, PostgresValueSampler)

    def test_mysql(self):
        s = create_value_sampler("mysql", "mysql://u:p@h/shop")
        assert isinstance(s, MySQLValueSampler)

    def test_mariadb_alias(self):
        s = create_value_sampler("mariadb", "mariadb://u:p@h/shop")
        assert isinstance(s, MySQLValueSampler)

    def test_sqlserver(self):
        s = create_value_sampler("sqlserver", "mssql://u:p@h/shop")
        assert isinstance(s, SQLServerValueSampler)

    def test_mssql_alias(self):
        s = create_value_sampler("mssql", "mssql://u:p@h/shop")
        assert isinstance(s, SQLServerValueSampler)

    def test_csv(self, tmp_path):
        s = create_value_sampler("csv", str(tmp_path))
        assert isinstance(s, CsvValueSampler)

    def test_databricks(self):
        s = create_value_sampler(
            "databricks", "databricks://:tok@h/sql/1.0/warehouses/x?catalog=c&schema=s"
        )
        assert isinstance(s, DatabricksValueSampler)

    def test_unsupported_returns_none(self):
        assert create_value_sampler("snowflake", "snowflake://u:p@a/DB") is None
