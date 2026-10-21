"""Measure inference against schemas whose real answer is already declared (#27).

Every ranking decision runs through constants chosen by judgement — 17 of them, plus
four thresholds. They work, and nobody can say what any of them is worth, because it
has never been measured. Six releases in one week each changed what evidence reaches
the scorer; each was verified against its own case and none against the whole.

The trick that makes measuring cheap: a database that *declares* its keys is already
labelled. Strip the declarations, run inference over what is left, and compare what
comes back with what was removed. Chinook, Northwind and Pagila give 55 tables, 63
foreign keys and 47 primary keys for the price of loading three SQL dumps.

This module is the harness only. It asserts nothing about what good looks like — the
first job is to state where we are, not to defend it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from relational_schema_analyzer.fk_inference import InferenceOptions, infer_foreign_keys
from relational_schema_analyzer.types import PhysicalSchema

#: A foreign key, normalized for comparison: case folded, column order preserved.
#: Order matters — ``(a, b) -> (x, y)`` is a different key from ``(b, a) -> (x, y)``.
Ref = tuple[str, tuple[str, ...], str, tuple[str, ...]]


def _ref(table: str, cols: Iterable[str], ftable: str, fcols: Iterable[str]) -> Ref:
    return (
        table.lower(),
        tuple(c.lower() for c in cols),
        ftable.lower(),
        tuple(c.lower() for c in fcols),
    )


def declared_refs(schema: PhysicalSchema) -> set[Ref]:
    """The foreign keys the database itself declares — the ground truth."""
    return {
        _ref(name, fk.columns, fk.foreign_table, fk.foreign_columns)
        for name, table in schema.tables.items()
        for fk in table.foreign_keys
    }


def without_foreign_keys(schema: PhysicalSchema) -> PhysicalSchema:
    """A copy with every foreign key removed, primary and unique keys intact.

    The realistic shape of the problem: a warehouse that landed its tables without
    constraints but kept the keys. Removing the primary keys as well is a different
    and harder question — see :func:`without_any_keys`.
    """
    stripped = schema.model_copy(deep=True)
    for table in stripped.tables.values():
        table.foreign_keys = []
    return stripped


def without_any_keys(schema: PhysicalSchema) -> PhysicalSchema:
    """A copy with foreign, primary and unique keys all removed.

    What a constraint-free source actually looks like. Inference alone can do nothing
    here — there are no targets — so this is the case key profiling exists for.
    """
    stripped = without_foreign_keys(schema)
    for table in stripped.tables.values():
        table.primary_key = []
        table.unique_constraints = []
    return stripped


@dataclass
class Score:
    """What inference got right, wrong, and missed, against a known answer."""

    corpus: str
    expected: int
    proposed: int
    correct: int
    missed: set[Ref] = field(default_factory=set)
    spurious: set[Ref] = field(default_factory=set)

    @property
    def precision(self) -> float:
        """Of what it proposed, how much was real. 1.0 when it proposed nothing."""
        return self.correct / self.proposed if self.proposed else 1.0

    @property
    def recall(self) -> float:
        """Of what was real, how much it found."""
        return self.correct / self.expected if self.expected else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def line(self) -> str:
        return (
            f"{self.corpus:12} expected={self.expected:3} proposed={self.proposed:3} "
            f"correct={self.correct:3}  precision={self.precision:.3f} "
            f"recall={self.recall:.3f} f1={self.f1:.3f}"
        )


def score_fk_inference(
    schema: PhysicalSchema,
    *,
    corpus: str,
    options: Optional[InferenceOptions] = None,
    sampler: Any = None,
) -> Score:
    """Strip the declared foreign keys, re-infer them, and score the result."""
    truth = declared_refs(schema)
    candidates = infer_foreign_keys(
        without_foreign_keys(schema), options=options, sampler=sampler
    )
    found = {
        _ref(c.table, c.columns, c.foreign_table, c.foreign_columns) for c in candidates
    }
    correct = truth & found
    return Score(
        corpus=corpus,
        expected=len(truth),
        proposed=len(found),
        correct=len(correct),
        missed=truth - found,
        spurious=found - truth,
    )


def score_primary_keys(schema: PhysicalSchema, profile: Any, *, corpus: str) -> Score:
    """Score proposed primary keys against the ones the database declares.

    ``profile`` is a :class:`~relational_schema_analyzer.key_profiling.KeyProfile` built
    over a key-stripped copy. Only tables that actually declared a primary key count:
    a table without one has no answer to be right or wrong about.
    """
    truth = {
        (name.lower(), tuple(c.lower() for c in t.primary_key))
        for name, t in schema.tables.items()
        if t.primary_key
    }
    proposed = set()
    for tname in profile.candidates:
        best = profile.best(tname)
        if best is not None:
            proposed.add((tname.lower(), tuple(c.lower() for c in best.columns)))
    correct = truth & proposed
    return Score(
        corpus=corpus,
        expected=len(truth),
        proposed=len(proposed),
        correct=len(correct),
        missed={t for t in truth if t not in correct},  # type: ignore[misc]
        spurious={p for p in proposed if p not in correct},  # type: ignore[misc]
    )


def total(scores: Iterable[Score], *, corpus: str = "ALL") -> Score:
    """Pool several corpora into one score, so a small corpus cannot dominate."""
    scores = list(scores)
    return Score(
        corpus=corpus,
        expected=sum(s.expected for s in scores),
        proposed=sum(s.proposed for s in scores),
        correct=sum(s.correct for s in scores),
        missed=set().union(*(s.missed for s in scores)) if scores else set(),
        spurious=set().union(*(s.spurious for s in scores)) if scores else set(),
    )
