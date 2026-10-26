"""Build relational_schema_analyzer/data/noun_lexicon.json.gz from LemmInflect's lexicon.

The table is the dictionary layer of ``naming.singularize`` / ``naming.pluralize``
(arango-solutions-org/r2g-arango#30, design chosen after the benchmark in
``tools/singularize_benchmark``). It is generated, not hand-edited: re-run this
script to rebuild it, and commit the result. It needs LemmInflect at *build*
time only; RSA does not depend on it at runtime.

    pip install lemminflect==0.2.3
    python tools/build_noun_lexicon.py

Source: LemmInflect 0.2.3 (MIT, Copyright (C) 2019 Brad Jascob),
``resources/lemma_lu.csv.gz`` (inflected form -> lemma) and
``resources/infl_lu.csv.gz`` (lemma -> inflected forms). Only lower-case,
all-letter noun entries are kept (proper nouns and hyphenated forms are dropped).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "relational_schema_analyzer" / "data" / "noun_lexicon.json.gz"
EXPECTED_VERSION = "0.2.3"


def _rows(resources: str, name: str) -> list[list[str]]:
    with gzip.open(os.path.join(resources, name)) as fh:
        return [r for r in csv.reader(io.TextIOWrapper(fh, "utf-8")) if len(r) >= 3]


def _word(w: str) -> bool:
    return w.isalpha() and w.isascii() and w.islower()


def build() -> dict:
    import importlib.metadata

    import lemminflect

    version = importlib.metadata.version("lemminflect")
    if version != EXPECTED_VERSION:
        sys.exit(f"built from LemmInflect {EXPECTED_VERSION}; installed {version}. Pin it or update EXPECTED_VERSION deliberately.")
    resources = os.path.join(os.path.dirname(lemminflect.__file__), "resources")
    lemma_rows = [r for r in _rows(resources, "lemma_lu.csv.gz") if r[1] == "noun"]
    infl_rows = [r for r in _rows(resources, "infl_lu.csv.gz") if r[1] == "noun"]

    plural_to_singular: dict[str, list[str]] = {}
    nouns: set[str] = set()
    for form, _, lemmas in lemma_rows:
        candidates = [x for x in lemmas.split("/") if _word(x)]
        if not _word(form) or not candidates:
            continue
        nouns.update(candidates)
        if candidates != [form]:
            plural_to_singular[form] = candidates
        else:
            nouns.add(form)
    singular_to_plural: dict[str, list[str]] = {}
    for lemma, _, forms in infl_rows:
        plurals = [x for x in forms.split("/") if _word(x)]
        if _word(lemma) and plurals:
            nouns.add(lemma)
            singular_to_plural[lemma] = plurals
    return {
        "format": 1,
        "source": f"LemmInflect {version} lemma_lu.csv.gz + infl_lu.csv.gz (MIT, Copyright (C) 2019 Brad Jascob)",
        "plural_to_singular": dict(sorted(plural_to_singular.items())),
        "singular_to_plural": dict(sorted(singular_to_plural.items())),
        # Nouns that are their own lemma and appear in neither map (news, cactus
        # as a lemma with no other listed form is already a key above).
        "lemmas_only": sorted(nouns - set(singular_to_plural) - {x for v in plural_to_singular.values() for x in v}),
    }


def main() -> None:
    table = build()
    raw = json.dumps(table, separators=(",", ":"), sort_keys=True).encode()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so the same input always produces the same bytes.
    OUT.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    print(f"{OUT.name}: {len(table['plural_to_singular'])} plurals, {len(table['singular_to_plural'])} singulars, "
          f"{len(table['lemmas_only'])} other nouns; {OUT.stat().st_size // 1024} KB; sha256 {hashlib.sha256(OUT.read_bytes()).hexdigest()[:16]}")


if __name__ == "__main__":
    main()
