"""Enumeration CHECK recognition (``check_enum``).

Every positive expression below is verbatim catalog output, captured from Postgres 16
(``pg_get_constraintdef``), MySQL 8.4 (``information_schema.CHECK_CONSTRAINTS``) and SQL
Server 2022 (``sys.check_constraints.definition``) for the DDL in each comment. Writing
them by hand would test the parser against what we imagine the databases store, which is
how this gap stayed open: the forms differ enough that a guess is usually wrong.

The negative half matters more than the positive. A wrong value list asserts a taxonomy
the schema does not have; a missing one only costs a discriminator.
"""

from __future__ import annotations

import pytest

from relational_schema_analyzer.check_enum import (
    EnumCheck,
    enrich_check_constraint,
    parse_enum_check,
    parse_mysql_check_clause,
    parse_mysql_enum_type,
)
from relational_schema_analyzer.discriminator import detect_discriminators
from relational_schema_analyzer.types import CheckConstraint, Column, Schema, Table

# ── Positive: Postgres 16 ─────────────────────────────────────────────────

POSTGRES = [
    # varchar(10) CHECK (s IN ('a','b'))
    (
        "CHECK (((status)::text = ANY ((ARRAY['a'::character varying, "
        "'b'::character varying])::text[])))",
        "status", ["a", "b"],
    ),
    # text CHECK (s IN ('a','b'))
    ("CHECK ((status = ANY (ARRAY['a'::text, 'b'::text])))", "status", ["a", "b"]),
    # varchar CHECK (s = ANY (ARRAY['a','b']))
    ("CHECK (((status)::text = ANY (ARRAY['a'::text, 'b'::text])))", "status", ["a", "b"]),
    # The form as a human writes it, which Postgres never stores but a dump may carry.
    ("CHECK (status IN ('a','b'))", "status", ["a", "b"]),
    # text CHECK (s = 'a' OR s = 'b')
    ("CHECK (((status = 'a'::text) OR (status = 'b'::text)))", "status", ["a", "b"]),
    # varchar CHECK (s IN ('only')) -- a one-value list is stored as '='
    ("CHECK (((status)::text = 'only'::text))", "status", ["only"]),
    # Doubled quote inside a value.
    ("CHECK ((s = ANY (ARRAY['it''s'::text, 'x'::text])))", "s", ["it's", "x"]),
    # Backslash is literal in a standard string.
    ("CHECK ((s = ANY (ARRAY['a\\b'::text, 'c'::text])))", "s", ["a\\b", "c"]),
    # int CHECK (level IN (1,2,3))
    ("CHECK ((level = ANY (ARRAY[1, 2, 3])))", "level", ["1", "2", "3"]),
    # int CHECK (n IN (-1, 0, 1)) -- the negative comes back quoted and cast
    ("CHECK ((n = ANY (ARRAY['-1'::integer, 0, 1])))", "n", ["-1", "0", "1"]),
    # bigint CHECK (b IN (1,2)) -- parenthesised and cast
    ("CHECK ((b = ANY (ARRAY[(1)::bigint, (2)::bigint])))", "b", ["1", "2"]),
    # char(2) CHECK (c IN ('aa','bb'))
    ("CHECK ((c = ANY (ARRAY['aa'::bpchar, 'bb'::bpchar])))", "c", ["aa", "bb"]),
    # Quoted identifier with a space and capitals.
    ("CHECK ((\"Mixed Case\" = ANY (ARRAY['a'::text, 'b'::text])))", "Mixed Case", ["a", "b"]),
    # NO INHERIT does not change what this table enforces.
    (
        "CHECK (((s)::text = ANY ((ARRAY['a'::character varying, "
        "'b'::character varying])::text[]))) NO INHERIT",
        "s", ["a", "b"],
    ),
    # Multi-word, parameterised and schema-qualified casts.
    ("CHECK ((s = ANY (ARRAY['a'::character varying(10), 'b'::public.mood])))", "s", ["a", "b"]),
]

# ── Positive: MySQL 8 ─────────────────────────────────────────────────────

