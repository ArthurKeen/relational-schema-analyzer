"""Identifier naming helpers (dependency-free subset).

Extracted from ``r2g/src/r2g/naming.py``. Only the paradigm-neutral string
utilities are lifted (``split_identifier``, ``pluralize``, ``singularize``,
``convert_identifier``). The ``apply_naming_convention`` routine — which mutates
the ArangoDB ``MappingConfig`` — stays in ``r2g``.
"""

from __future__ import annotations

import re
from typing import Literal

NameCase = Literal["preserve", "snake", "camel", "pascal"]

# Split an identifier into words. Handles snake_case, kebab-case, dotted, spaced,
# camelCase, PascalCase and acronym runs (e.g. "HTTPServer" -> ["HTTP", "Server"]).
_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+")


def split_identifier(name: str) -> list[str]:
    """Break ``name`` into normalized word tokens (order preserved)."""
    words: list[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        if part:
            words.extend(_WORD_RE.findall(part))
    return [w for w in words if w]


#: Same word in both forms; neither :func:`pluralize` nor :func:`singularize`
#: changes it.
_INVARIANT = frozenset(
    {"data", "metadata", "media", "news", "series", "species", "gps", "sms", "dns"}
)

#: Singular words that already end in ``s``. Their plural adds ``es``, except
#: ``-is`` words, which go to ``-es`` (``analysis`` -> ``analyses``).
_SINGULAR_ENDING_IN_S = frozenset(
    {
        "status", "bus", "campus", "virus", "alias", "bonus", "census", "corpus",
        "lens", "gas", "canvas", "atlas", "chassis",
        "analysis", "diagnosis", "crisis", "thesis", "hypothesis", "synopsis",
        "synthesis", "parenthesis", "emphasis",
    }
)

#: Irregular plural -> singular. :func:`pluralize` uses the reverse.
_IRREGULAR = {
    "people": "person",
    "children": "child",
    "men": "man",
    "women": "woman",
    "heroes": "hero",
    "potatoes": "potato",
    "tomatoes": "tomato",
    "echoes": "echo",
    "quizzes": "quiz",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "matrices": "matrix",
    "vertices": "vertex",
}
#: Plural -> singular for :func:`singularize` only (``index`` pluralizes to ``indexes``).
_SINGULARIZE_ONLY = {"indices": "index"}
_IRREGULAR_PLURAL = {v: k for k, v in _IRREGULAR.items()}

#: ``-ie`` singulars, whose plural ``-ies`` must not become ``-y``.
_IE_SINGULARS = frozenset(
    {
        "movie", "cookie", "calorie", "zombie", "rookie", "selfie", "hoodie", "pie", "tie",
        "lie", "brownie", "genie", "smoothie", "collie", "magpie", "birdie", "prairie",
        "reverie", "sortie", "lingerie",
    }
)
#: ``-che`` singulars, whose plural ``-ches`` must not lose its ``e``.
_CHE_SINGULARS = frozenset({"cache", "niche", "avalanche", "headache", "moustache", "cliche"})


def _same_case(target: str, like: str) -> str:
    """Spell ``target`` in the case of ``like`` (UPPER, Capitalized or lower)."""
    if like.isupper():
        return target.upper()
    if like[:1].isupper():
        return target[:1].upper() + target[1:]
    return target


def _split_tail(word: str) -> tuple[str, str]:
    """``(head, tail)`` where ``tail`` is the last word of the identifier, so a
    compound (``ORDER_STATUS``, ``usage_metrics``) inflects only its last word."""
    words = split_identifier(word)
    if words and word.endswith(words[-1]):
        tail = words[-1]
        return word[: len(word) - len(tail)], tail
    return "", word


def _singular_word(word: str) -> str:
    low = word.lower()
    if low in _INVARIANT or low in _SINGULAR_ENDING_IN_S:
        return word
    irregular = _IRREGULAR.get(low) or _SINGULARIZE_ONLY.get(low)
    if irregular is not None:
        return _same_case(irregular, word)
    if low.endswith("ies") and len(word) > 3:
        if low[:-1] in _IE_SINGULARS:
            return word[:-1]
        return word[:-3] + ("Y" if word[-1].isupper() else "y")
    if low.endswith("es") and len(word) > 3:
        stem = low[:-2]
        if stem + "is" in _SINGULAR_ENDING_IN_S:  # analyses -> analysis
            return word[:-2] + ("IS" if word[-1].isupper() else "is")
        if stem in _SINGULAR_ENDING_IN_S or stem.endswith(("ss", "x", "sh", "zz")):
            return word[:-2]
        if stem.endswith("ch") and stem + "e" not in _CHE_SINGULARS:
            return word[:-2]
        # Otherwise the singular ends in e (warehouses, sizes, caches): drop only the s.
    if low.endswith("s") and not low.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def _plural_word(word: str) -> str:
    low = word.lower()
    upper = word[-1].isupper()
    if low in _INVARIANT:
        return word
    irregular = _IRREGULAR_PLURAL.get(low)
    if irregular is not None:
        return _same_case(irregular, word)
    if low in _SINGULAR_ENDING_IN_S and low.endswith("is"):
        return word[:-2] + ("ES" if upper else "es")
    if low.endswith("y") and len(word) > 1 and low[-2] not in "aeiou":
        return word[:-1] + ("IES" if upper else "ies")
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return word + ("ES" if upper else "es")
    return word + ("S" if upper else "s")


def pluralize(word: str) -> str:
    """Best-effort English plural for table / relationship name heuristics.

    ``y`` after a consonant -> ``ies``; sibilant endings (``s``/``x``/``z``/
    ``ch``/``sh``) -> ``es``; ``-is`` singulars -> ``-es``; a short irregular
    table (``person`` -> ``people``); words that do not change (``data``,
    ``series``); else append ``s``. Only the last word of a compound is
    inflected. Matching is case-insensitive and added letters follow the case
    of the word's last letter. Used only for fuzzy name matching, never for
    stored names. Checked against :mod:`.naming_known_answers`.
    """
    if not word:
        return word
    head, tail = _split_tail(word)
    return head + _plural_word(tail)


def singularize(word: str) -> str:
    """Best-effort English singular, the inverse of :func:`pluralize`.

    ``ies`` -> ``y`` (but ``movies`` -> ``movie``); ``-es`` is dropped only
    after a sibilant (``addresses``, ``boxes``, ``batches``) or a singular that
    already ends in ``s`` (``statuses`` -> ``status``); any other ``-es`` keeps
    its ``e`` (``warehouses`` -> ``warehouse``, ``sizes`` -> ``size``); a
    trailing ``s`` (not ``ss``) is dropped; singular words that end in ``s``
    (``status``, ``analysis``), invariant words (``news``, ``series``) and a
    short irregular table (``people`` -> ``person``) are handled by name. Only
    the last word of a compound is inflected (``ORDER_STATUS`` stays put).

    Matching is case-insensitive and the replacement follows the case of the
    word, so Snowflake-style ``CUSTOMERS`` singularizes like ``customers`` (the
    same fix as r2g-arango#4). Used only for fuzzy name matching (e.g.
    ``orders`` <-> ``order``), never for stored names. Checked against
    :mod:`.naming_known_answers`, the list every copy of this function shares
    (r2g-arango#30).
    """
    if not word:
        return word
    head, tail = _split_tail(word)
    return head + _singular_word(tail)


def convert_identifier(name: str, style: NameCase) -> str:
    """Re-case ``name`` into ``style``.

    Returns ``name`` unchanged when ``style`` is ``"preserve"`` or when the
    identifier yields no word tokens (e.g. it is empty or all punctuation).
    """
    if style == "preserve" or not name:
        return name
    words = split_identifier(name)
    if not words:
        return name
    lower = [w.lower() for w in words]
    if style == "snake":
        return "_".join(lower)
    if style == "pascal":
        return "".join(w.capitalize() for w in lower)
    if style == "camel":
        return lower[0] + "".join(w.capitalize() for w in lower[1:])
    return name
