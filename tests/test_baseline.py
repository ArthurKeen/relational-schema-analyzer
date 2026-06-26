from __future__ import annotations

from relational_schema_analyzer.baseline import infer_baseline
from relational_schema_analyzer.types import Column, ForeignKey, PhysicalSchema, Table


def _col(name, data_type="integer", nullable=False, pk=False):
    return Column(name=name, data_type=data_type, is_nullable=nullable, is_primary_key=pk)


def _schema(*tables: Table) -> PhysicalSchema:
    return PhysicalSchema(tables={t.name: t for t in tables})


def _rel_by_type(result, rel_type):
    return next(r for r in result["conceptualSchema"]["relationships"] if r["type"] == rel_type)


# ── Entities & properties ───────────────────────────────────────────


class TestEntities:
    def test_table_becomes_entity_with_pascal_name(self):
        users = Table(name="users", columns=[_col("id", pk=True), _col("name", "text")],
                      primary_key=["id"])
        result = infer_baseline(_schema(users))
        entities = result["conceptualSchema"]["entities"]
        assert [e["name"] for e in entities] == ["Users"]
        assert entities[0]["labels"] == ["Users"]
        assert entities[0]["source"] == "baseline"

    def test_columns_become_typed_properties(self):
        users = Table(
            name="users",
            columns=[_col("id", pk=True), _col("email", "varchar", nullable=True)],
            primary_key=["id"],
        )
        result = infer_baseline(_schema(users))
        props = {p["name"]: p for p in result["conceptualSchema"]["entities"][0]["properties"]}
        assert props["id"]["type"] == "integer"
        assert props["id"]["unique"] is True
        assert props["id"]["indexed"] is True
        assert props["email"]["type"] == "string"
        assert props["email"]["nullable"] is True
        assert "unique" not in props["email"]

    def test_physical_mapping_records_table_back_reference(self):
        users = Table(name="users", columns=[_col("id", pk=True)], primary_key=["id"])
        result = infer_baseline(_schema(users))
        em = result["physicalMapping"]["entities"]["Users"]
        assert em["style"] == "TABLE"
        assert em["tableName"] == "users"
        assert em["primaryKey"] == ["id"]
        assert em["properties"]["id"]["field"] == "id"
        assert em["properties"]["id"]["sqlType"] == "integer"

    def test_missing_primary_key_flags_review(self):
        t = Table(name="logs", columns=[_col("msg", "text", nullable=True)])
        result = infer_baseline(_schema(t))
        assert result["reviewRequired"] is True
        assert "missing_primary_key" in result["detectedPatterns"]


# ── Foreign keys ────────────────────────────────────────────────────


class TestForeignKeys:
    def test_fk_becomes_foreign_key_relationship_1_to_n(self):
        users = Table(name="users", columns=[_col("id", pk=True)], primary_key=["id"])
        orders = Table(
            name="orders",
            columns=[_col("id", pk=True), _col("user_id")],
            primary_key=["id"],
            foreign_keys=[ForeignKey(column="user_id", foreign_table="users",
                                     foreign_column="id")],
        )
        result = infer_baseline(_schema(users, orders))
        rel = _rel_by_type(result, "Orders_Users")
        assert rel["fromEntity"] == "Orders"
        assert rel["toEntity"] == "Users"
        assert rel["cardinality"] == "1:N"
        pm = result["physicalMapping"]["relationships"]["Orders_Users"]
        assert pm["style"] == "FOREIGN_KEY"
        assert pm["fromTable"] == "orders"
        assert pm["fromColumns"] == ["user_id"]
        assert pm["toTable"] == "users"
        assert pm["toColumns"] == ["id"]

    def test_fk_equal_to_pk_is_one_to_one(self):
        users = Table(name="users", columns=[_col("id", pk=True)], primary_key=["id"])
        profiles = Table(
            name="profiles",
            columns=[_col("user_id", pk=True)],
            primary_key=["user_id"],
            foreign_keys=[ForeignKey(column="user_id", foreign_table="users",
                                     foreign_column="id")],
        )
        result = infer_baseline(_schema(users, profiles))
        # single-col PK that is itself the FK → inheritance candidate + 1:1
        rel = _rel_by_type(result, "Profiles_Users")
        assert rel["cardinality"] == "1:1"
        assert "inheritance_via_shared_pk" in result["detectedPatterns"]
        assert result["reviewRequired"] is True
        profile = next(
            e for e in result["conceptualSchema"]["entities"] if e["name"] == "Profiles"
        )
        assert profile["subClassOf"] == "Users"

    def test_two_fks_between_same_entities_get_distinct_types(self):
        users = Table(name="users", columns=[_col("id", pk=True)], primary_key=["id"])
        messages = Table(
            name="messages",
            columns=[_col("id", pk=True), _col("sender_id"), _col("recipient_id")],
            primary_key=["id"],
            foreign_keys=[
                ForeignKey(column="sender_id", foreign_table="users", foreign_column="id"),
                ForeignKey(column="recipient_id", foreign_table="users", foreign_column="id"),
            ],
        )
        result = infer_baseline(_schema(users, messages))
        types = {
            r["type"]
            for r in result["conceptualSchema"]["relationships"]
            if r["fromEntity"] == "Messages"
        }
        assert len(types) == 2