MYSQL = [
    # As SHOW CREATE TABLE and some 8.0 releases report it.
    ("(`status` in (_utf8mb4'a',_utf8mb4'b'))", "status", ["a", "b"]),
    # As MySQL 8.4 reports CHECK_CLAUSE: every quote and backslash escaped once more.
    ("(`status` in (_utf8mb4\\'a\\',_utf8mb4\\'b\\'))", "status", ["a", "b"]),
    ("(`status` in (_latin1\\'a\\',_latin1\\'b\\'))", "status", ["a", "b"]),
    # CHECK (s IN ('it''s','x','back\\slash')), 8.4 form.
    (
        "(`s` in (_utf8mb4\\'it\\\\\\'s\\',_utf8mb4\\'x\\',_utf8mb4\\'back\\\\\\\\slash\\'))",
        "s", ["it's", "x", "back\\slash"],
    ),
    # The same, single-escaped.
    ("(`s` in (_utf8mb4'it\\'s',_utf8mb4'x'))", "s", ["it's", "x"]),
    # s = 'a' OR s = 'b'
    ("((`s4` = _latin1\\'a\\') or (`s4` = _latin1\\'b\\'))", "s4", ["a", "b"]),
    # IN ('only')
    ("(`s6` = _latin1\\'only\\')", "s6", ["only"]),
    # int CHECK (level IN (1,2,3))
    ("(`level` in (1,2,3))", "level", ["1", "2", "3"]),
    # int CHECK (n IN (-1,0,1)) -- negatives as -(1)
    ("(`n` in (-(1),0,1))", "n", ["-1", "0", "1"]),
]

# ── Positive: SQL Server 2022 ─────────────────────────────────────────────

SQLSERVER = [
    # CHECK (s IN ('a','b')) -- rewritten as a reversed OR chain
    ("([status]='b' OR [status]='a')", "status", ["b", "a"]),
    # CHECK (s = 'a' OR s = 'b')
    ("([status]='a' OR [status]='b')", "status", ["a", "b"]),
    # The IN form, as written in DDL scripts.
    ("([status] IN ('a','b'))", "status", ["a", "b"]),
    # Doubled quote.
    ("([s]='x' OR [s]='it''s')", "s", ["x", "it's"]),
    # nvarchar CHECK (s IN (N'a', N'b'))
    ("([nv]=N'b' OR [nv]=N'a')", "nv", ["b", "a"]),
    # IN ('only')
    ("([s]='only')", "s", ["only"]),
    # int CHECK (level IN (1,2,3)) -- numbers parenthesised
    ("([level]=(3) OR [level]=(2) OR [level]=(1))", "level", ["3", "2", "1"]),
    # int CHECK (n IN (-1,0,1))
    ("([n]=(1) OR [n]=(0) OR [n]=(-1))", "n", ["1", "0", "-1"]),
    # A bracketed name containing a closing bracket.
    ("([a]]b]='x' OR [a]]b]='y')", "a]b", ["x", "y"]),
]


@pytest.mark.parametrize(("expression", "column", "values"), POSTGRES + SQLSERVER)
def test_recognises_stored_forms(expression, column, values):
    assert parse_enum_check(expression) == EnumCheck(column, values)


@pytest.mark.parametrize(("clause", "column", "values"), MYSQL)
def test_recognises_mysql_check_clauses(clause, column, values):
    assert parse_mysql_check_clause(clause) == EnumCheck(column, values)


def test_integer_list_as_written():
    assert parse_enum_check("CHECK (level IN (1,2,3))") == EnumCheck("level", ["1", "2", "3"])


def test_duplicates_are_removed_in_order():
    assert parse_enum_check("([s]='a' OR [s]='b' OR [s]='a')") == EnumCheck("s", ["a", "b"])


def test_keywords_are_case_insensitive():
    assert parse_enum_check("check (s in ('a', 'b'))") == EnumCheck("s", ["a", "b"])


# ── Negative: everything that is not a single-column enumeration ──────────

