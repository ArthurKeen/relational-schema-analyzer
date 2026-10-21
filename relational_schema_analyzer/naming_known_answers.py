"""Known answers for :func:`~relational_schema_analyzer.naming.singularize` and
:func:`~relational_schema_analyzer.naming.pluralize`.

This is the shared test list for every copy of the singularizer
(arango-solutions-org/r2g-arango#30). RSA, r2g and arangodb-schema-analyzer each
keep their own implementation; each tests it against this list. r2g depends on
RSA, so its tests import this module and check r2g's copy (and ASA's, when
installed) against it. ASA vendors a copy of the module; keep the two identical.

Every entry is lower case. Tests derive the UPPER and Capitalized spellings and
a ``prefix_`` compound from each one, so case and compound handling are covered
without listing them.
"""

from __future__ import annotations

#: ``(plural, singular)``: ``singularize(plural) == singular``,
#: ``pluralize(singular) == plural``, and ``singularize(singular) == singular``.
PLURAL_SINGULAR_PAIRS: tuple[tuple[str, str], ...] = (
    # Regular -s.
    ("accounts", "account"),
    ("orders", "order"),
    ("customers", "customer"),
    ("products", "product"),
    ("invoices", "invoice"),
    ("devices", "device"),
    ("sessions", "session"),
    ("payments", "payment"),
    ("metrics", "metric"),
    ("surveys", "survey"),
    ("keys", "key"),
    ("holidays", "holiday"),
    ("photos", "photo"),
    ("menus", "menu"),
    ("kiwis", "kiwi"),
    # -e words: the plural only adds -s, so -ses/-ces/-zes keep their e.
    ("warehouses", "warehouse"),
    ("courses", "course"),
    ("cases", "case"),
    ("purchases", "purchase"),
    ("databases", "database"),
    ("responses", "response"),
    ("releases", "release"),
    ("licenses", "license"),
    ("phases", "phase"),
    ("houses", "house"),
    ("sizes", "size"),
    ("prizes", "prize"),
    ("caches", "cache"),
    ("niches", "niche"),
    # Sibilant endings take -es.
    ("addresses", "address"),
    ("classes", "class"),
    ("processes", "process"),
    ("businesses", "business"),
    ("boxes", "box"),
    ("taxes", "tax"),
    ("batches", "batch"),
    ("matches", "match"),
    ("branches", "branch"),
    ("searches", "search"),
    ("dishes", "dish"),
    ("buzzes", "buzz"),
    # Singular words that already end in -s.
    ("statuses", "status"),
    ("buses", "bus"),
    ("campuses", "campus"),
    ("viruses", "virus"),
    ("aliases", "alias"),
    ("bonuses", "bonus"),
    ("censuses", "census"),
    ("lenses", "lens"),
    ("gases", "gas"),
    ("canvases", "canvas"),
    # -is -> -es.
    ("analyses", "analysis"),
    ("diagnoses", "diagnosis"),
    ("crises", "crisis"),
    ("theses", "thesis"),
    ("hypotheses", "hypothesis"),
    # -y after a consonant -> -ies; -ie words only add -s.
    ("categories", "category"),
    ("opportunities", "opportunity"),
    ("territories", "territory"),
    ("companies", "company"),
    ("movies", "movie"),
    ("cookies", "cookie"),
    ("calories", "calorie"),
    ("brownies", "brownie"),
    # Irregular.
    ("people", "person"),
    ("children", "child"),
    ("men", "man"),
    ("women", "woman"),
    ("heroes", "hero"),
    ("potatoes", "potato"),
    ("quizzes", "quiz"),
    ("criteria", "criterion"),
    ("matrices", "matrix"),
    ("vertices", "vertex"),
)

#: ``(plural, singular)`` that hold only for :func:`singularize`: the singular
#: pluralizes another way (``index`` -> ``indexes``).
SINGULARIZE_ONLY: tuple[tuple[str, str], ...] = (("indices", "index"),)

#: Words that are the same in both forms: neither function changes them.
INVARIANT_WORDS: tuple[str, ...] = (
    "data",
    "metadata",
    "media",
    "news",
    "series",
    "species",
    "gps",
    "sms",
    "dns",
)