# ── Join tables ─────────────────────────────────────────────────────


class TestJoinTables:
    def test_join_table_becomes_n_to_m_relationship_not_entity(self):
        students = Table(name="students", columns=[_col("id", pk=True)], primary_key=["id"])
        courses = Table(name="courses", columns=[_col("id", pk=True)], primary_key=["id"])
        enrollments = Table(
            name="enrollments",
            columns=[_col("student_id", pk=True), _col("course_id", pk=True),
                     _col("grade", "text", nullable=True)],
            primary_key=["student_id", "course_id"],
            foreign_keys=[
                ForeignKey(column="student_id", foreign_table="students", foreign_column="id"),
                ForeignKey(column="course_id", foreign_table="courses", foreign_column="id"),
            ],
        )
        result = infer_baseline(_schema(students, courses, enrollments))
        entity_names = {e["name"] for e in result["conceptualSchema"]["entities"]}
        assert entity_names == {"Students", "Courses"}
        assert "join_table" in result["detectedPatterns"]
        rel = _rel_by_type(result, "Students_Courses")
        assert rel["cardinality"] == "N:M"
        assert [p["name"] for p in rel["properties"]] == ["grade"]
        pm = result["physicalMapping"]["relationships"]["Students_Courses"]
        assert pm["style"] == "JOIN_TABLE"
        assert pm["joinTable"] == "enrollments"
        assert pm["joinFromColumns"] == ["student_id"]
        assert pm["joinToColumns"] == ["course_id"]
        assert pm["attributeColumns"] == ["grade"]


# ── Inferred FKs (no declared constraints) ──────────────────────────


class TestInferredForeignKeys:
    def test_inference_runs_when_no_fks_declared(self):
        author = Table(name="author", columns=[_col("id", pk=True)], primary_key=["id"])
        book = Table(
            name="book",
            columns=[_col("id", pk=True), _col("author_id")],
            primary_key=["id"],
        )
        result = infer_baseline(_schema(author, book))
        assert "inferred_foreign_keys" in result["detectedPatterns"]
        assert result["reviewRequired"] is True
        rel = _rel_by_type(result, "Book_Author")
        assert rel["inferred"] is True
        assert 0.0 < rel["confidence"] <= 1.0
        assert result["physicalMapping"]["relationships"]["Book_Author"]["inferred"] is True

    def test_no_inference_when_fks_declared(self):
        author = Table(name="author", columns=[_col("id", pk=True)], primary_key=["id"])
        book = Table(
            name="book",
            columns=[_col("id", pk=True), _col("author_id")],
            primary_key=["id"],
            foreign_keys=[ForeignKey(column="author_id", foreign_table="author",
                                     foreign_column="id")],
        )
        result = infer_baseline(_schema(author, book))
        assert "inferred_foreign_keys" not in result["detectedPatterns"]
        rel = _rel_by_type(result, "Book_Author")
        assert "inferred" not in rel