NOT_ENUMERATIONS = [
    # Two columns.
    "CHECK (((multi1 = 'a'::text) OR (multi2 = 'b'::text)))",
    "([a]='x' OR [b]='y')",
    "((a, b) IN (('x', 'y')))",
    # Ranges.
    "CHECK (((r >= 1) AND (r <= 5)))",
    "([r]>=(1) AND [r]<=(5))",
    "(`r` between 1 and 5)",
    "CHECK ((r > 0))",
    # A function of the column.
    "CHECK ((lower(fn) = ANY (ARRAY['a'::text, 'b'::text])))",
    "(lower([fn])='b' OR lower([fn])='a')",
    "(lower(`fn`) in (_utf8mb4'a',_utf8mb4'b'))",
    # NOT IN, as each database stores it, and as written.
    "CHECK ((s <> 'z'::text))",
    "CHECK ((s <> ALL (ARRAY['y'::text, 'z'::text])))",
    "(NOT ([ni]='y' OR [ni]='z'))",
    "(`ni` <> _utf8mb4'z')",
    "CHECK (s NOT IN ('y','z'))",
    # AND anywhere, including mixed with OR.
    "CHECK ((((mix = 'a'::text) OR (mix = 'b'::text)) AND (mix IS NOT NULL)))",
    "CHECK (s = 'a' OR s = 'b' AND s = 'c')",
    "CHECK (s IN ('a','b') AND s IS NOT NULL)",
    # NULL as a value, and IS NULL as an alternative.
    "CHECK ((nl = ANY (ARRAY['a'::text, NULL::text])))",
    "CHECK (s IN ('a', NULL))",
    "CHECK (s = 'a' OR s IS NULL)",
    # Postgres NOT VALID: existing rows were never checked.
    "CHECK ((s = ANY (ARRAY['q'::text, 'r'::text]))) NOT VALID",
    # Non-integer numbers.
    "(`d` in (1.5,2.0))",
    "CHECK ((d = ANY (ARRAY[1.5, 2.0])))",
    "CHECK (d IN (1e3, 2))",
    # Strings whose escaping rules differ.
    "CHECK (s IN (E'a\\nb', 'c'))",
    "CHECK (s IN (X'41', 'c'))",
    "CHECK (s IN (U&'a', 'c'))",
    # Comparison against another column or expression, not a literal.
    "CHECK (s IN (t, 'a'))",
    "CHECK ((s = t))",
    "CHECK (s IN (SELECT code FROM codes))",
    "CHECK (s = 'a' || 'b')",
    "CHECK (s = 'a'::text || 'b')",
    # Array literal string rather than ARRAY[...].
    "CHECK ((s = ANY ('{a,b}'::text[])))",
    # Qualified column.
    "CHECK (t.s IN ('a','b'))",
    # Pattern matching.
    "CHECK (s LIKE 'a%')",
    "CHECK (s = ANY (ARRAY['a']) OR s LIKE 'b%')",
    # Booleans are not values here.
    "CHECK ((active = true))",
    # Reserved word where a column should be.
    "CHECK (NULL IN ('a'))",
    # Malformed or empty.
    "",
    "CHECK ()",
    "CHECK (s IN ())",
    "CHECK (s IN ('a',))",
    "CHECK (s IN ('a')",
    "CHECK (s IN ('a'))) ",
    "CHECK (s IN ('unterminated))",
    "CHECK (s = ANY (ARRAY['a', 'b']]))",
    "CHECK (s = ANY (ARRAY['a', 'b'))",
    "CHECK (s IN ('a')) extra",
]


@pytest.mark.parametrize("expression", NOT_ENUMERATIONS)
def test_rejects_everything_else(expression):
    assert parse_enum_check(expression) is None


@pytest.mark.parametrize(
    "clause",
    [
        "(`r` between 1 and 5)",
        "(`ni` <> _utf8mb4\\'z\\')",
        "(lower(`fn`) in (_utf8mb4\\'a\\',_utf8mb4\\'b\\'))",
        "((`a` = _utf8mb4\\'x\\') or (`b` = _utf8mb4\\'y\\'))",
        "(`d` in (1.5,2.0))",
        # An unknown backslash escape (\n) is not guessed at.
        "(`s` in (_utf8mb4'a\\nb',_utf8mb4'c'))",
        # A double-escaped clause with a stray escape.
        "(`s` in (_utf8mb4\\'a\\',_utf8mb4\\'b\\'))\\x",
        "",
    ],
)
def test_rejects_mysql_non_enumerations(clause):
    assert parse_mysql_check_clause(clause) is None


def test_backslash_is_literal_outside_mysql_mode():
    # Standard SQL: '\' is an ordinary character, so 'a\' is a complete string.
    assert parse_enum_check("CHECK (s IN ('a\\', 'b'))") == EnumCheck("s", ["a\\", "b"])


def test_pathological_input_is_rejected_not_crashed():
    deep = "(" * 500 + "s = 'a'" + ")" * 500
    assert parse_enum_check(deep) is None
    huge = "CHECK (s IN (" + ",".join(f"'{i}'" for i in range(30_000)) + "))"
    assert parse_enum_check(huge) is None


