"""R2RML export: structure, join conditions, and RDF validity.

Assertions run over a parsed rdflib graph rather than the Turtle text, so they
check what an R2RML processor would actually see.
"""

from __future__ import annotations

import pytest

from relational_schema_analyzer import (
    RelationalSchemaAnalyzer,
    export_owl_turtle,
    export_r2rml_turtle,
)
from relational_schema_analyzer.types import Column, ForeignKey, PhysicalSchema, Table

rdflib = pytest.importorskip("rdflib")

RR = "http://www.w3.org/ns/r2rml#"
REL = "http://arangodb.com/schema/relational#"
MAP = "http://arangodb.com/mapping/r2rml#"
DATA = "http://arangodb.com/data/"


def _col(name, dt="integer", nullable=False, pk=False):
    return Column(name=name, data_type=dt, is_nullable=nullable, is_primary_key=pk)


def _graph(schema: PhysicalSchema, **kwargs):
    ttl = export_r2rml_turtle(RelationalSchemaAnalyzer().analyze(schema), **kwargs)
    return rdflib.Graph().parse(data=ttl, format="turtle"), ttl


def _u(iri: str):
    return rdflib.URIRef(iri)


def _one(g, subject, predicate):
    return g.value(subject=subject, predicate=_u(RR + predicate))


def _basic_schema() -> PhysicalSchema:
    users = Table(
        name="users",
        schema_name="sales",
        columns=[_col("id", pk=True), _col("email", "varchar", nullable=True)],
        primary_key=["id"],
    )
    orders = Table(
        name="orders",
        schema_name="sales",
        columns=[_col("id", pk=True), _col("user_id"), _col("total", "numeric")],
        primary_key=["id"],
        foreign_keys=[ForeignKey(column="user_id", foreign_table="users",
                                 foreign_column="id")],
    )
    return PhysicalSchema(tables={"users": users, "orders": orders})


def _join_schema() -> PhysicalSchema:
    """An N:M whose second FK targets a non-PK-named parent column."""
    orders = Table(name="orders", schema_name="sales", primary_key=["id"],
                   columns=[_col("id", pk=True)])
    products = Table(name="products", schema_name="sales", primary_key=["sku"],
                     columns=[_col("sku", "varchar", pk=True)])
    basket = Table(
        name="basket",
        schema_name="sales",
        primary_key=["order_id", "product_sku"],
        columns=[_col("order_id"), _col("product_sku", "varchar"),
                 _col("qty", nullable=True)],
        foreign_keys=[
            ForeignKey(column="order_id", foreign_table="orders", foreign_column="id"),
            ForeignKey(column="product_sku", foreign_table="products",
                       foreign_column="sku"),
        ],
    )
    return PhysicalSchema(tables={t.name: t for t in (orders, products, basket)})


class TestDocumentValidity:
    def test_parses_as_rdf(self):
        g, _ = _graph(_basic_schema())
        assert len(g) > 0

    def test_one_triples_map_per_entity(self):
        g, _ = _graph(_basic_schema())
        maps = set(g.subjects(rdflib.RDF.type, _u(RR + "TriplesMap")))
        assert maps == {_u(MAP + "TriplesMap_Users"), _u(MAP + "TriplesMap_Orders")}

    def test_every_parent_triples_map_resolves(self):
        g, _ = _graph(_join_schema())
        declared = set(g.subjects(rdflib.RDF.type, _u(RR + "TriplesMap")))
        referenced = set(g.objects(None, _u(RR + "parentTriplesMap")))
        assert referenced and not (referenced - declared)


class TestLogicalTable:
    def test_table_name_is_schema_qualified_and_delimited(self):
        # Undelimited mixed-case names silently resolve to the wrong table on
        # PostgreSQL, so the qualified form must survive into the mapping.
        g, _ = _graph(_basic_schema())
        lt = _one(g, _u(MAP + "TriplesMap_Users"), "logicalTable")
        assert str(_one(g, lt, "tableName")) == '"sales"."users"'

    def test_unqualified_when_source_reports_no_schema(self):
        t = Table(name="users", columns=[_col("id", pk=True)], primary_key=["id"])
        g, _ = _graph(PhysicalSchema(tables={"users": t}))
        lt = _one(g, _u(MAP + "TriplesMap_Users"), "logicalTable")
        assert str(_one(g, lt, "tableName")) == '"users"'


