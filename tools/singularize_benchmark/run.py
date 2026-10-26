"""Score singularization techniques against the answer key and the held-out set.

    python tools/singularize_benchmark/run.py            # RSA's singularize only
    pip install inflect inflection lemminflect nltk PyStemmer
    python -c "import nltk; nltk.download('wordnet')"
    python tools/singularize_benchmark/run.py --all      # and every comparison library

Results recorded 2026-10-23 (before the dictionary layer) and the design they
led to: arango-solutions-org/r2g-arango#30. Stemmers (Snowball, which
ArangoSearch's ``stem`` and ``text`` analyzers use) return stems such as
``warehous`` / ``categori``, so they cannot name a class.
"""

from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

from heldout import HELD  # noqa: E402
from oracle import CASES  # noqa: E402


def _rsa():
    from relational_schema_analyzer.naming import singularize
    return singularize


def _optional():
    out = {}
    try:
        import inflect
        e = inflect.engine()
        out["inflect"] = lambda w: e.singular_noun(w) or w
    except ImportError:
        pass
    try:
        import inflection
        out["inflection (Rails rules)"] = inflection.singularize
    except ImportError:
        pass
    try:
        from lemminflect import getLemma
        out["lemminflect"] = lambda w: (getLemma(w, upos="NOUN") or (w,))[0]
    except ImportError:
        pass
    try:
        from nltk.stem import WordNetLemmatizer
        lem = WordNetLemmatizer()
        lem.lemmatize("tests", "n")
        out["WordNet (NLTK)"] = lambda w: lem.lemmatize(w, "n")
    except (ImportError, LookupError):
        pass
    try:
        import Stemmer
        out["Snowball stemmer (= ArangoSearch stem)"] = Stemmer.Stemmer("english").stemWord
    except ImportError:
        pass
    return out


def _ok(got: str, want) -> bool:
    return got in want if isinstance(want, tuple) else got == want


def score(fn):
    dev = [(w, fn(w), want) for c in CASES.values() for w, want in c.items()]
    held = [(w, fn(w), want) for w, want in HELD.items()]
    return dev, held


def main() -> None:
    techniques = {"relational-schema-analyzer singularize": _rsa()}
    if "--all" in sys.argv:
        techniques.update(_optional())
    for name, fn in techniques.items():
        dev, held = score(fn)
        dm = [f"{w}->{g}" for w, g, want in dev if not _ok(g, want)]
        hm = [f"{w}->{g}" for w, g, want in held if not _ok(g, want)]
        print(f"{name:42} answer key {len(dev) - len(dm)}/{len(dev)}   held-out {len(held) - len(hm)}/{len(held)}")
        if "--misses" in sys.argv:
            print(f"   misses: {dm + hm}")


if __name__ == "__main__":
    main()