# ── MySQL ENUM column types ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("column_type", "values"),
    [
        # Verbatim COLUMN_TYPE from MySQL 8.4 for enum('x','it''s','y z','back\\slash').
        ("enum('x','it''s','y z','back\\\\slash')", ["x", "it's", "y z", "back\\slash"]),
        ("enum('only')", ["only"]),
        ("ENUM('a','b')", ["a", "b"]),
    ],
)
def test_mysql_enum_type(column_type, values):
    assert parse_mysql_enum_type(column_type) == values


@pytest.mark.parametrize(
    "column_type",
    ["set('a','b')", "varchar(10)", "enum()", "enum('a',)", "enum('a' 'b')", "enum(1,2)",
     "enum('a'", "", "enum('a','b') extra"],
)
def test_mysql_enum_type_rejects_others(column_type):
    assert parse_mysql_enum_type(column_type) is None


# ── Reconciling the parse with the catalog ────────────────────────────────

COLUMNS = ["id", "status", "Kind"]


def _check(expression, columns=()):
    return CheckConstraint(name="c", expression=expression, columns=list(columns))


def test_fills_values_and_keeps_the_raw_expression():
    raw = "CHECK ((status = ANY (ARRAY['a'::text, 'b'::text])))"
    out = enrich_check_constraint(_check(raw, ["status"]), COLUMNS)
    assert out.enum_values == ["a", "b"]
    assert out.columns == ["status"]
    assert out.expression == raw
    assert out.name == "c"


def test_fills_columns_when_the_catalog_reported_none():
    out = enrich_check_constraint(_check("([status]='a' OR [status]='b')"), COLUMNS)
    assert out.columns == ["status"]


def test_resolves_to_the_tables_own_spelling():
    out = enrich_check_constraint(_check("([kind]='a' OR [kind]='b')"), COLUMNS)
    assert out.columns == ["Kind"]


def test_ambiguous_case_fold_needs_an_exact_match():
    cols = ["Status", "STATUS"]
    assert enrich_check_constraint(_check("CHECK (status IN ('a'))"), cols).enum_values is None
    exact = enrich_check_constraint(_check('CHECK ("STATUS" IN (\'a\'))'), cols)
    assert exact.columns == ["STATUS"]


def test_unknown_column_is_left_alone():
    check = _check("CHECK (missing IN ('a','b'))")
    assert enrich_check_constraint(check, COLUMNS) == check


def test_disagreeing_with_the_catalog_is_left_alone():
    check = _check("CHECK (status IN ('a','b'))", ["id"])
    assert enrich_check_constraint(check, COLUMNS) == check


def test_a_multi_column_catalog_entry_is_left_alone():
    check = _check("CHECK (status IN ('a','b'))", ["status", "id"])
    assert enrich_check_constraint(check, COLUMNS) == check


def test_existing_values_are_not_replaced():
    check = CheckConstraint(
        expression="CHECK (status IN ('a','b'))", columns=["status"], enum_values=["z"]
    )
    assert enrich_check_constraint(check, COLUMNS).enum_values == ["z"]


def test_mysql_dialect_reads_mysql_escaping():
    clause = "(`status` in (_utf8mb4\\'a\\',_utf8mb4\\'b\\'))"
    assert enrich_check_constraint(_check(clause), COLUMNS).enum_values is None
    assert enrich_check_constraint(_check(clause), COLUMNS, dialect="mysql").enum_values == [
        "a", "b",
    ]


def test_input_is_not_mutated():
    check = _check("CHECK (status IN ('a','b'))")
    enrich_check_constraint(check, COLUMNS)
    assert check.enum_values is None
    assert check.columns == []


# ── Downstream: declared discriminator detection now fires ────────────────


def test_parsed_check_reaches_discriminator_detection():
    raw = (
        "CHECK (((account_type)::text = ANY ((ARRAY['personal'::character varying, "
        "'business'::character varying])::text[])))"
    )
    table = Table(
        name="accounts",
        columns=[
            Column(name="id", data_type="integer", is_primary_key=True),
            Column(name="account_type", data_type="character varying"),
        ],
        primary_key=["id"],
        check_constraints=[_check(raw, ["account_type"])],
    )
    assert detect_discriminators(Schema(tables={"accounts": table})) == []

    enriched = table.model_copy(
        update={
            "check_constraints": [
                enrich_check_constraint(c, ["id", "account_type"])
                for c in table.check_constraints
            ]
        }
    )
    (found,) = detect_discriminators(Schema(tables={"accounts": enriched}))
    assert (found.column, found.values, found.source) == (
        "account_type", ["business", "personal"], "check_constraint",
    )
