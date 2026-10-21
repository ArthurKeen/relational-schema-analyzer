"""Recognise a CHECK constraint that enumerates the values one column may hold.

``CheckConstraint.enum_values`` is what declared-CHECK discriminator detection reads
(``discriminator.py``), and before this module only the CSV and dbt connectors filled it.
The live connectors kept the raw expression and nothing else, so on a real Postgres, MySQL
or SQL Server schema the declared value list was lost and declared discriminator detection
found nothing.

Each database rewrites a CHECK before storing it, so the same declaration arrives in
different shapes. ``CHECK (status IN ('a','b'))`` comes back as:

* Postgres: ``CHECK (((status)::text = ANY ((ARRAY['a'::character varying,
  'b'::character varying])::text[])))`` for a ``varchar`` column, and
  ``CHECK ((status = ANY (ARRAY['a'::text, 'b'::text])))`` for ``text``. A one-value list
  becomes a plain ``=``; integers arrive bare, cast (``(1)::bigint``) or, when negative,
  quoted (``'-1'::integer``).
* MySQL 8: ``(`status` in (_utf8mb4'a',_utf8mb4'b'))``, with a character-set introducer
  on every string and negatives written ``-(1)``. Some 8.x releases (8.4 among them)
  report ``information_schema.CHECK_CONSTRAINTS.CHECK_CLAUSE`` with every quote and
  backslash escaped a second time -- ``_utf8mb4\\'a\\'`` -- which
  :func:`parse_mysql_check_clause` undoes.
* SQL Server: ``([status]='b' OR [status]='a')`` -- an ``IN`` list is rewritten as an
  ``OR`` chain, usually in reverse order, with numbers parenthesised (``[level]=(1)``) and
  Unicode strings prefixed ``N'...'``.

**Conservative by design.** A wrong value list is worse than none: it would assert a
taxonomy the schema does not have. So anything outside the shapes above returns ``None``:
more than one column, a range, a function of the column, ``NOT IN`` (stored as ``<>`` /
``<> ALL``), any ``AND``, a ``NULL`` in the list, a non-integer number, a string form whose
escaping is uncertain (Postgres ``E'...'``, hex, bit strings), and a Postgres constraint
marked ``NOT VALID``, whose existing rows were never checked. Whether a constraint is
enforced at all is the connector's question (MySQL ``NOT ENFORCED``, SQL Server
``is_disabled`` / ``is_not_trusted``); this module only reads the text.

Dependency-free: a small tokenizer and a recursive-descent recogniser, with no regex over
the whole expression, so a quote or bracket inside a value cannot be mistaken for syntax.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NamedTuple, Optional, TypeVar

from .types import CheckConstraint

__all__ = [
    "EnumCheck",
    "enrich_check_constraint",
    "parse_enum_check",
    "parse_mysql_check_clause",
    "parse_mysql_enum_type",
]

#: Longest expression worth parsing. Real enumeration CHECKs are a few hundred characters;
#: the bound keeps a pathological catalog value from costing anything.
_MAX_EXPRESSION_LENGTH = 100_000
#: Deepest parenthesis nesting followed before giving up (Postgres adds three or four).
_MAX_DEPTH = 64

#: Unquoted words that can never be a column or a type name in the shapes recognised here.
_RESERVED = frozenset(
    {
        "ALL", "AND", "ANY", "ARRAY", "BETWEEN", "CASE", "CHECK", "COLLATE", "ESCAPE",
        "EXISTS", "FALSE", "ILIKE", "IN", "IS", "LIKE", "NOT", "NULL", "OR", "SIMILAR",
        "SOME", "TRUE", "UNKNOWN",
    }
)


class EnumCheck(NamedTuple):
    """A single-column enumeration: the column as written, and its values in order."""

    column: str
    values: list[str]


_T = TypeVar("_T")


class _Unparseable(Exception):
    """Raised internally for anything outside the recognised shapes."""


# ── Tokenizer ─────────────────────────────────────────────────────────────

# Token kinds.
_IDENT = "ident"  # value = name with quoting removed; quoted flag carried separately
_STR = "str"  # value = the string's content, escapes resolved
_NUM = "num"  # value = the digits as written
_OP = "op"  # value = punctuation or operator text


class _Tok(NamedTuple):
    kind: str
    value: str
    quoted: bool = False


def _read_quoted(text: str, i: int, close: str, *, backslash_escapes: bool) -> tuple[str, int]:
    """Read a quoted run starting just after its opening delimiter.

    ``close`` doubled inside the run stands for itself (``'it''s'``, ``"a""b"``,
    ``[a]]b]``). With ``backslash_escapes`` (MySQL strings), ``\\'``, ``\\"`` and ``\\\\``
    are also accepted; any other backslash escape is rejected rather than guessed at.
    Returns the content and the index just past the closing delimiter.
    """
    out: list[str] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if backslash_escapes and ch == "\\":
            if i + 1 >= n or text[i + 1] not in "'\"\\":
                raise _Unparseable("unsupported backslash escape")
            out.append(text[i + 1])
            i += 2
            continue
        if ch == close:
            if i + 1 < n and text[i + 1] == close:
                out.append(close)
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise _Unparseable("unterminated quoted run")


def _tokenize(text: str, *, backslash_escapes: bool) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(text)
    array_depth = 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "'":
            value, i = _read_quoted(text, i + 1, "'", backslash_escapes=backslash_escapes)
            toks.append(_Tok(_STR, value))
            continue
        if ch == '"':
            value, i = _read_quoted(text, i + 1, '"', backslash_escapes=False)
            toks.append(_Tok(_IDENT, value, quoted=True))
            continue
        if ch == "`":
            value, i = _read_quoted(text, i + 1, "`", backslash_escapes=False)
            toks.append(_Tok(_IDENT, value, quoted=True))
            continue
        if ch == "[":
            # Three meanings: Postgres ``ARRAY[``, a Postgres ``text[]`` suffix, and a SQL
            # Server ``[column]``. Only the last is followed by a name.
            if toks and toks[-1].kind == _IDENT and not toks[-1].quoted and (
                toks[-1].value.upper() == "ARRAY"
            ):
                array_depth += 1
                toks.append(_Tok(_OP, "["))
                i += 1
                continue
            if text.startswith("[]", i):
                toks.append(_Tok(_OP, "[]"))
                i += 2
                continue
            value, i = _read_quoted(text, i + 1, "]", backslash_escapes=False)
            toks.append(_Tok(_IDENT, value, quoted=True))
            continue
        if ch == "]":
            if array_depth == 0:
                raise _Unparseable("unbalanced ]")
            array_depth -= 1
            toks.append(_Tok(_OP, "]"))
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < n and (text[j].isalnum() or text[j] == "."):
                j += 1
            toks.append(_Tok(_NUM, text[i:j]))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            word = text[i:j]
            if j < n and text[j] == "'":
                # A prefix glued to a string literal.
                if word.upper() == "N" or (word.startswith("_") and len(word) > 1):
                    # SQL Server N'..' (Unicode) and MySQL _charset'..' introducers: the
                    # content is unchanged, so the prefix carries nothing to keep.
                    i = j
                    continue
                # E'..' (Postgres escape string), X'..', B'..', U&'..' and the rest: the
                # escaping rules differ, so the value cannot be read reliably.
                raise _Unparseable(f"unsupported string prefix {word!r}")
            toks.append(_Tok(_IDENT, word))
            i = j
            continue
        if text.startswith("::", i):
            toks.append(_Tok(_OP, "::"))
            i += 2
            continue
        for op in ("<>", "!=", ">=", "<=", "||"):
            if text.startswith(op, i):
                toks.append(_Tok(_OP, op))
                i += len(op)
                break
        else:
            toks.append(_Tok(_OP, ch))
            i += 1
    return toks


# ── Recogniser ────────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, toks: list[_Tok]) -> None:
        self.toks = toks
        self.pos = 0
        self.depth = 0

    # Primitive helpers -----------------------------------------------------

    def _peek(self, offset: int = 0) -> Optional[_Tok]:
        idx = self.pos + offset
        return self.toks[idx] if idx < len(self.toks) else None

    def _is_op(self, value: str, offset: int = 0) -> bool:
        tok = self._peek(offset)
        return tok is not None and tok.kind == _OP and tok.value == value

    def _is_word(self, word: str, offset: int = 0) -> bool:
        tok = self._peek(offset)
        return (
            tok is not None and tok.kind == _IDENT and not tok.quoted
            and tok.value.upper() == word
        )

    def _expect_op(self, value: str) -> None:
        if not self._is_op(value):
            raise _Unparseable(f"expected {value!r}")
        self.pos += 1

    def _expect_word(self, word: str) -> None:
        if not self._is_word(word):
            raise _Unparseable(f"expected {word}")
        self.pos += 1

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise _Unparseable("nesting too deep")

    def _leave(self) -> None:
        self.depth -= 1

    def _attempt(self, fn: Callable[[], _T]) -> Optional[_T]:
        """Run ``fn``; on failure rewind and return ``None``."""
        saved = (self.pos, self.depth)
        try:
            return fn()
        except _Unparseable:
            self.pos, self.depth = saved
            return None

    # Grammar -------------------------------------------------------------

    def parse(self) -> EnumCheck:
        if self._is_word("CHECK"):
            self.pos += 1
        result = self.disjunction()
        # Postgres appends ``NO INHERIT`` to a constraint children do not inherit; the
        # values it enforces on this table are unaffected. ``NOT VALID`` (existing rows
        # never checked) and anything else trailing fall through to the error below.
        if self._is_word("NO") and self._is_word("INHERIT", 1):
            self.pos += 2
        if self.pos != len(self.toks):
            raise _Unparseable("trailing tokens")
        return result

    def disjunction(self) -> EnumCheck:
        """``term (OR term)*``, every term naming the same column."""
        first = self.term()
        column, values = first.column, list(first.values)
        while self._is_word("OR"):
            self.pos += 1
            nxt = self.term()
            if nxt.column != column:
                raise _Unparseable("more than one column")
            values.extend(nxt.values)
        return EnumCheck(column, values)

    def term(self) -> EnumCheck:
        """A predicate, or a parenthesised disjunction."""
        found = self._attempt(self.predicate)
        if found is not None:
            return found
        self._expect_op("(")
        self._enter()
        inner = self.disjunction()
        self._expect_op(")")
        self._leave()
        return inner

    def predicate(self) -> EnumCheck:
        column = self.column_ref()
        if self._is_word("IN"):
            self.pos += 1
            self._expect_op("(")
            values = self.value_list(")")
            self._expect_op(")")
            return EnumCheck(column, values)
        self._expect_op("=")
        if self._is_word("ANY"):
            self.pos += 1
            self._expect_op("(")
            values = self.array_expr()
            self._expect_op(")")
            return EnumCheck(column, values)
        return EnumCheck(column, [self.value()])

    def column_ref(self) -> str:
        """A bare or quoted name, optionally parenthesised and cast: ``(status)::text``."""
        tok = self._peek()
        if tok is not None and tok.kind == _IDENT and (
            tok.quoted or tok.value.upper() not in _RESERVED
        ):
            # A name followed by ``(`` is a function call; followed by ``.`` it is
            # qualified. Both are rejected by whatever the caller expects next.
            self.pos += 1
            name = tok.value
        elif self._is_op("("):
            self.pos += 1
            self._enter()
            name = self.column_ref()
            self._expect_op(")")
            self._leave()
        else:
            raise _Unparseable("expected a column")
        self.casts()
        return name

    def array_expr(self) -> list[str]:
        """``ARRAY[v, ...]``, optionally parenthesised and cast: ``(ARRAY[...])::text[]``."""
        if self._is_word("ARRAY"):
            self.pos += 1
            self._expect_op("[")
            values = self.value_list("]")
            self._expect_op("]")
        elif self._is_op("("):
            self.pos += 1
            self._enter()
            values = self.array_expr()
            self._expect_op(")")
            self._leave()
        else:
            raise _Unparseable("expected ARRAY[...]")
        self.casts()
        return values

    def value_list(self, closer: str) -> list[str]:
        values = [self.value()]
        while self._is_op(","):
            self.pos += 1
            values.append(self.value())
        if not self._is_op(closer):
            raise _Unparseable("malformed value list")
        return values

    def value(self) -> str:
        """A string or integer literal, optionally negated, parenthesised and cast."""
        tok = self._peek()
        if tok is not None and tok.kind == _STR:
            self.pos += 1
            out = tok.value
        elif self._is_op("-"):
            self.pos += 1
            out = "-" + self.integer()
        elif self._is_op("("):
            self.pos += 1
            self._enter()
            out = self.value()
            self._expect_op(")")
            self._leave()
        else:
            out = self.integer()
        self.casts()
        return out

    def integer(self) -> str:
        """Digits only -- optionally parenthesised, as MySQL writes ``-(1)``."""
        if self._is_op("("):
            self.pos += 1
            self._enter()
            out = self.integer()
            self._expect_op(")")
            self._leave()
            return out
        tok = self._peek()
        if tok is None or tok.kind != _NUM or not tok.value.isdigit():
            raise _Unparseable("expected an integer")
        self.pos += 1
        return tok.value

    def casts(self) -> None:
        """Zero or more ``::type`` suffixes, discarded: a cast does not change the value set."""
        while self._is_op("::"):
            self.pos += 1
            self.type_name()

    def type_name(self) -> None:
        """``character varying``, ``"char"``, ``public.mood``, ``numeric(10,2)``, ``text[]``."""

        def word() -> bool:
            tok = self._peek()
            if tok is not None and tok.kind == _IDENT and (
                tok.quoted or tok.value.upper() not in _RESERVED
            ):
                self.pos += 1
                return True
            return False

        if not word():
            raise _Unparseable("expected a type name")
        while True:
            if self._is_op("."):
                self.pos += 1
                if not word():
                    raise _Unparseable("expected a type name")
            elif not word():
                break
        if self._is_op("("):
            self.pos += 1
            self.integer()
            while self._is_op(","):
                self.pos += 1
                self.integer()
            self._expect_op(")")
        while self._is_op("[]"):
            self.pos += 1


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def parse_enum_check(expression: str, *, backslash_escapes: bool = False) -> Optional[EnumCheck]:
    """Return ``(column, values)`` when ``expression`` enumerates one column, else ``None``.

    Accepts ``col IN (v, ...)``, ``col = ANY (ARRAY[v, ...])``, ``col = v`` and an ``OR``
    chain of ``col = v`` over a single column, with any amount of redundant parentheses
    and ``::type`` casts, an optional leading ``CHECK`` and a trailing ``NO INHERIT``.
    Values are string literals or integers, returned as text in the order written with
    duplicates removed. ``backslash_escapes`` reads strings by MySQL's rules.
    """
    if not expression or len(expression) > _MAX_EXPRESSION_LENGTH:
        return None
    try:
        toks = _tokenize(expression, backslash_escapes=backslash_escapes)
        found = _Parser(toks).parse()
    except (_Unparseable, RecursionError):
        return None
    if not found.column or not found.values:
        return None
    return EnumCheck(found.column, _dedupe(found.values))


def _undo_mysql_double_escape(clause: str) -> Optional[str]:
    """Undo the extra escaping some MySQL releases apply to ``CHECK_CLAUSE``.

    Detected rather than assumed: the extra layer is present exactly when *every* quote is
    preceded by an odd run of backslashes, since in a normally escaped clause the
    delimiting quotes are bare. Returns ``None`` if the layer looks present but contains an
    escape it should not.
    """
    quotes = [i for i, ch in enumerate(clause) if ch == "'"]
    if not quotes:
        return clause

    def escaped(idx: int) -> bool:
        run = 0
        while idx - 1 - run >= 0 and clause[idx - 1 - run] == "\\":
            run += 1
        return run % 2 == 1

    if not all(escaped(i) for i in quotes):
        return clause
    out: list[str] = []
    i, n = 0, len(clause)
    while i < n:
        ch = clause[i]
        if ch == "\\":
            if i + 1 >= n or clause[i + 1] not in "\\'":
                return None
            out.append(clause[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_mysql_check_clause(clause: str) -> Optional[EnumCheck]:
    """:func:`parse_enum_check` for ``information_schema.CHECK_CONSTRAINTS.CHECK_CLAUSE``."""
    if not clause:
        return None
    unescaped = _undo_mysql_double_escape(clause)
    if unescaped is None:
        return None
    return parse_enum_check(unescaped, backslash_escapes=True)


def parse_mysql_enum_type(column_type: str) -> Optional[list[str]]:
    """Values of a MySQL ``enum('a','b')`` ``COLUMN_TYPE``, or ``None`` for anything else.

    ``COLUMN_TYPE`` doubles an embedded quote (``'it''s'``) and backslash (``'a\\\\b'``).
    ``set(...)`` is not an enumeration -- a row holds any subset -- and is not read.
    """
    if not column_type or len(column_type) > _MAX_EXPRESSION_LENGTH:
        return None
    try:
        toks = _tokenize(column_type, backslash_escapes=True)
    except _Unparseable:
        return None
    if len(toks) < 4 or toks[0].kind != _IDENT or toks[0].quoted:
        return None
    if toks[0].value.lower() != "enum" or toks[1] != _Tok(_OP, "(") or toks[-1] != _Tok(_OP, ")"):
        return None
    body = toks[2:-1]
    values: list[str] = []
    for idx, tok in enumerate(body):
        if idx % 2 == 0:
            if tok.kind != _STR:
                return None
            values.append(tok.value)
        elif tok != _Tok(_OP, ","):
            return None
    if len(body) % 2 == 0:  # trailing comma
        return None
    return _dedupe(values)


def _resolve_column(name: str, table_columns: Sequence[str]) -> Optional[str]:
    """The table's own spelling of ``name``: exact match first, else a unique case-fold match.

    MySQL and (by default) SQL Server compare identifiers case-insensitively; Postgres can
    hold two columns differing only in case, in which case only an exact match is trusted.
    """
    if name in table_columns:
        return name
    folded = [c for c in table_columns if c.casefold() == name.casefold()]
    return folded[0] if len(folded) == 1 else None


def enrich_check_constraint(
    check: CheckConstraint,
    table_columns: Sequence[str],
    *,
    dialect: str = "standard",
) -> CheckConstraint:
    """Return ``check`` with ``enum_values`` filled when it enumerates one column.

    ``dialect="mysql"`` reads the expression by MySQL's rules (see
    :func:`parse_mysql_check_clause`); anything else uses :func:`parse_enum_check`.
    The expression is never rewritten. ``columns`` is filled from the parse only when the
    catalog reported none (MySQL never does; SQL Server does not for a table-level
    constraint). When the catalog did report columns, the parse must agree with them -- a
    single column, the same one -- or the constraint is returned unchanged.
    """
    if check.enum_values:
        return check
    parsed = (
        parse_mysql_check_clause(check.expression)
        if dialect == "mysql"
        else parse_enum_check(check.expression)
    )
    if parsed is None:
        return check
    resolved = _resolve_column(parsed.column, table_columns)
    if resolved is None:
        return check
    if check.columns:
        if len(check.columns) != 1 or check.columns[0].casefold() != resolved.casefold():
            return check
        columns = list(check.columns)
    else:
        columns = [resolved]
    return check.model_copy(update={"enum_values": list(parsed.values), "columns": columns})