class TestSubjectMap:
    def test_template_built_from_primary_key_and_typed(self):
        g, _ = _graph(_basic_schema())
        sm = _one(g, _u(MAP + "TriplesMap_Users"), "subjectMap")
        assert str(_one(g, sm, "template")) == f"{DATA}Users/{{id}}"
        assert _one(g, sm, "class") == _u(REL + "Users")

    def test_composite_key_yields_multi_column_template(self):
        t = Table(name="pair", primary_key=["a", "b"],
                  columns=[_col("a", pk=True), _col("b", pk=True)])
        other = Table(name="other", primary_key=["id"], columns=[_col("id", pk=True)])
        g, _ = _graph(PhysicalSchema(tables={"pair": t, "other": other}))
        sm = _one(g, _u(MAP + "TriplesMap_Pair"), "subjectMap")
        assert str(_one(g, sm, "template")) == f"{DATA}Pair/{{a}}/{{b}}"

    def test_table_without_primary_key_falls_back_to_blank_node(self):
        logs = Table(name="logs", columns=[_col("msg", "text", nullable=True)])
        other = Table(name="other", primary_key=["id"], columns=[_col("id", pk=True)])
        g, ttl = _graph(PhysicalSchema(tables={"logs": logs, "other": other}))
        sm = _one(g, _u(MAP + "TriplesMap_Logs"), "subjectMap")
        assert _one(g, sm, "termType") == _u(RR + "BlankNode")
        assert _one(g, sm, "class") == _u(REL + "Logs")
        assert "No primary key on this table" in ttl


class TestDatatypeProperties:
    def test_column_and_datatype_are_mapped(self):
        g, _ = _graph(_basic_schema())
        found = {}
        for pom in g.objects(_u(MAP + "TriplesMap_Orders"), _u(RR + "predicateObjectMap")):
            om = _one(g, pom, "objectMap")
            col = _one(g, om, "column")
            if col is not None:
                found[str(_one(g, pom, "predicate"))] = (
                    str(col), str(_one(g, om, "datatype"))
                )
        xsd = "http://www.w3.org/2001/XMLSchema#"
        assert found[REL + "Orders_id"] == ("id", xsd + "integer")
        assert found[REL + "Orders_total"] == ("total", xsd + "decimal")


class TestRelationships:
    def test_foreign_key_becomes_referencing_object_map(self):
        g, _ = _graph(_basic_schema())
        for pom in g.objects(_u(MAP + "TriplesMap_Orders"), _u(RR + "predicateObjectMap")):
            if _one(g, pom, "predicate") != _u(REL + "Orders_Users"):
                continue
            om = _one(g, pom, "objectMap")
            assert _one(g, om, "parentTriplesMap") == _u(MAP + "TriplesMap_Users")
            jc = _one(g, om, "joinCondition")
            assert str(_one(g, jc, "child")) == "user_id"
            assert str(_one(g, jc, "parent")) == "id"
            return
        pytest.fail("no referencing object map emitted for the FK")

    def test_join_table_gets_its_own_triples_map(self):
        g, _ = _graph(_join_schema())
        link = _u(MAP + "TriplesMap_Orders_Products_Link")
        assert (link, rdflib.RDF.type, _u(RR + "TriplesMap")) in g
        lt = _one(g, link, "logicalTable")
        assert str(_one(g, lt, "tableName")) == '"sales"."basket"'
        sm = _one(g, link, "subjectMap")
        assert str(_one(g, sm, "template")) == f"{DATA}Orders/{{order_id}}"

    def test_join_condition_uses_real_parent_column_not_the_parent_pk_name(self):
        # `basket.product_sku` references `products.sku`. Before the mapping
        # carried parent columns, a consumer had to guess the parent side.
        g, _ = _graph(_join_schema())
        link = _u(MAP + "TriplesMap_Orders_Products_Link")
        pom = next(iter(g.objects(link, _u(RR + "predicateObjectMap"))))
        om = _one(g, pom, "objectMap")
        assert _one(g, om, "parentTriplesMap") == _u(MAP + "TriplesMap_Products")
        jc = _one(g, om, "joinCondition")
        assert str(_one(g, jc, "child")) == "product_sku"
        assert str(_one(g, jc, "parent")) == "sku"

    def test_join_table_attribute_columns_are_reported_not_silently_dropped(self):
        _, ttl = _graph(_join_schema())
        assert "qty" in ttl
        assert "cannot attach properties to a relationship" in ttl


