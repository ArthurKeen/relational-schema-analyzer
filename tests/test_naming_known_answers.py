"""singularize / pluralize against the shared known-answer list (r2g-arango#30)."""

from __future__ import annotations

import pytest

from relational_schema_analyzer.naming import pluralize, singularize
from relational_schema_analyzer.naming_known_answers import (
    INVARIANT_WORDS,
    PLURAL_SINGULAR_PAIRS,
    SINGULARIZE_ONLY,
)


def _spellings(word: str) -> list[tuple[str, str]]:
    """``(spelling, case)`` for lower, UPPER, Capitalized and a ``prefix_`` compound."""
    return [
        (word, "lower"),
        (word.upper(), "upper"),
        (word[:1].upper() + word[1:], "capitalized"),
        (f"order_{word}", "compound"),
        (f"ORDER_{word.upper()}", "upper compound"),
    ]


def _respell(answer: str, case: str) -> str:
    return {
        "lower": answer,
        "upper": answer.upper(),
        "capitalized": answer[:1].upper() + answer[1:],
        "compound": f"order_{answer}",
        "upper compound": f"ORDER_{answer.upper()}",
    }[case]


@pytest.mark.parametrize(("plural", "singular"), PLURAL_SINGULAR_PAIRS + SINGULARIZE_ONLY)
def test_singularize_known_plurals(plural, singular):
    for spelling, case in _spellings(plural):
        assert singularize(spelling) == _respell(singular, case), spelling


@pytest.mark.parametrize(("plural", "singular"), PLURAL_SINGULAR_PAIRS)
def test_pluralize_known_singulars(plural, singular):
    for spelling, case in _spellings(singular):
        assert pluralize(spelling) == _respell(plural, case), spelling


@pytest.mark.parametrize(("plural", "singular"), PLURAL_SINGULAR_PAIRS + SINGULARIZE_ONLY)
def test_singularize_leaves_a_singular_alone(plural, singular):
    # Tables are often already singular (region, order_status, address).
    for spelling, case in _spellings(singular):
        assert singularize(spelling) == _respell(singular, case), spelling


@pytest.mark.parametrize("word", INVARIANT_WORDS)
def test_invariant_words_do_not_change(word):
    for spelling, _ in _spellings(word):
        assert singularize(spelling) == spelling
        assert pluralize(spelling) == spelling


def test_the_list_has_no_contradictions():
    plurals = [p for p, _ in PLURAL_SINGULAR_PAIRS + SINGULARIZE_ONLY]
    singulars = [s for _, s in PLURAL_SINGULAR_PAIRS]
    assert len(plurals) == len(set(plurals))
    assert len(singulars) == len(set(singulars))
    assert not set(INVARIANT_WORDS) & (set(plurals) | set(singulars))
    assert all(w == w.lower() for w in plurals + singulars + list(INVARIANT_WORDS))