class TestOntologyAlignment:
    def test_classes_and_predicates_match_the_owl_export(self):
        # The whole point of shipping both: an R2RML processor must populate the
        # very ontology the OWL export declares.
        schema = _basic_schema()
        analysis = RelationalSchemaAnalyzer().analyze(schema)
        owl = rdflib.Graph().parse(data=export_owl_turtle(analysis), format="turtle")
        r2rml = rdflib.Graph().parse(data=export_r2rml_turtle(analysis), format="turtle")

        declared = set(owl.subjects(rdflib.RDF.type, _u("http://www.w3.org/2002/07/owl#Class")))
        used = set(r2rml.objects(None, _u(RR + "class")))
        assert used and used <= declared

        owl_props = {
            s
            for t in ("DatatypeProperty", "ObjectProperty")
            for s in owl.subjects(rdflib.RDF.type, _u(f"http://www.w3.org/2002/07/owl#{t}"))
        }
        predicates = set(r2rml.objects(None, _u(RR + "predicate")))
        assert predicates and predicates <= owl_props


class TestCustomIris:
    def test_all_three_bases_are_honoured(self):
        g, _ = _graph(
            _basic_schema(),
            base_iri="http://ex.org/onto#",
            data_iri="http://ex.org/row/",
            mapping_iri="http://ex.org/m#",
        )
        tm = _u("http://ex.org/m#TriplesMap_Users")
        assert (tm, rdflib.RDF.type, _u(RR + "TriplesMap")) in g
        sm = _one(g, tm, "subjectMap")
        assert str(_one(g, sm, "template")).startswith("http://ex.org/row/Users/")
        assert _one(g, sm, "class") == _u("http://ex.org/onto#Users")


class TestIdentifierQuoting:
    def test_names_needing_delimiting_are_quoted(self):
        t = Table(
            name="odd table",
            schema_name="my schema",
            primary_key=["row id"],
            columns=[_col("row id", pk=True)],
        )
        other = Table(name="other", primary_key=["id"], columns=[_col("id", pk=True)])
        g, _ = _graph(PhysicalSchema(tables={"odd table": t, "other": other}))
        tm = _u(MAP + "TriplesMap_OddTable")
        lt = _one(g, tm, "logicalTable")
        assert str(_one(g, lt, "tableName")) == '"my schema"."odd table"'
        sm = _one(g, tm, "subjectMap")
        assert str(_one(g, sm, "template")) == f'{DATA}OddTable/{{"row id"}}'
        pom = next(iter(g.objects(tm, _u(RR + "predicateObjectMap"))))
        assert str(_one(g, _one(g, pom, "objectMap"), "column")) == '"row id"'

    def test_determinism(self):
        schema = _join_schema()
        a = export_r2rml_turtle(RelationalSchemaAnalyzer().analyze(schema))
        b = export_r2rml_turtle(RelationalSchemaAnalyzer().analyze(schema))
        assert a == b


# ── Executable against a real engine ────────────────────────────────


class TestExecutableAgainstDuckDb:
    """A well-formed mapping is not necessarily a *working* one.

    DuckDB is embedded, so CI can build a real schema, introspect it with the
    real connector, and then run the SQL the emitted mapping implies. This is
    what catches a wrong ``rr:tableName`` quoting style or a join condition
    pointing at the wrong parent column — neither of which a structural
    assertion on the Turtle would notice.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def db(tmp_path_factory):
        duckdb = pytest.importorskip("duckdb")

        path = str(tmp_path_factory.mktemp("r2rml") / "shop.duckdb")
        con = duckdb.connect(path)
        con.execute("CREATE SCHEMA sales")
        con.execute(
            "CREATE TABLE sales.users (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL)"
        )
        con.execute(
            "CREATE TABLE sales.products (id INTEGER PRIMARY KEY, "
            "sku VARCHAR NOT NULL UNIQUE, price DECIMAL(10,2))"
        )
        con.execute(
            "CREATE TABLE sales.orders (id INTEGER PRIMARY KEY, "
            "user_id INTEGER NOT NULL REFERENCES sales.users(id))"
        )
        # The second FK deliberately targets a non-PK unique column.
        con.execute(
            "CREATE TABLE sales.basket ("
            "order_id INTEGER NOT NULL REFERENCES sales.orders(id), "
            "product_sku VARCHAR NOT NULL REFERENCES sales.products(sku), "
            "qty INTEGER, PRIMARY KEY (order_id, product_sku))"
        )
        con.execute("INSERT INTO sales.users VALUES (1,'a@x.com'),(2,'b@x.com')")
        con.execute("INSERT INTO sales.products VALUES (1,'SKU-1',9.99),(2,'SKU-2',24.5)")
        con.execute("INSERT INTO sales.orders VALUES (10,1)")
        con.execute("INSERT INTO sales.basket VALUES (10,'SKU-1',1),(10,'SKU-2',1)")
        con.close()
        return path

    @staticmethod
    @pytest.fixture(scope="class")
    def graph(db):
        from relational_schema_analyzer import create_connector

        schema = create_connector("duckdb", db, schema_name="sales").get_schema()
        ttl = export_r2rml_turtle(RelationalSchemaAnalyzer().analyze(schema))
        return rdflib.Graph().parse(data=ttl, format="turtle")

    @staticmethod
    def _table_of(g, tm):
        return str(_one(g, _one(g, tm, "logicalTable"), "tableName"))

    def test_every_logical_table_resolves_in_the_database(self, db, graph):
        import duckdb

        con = duckdb.connect(db, read_only=True)
        maps = set(graph.subjects(rdflib.RDF.type, _u(RR + "TriplesMap")))
        assert len(maps) == 4
        for tm in maps:
            table = self._table_of(graph, tm)
            con.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        con.close()

    def test_every_join_condition_executes_and_matches_rows(self, db, graph):
        import duckdb

        con = duckdb.connect(db, read_only=True)
        joins = 0
        for tm in graph.subjects(rdflib.RDF.type, _u(RR + "TriplesMap")):
            child_table = self._table_of(graph, tm)
            for pom in graph.objects(tm, _u(RR + "predicateObjectMap")):
                om = _one(graph, pom, "objectMap")
                parent_tm = _one(graph, om, "parentTriplesMap") if om else None
                if parent_tm is None:
                    continue
                pairs = [
                    (str(_one(graph, jc, "child")), str(_one(graph, jc, "parent")))
                    for jc in graph.objects(om, _u(RR + "joinCondition"))
                ]
                assert pairs, "referencing object map with no join condition"
                on = " AND ".join(f'c."{c}" = p."{p}"' for c, p in pairs)
                sql = (  # noqa: S608 - identifiers come from our own mapping
                    f"SELECT count(*) FROM {child_table} c "
                    f"JOIN {self._table_of(graph, parent_tm)} p ON {on}"
                )
                assert con.execute(sql).fetchone()[0] > 0, sql
                joins += 1
        con.close()
        assert joins == 2  # orders->users, basket->products

    def test_join_targets_the_referenced_column_not_the_parent_pk(self, db, graph):
        # basket.product_sku references products.sku; emitting the parent's PK
        # ("id") instead would join on the wrong column and yield bad triples
        # without any error.
        link = _u(MAP + "TriplesMap_Orders_Products_Link")
        pom = next(iter(graph.objects(link, _u(RR + "predicateObjectMap"))))
        jc = _one(graph, _one(graph, pom, "objectMap"), "joinCondition")
        assert (str(_one(graph, jc, "child")), str(_one(graph, jc, "parent"))) == (
            "product_sku",
            "sku",
        )
