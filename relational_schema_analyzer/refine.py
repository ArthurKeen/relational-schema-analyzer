"""Optional LLM refinement of the deterministic baseline (Phase 4).

The relational baseline is already a complete, high-quality conceptual model, so the
LLM's job here is **refinement, not generation**: it may relabel entities /
relationships with better semantic names and attach hints (natural-language
description, embed-vs-link, n-ary recognition). It deliberately **cannot** invent or
drop structure — the workflow only honors renames/hints for elements that already
exist in the baseline, validates them (types, no name collisions), and applies them
to safe copies. Any provider error or unrepairable output leaves the baseline
untouched (the caller falls back to it).

Refinement JSON contract (LLM output):

    {
      "entities": { "<currentName>": {"name": "<NewName>"?, "description": str?,
                                      "embedInto": "<EntityName>"?} },
      "relationships": { "<currentType>": {"type": "<NewType>"?, "description": str?,
                                           "embed": bool?, "nary": bool?} }
    }
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .providers.base import LLMError, LLMProvider

SYSTEM_PROMPT = (
    "You refine a relational->conceptual schema. You are given entities and "
    "relationships already inferred deterministically. Improve ONLY their semantic "
    "naming and add hints. You MUST NOT invent, remove, or restructure elements. "
    "Return ONLY a JSON object (no markdown) of the form "
    '{"entities": {"<currentName>": {"name": "<NewName>", "description": "...", '
    '"embedInto": "<EntityName>"}}, "relationships": {"<currentType>": '
    '{"type": "<NewType>", "description": "...", "embed": true, "nary": false}}}. '
    "Only include keys for elements you want to change; omit the rest. Names must be "
    "unique. Use PascalCase for entities and a readable identifier for relationships."
)


def _extract_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError("no JSON object found in LLM output", code="PARSE_ERROR")
    try:
        data = json.loads(s[start : end + 1])
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"LLM output was not valid JSON: {e}", code="PARSE_ERROR") from e
    if not isinstance(data, dict):
        raise LLMError("LLM output must be a JSON object", code="PARSE_ERROR")
    return data


def build_prompt(conceptual: dict[str, Any], physical_mapping: dict[str, Any]) -> str:
    pm_entities = physical_mapping.get("entities", {})
    lines = ["Entities (currentName <- table): properties"]
    for e in conceptual.get("entities", []):
        table = (pm_entities.get(e["name"]) or {}).get("tableName", "?")
        props = ", ".join(p.get("name", "") for p in e.get("properties", []))
        lines.append(f"- {e['name']} <- {table}: {props}")
    lines.append("")
    lines.append("Relationships (currentType: from -> to [cardinality, style]):")
    pm_rels = physical_mapping.get("relationships", {})
    for r in conceptual.get("relationships", []):
        style = (pm_rels.get(r["type"]) or {}).get("style", "?")
        card = r.get("cardinality", "?")
        lines.append(
            f"- {r['type']}: {r.get('fromEntity')} -> {r.get('toEntity')} [{card}, {style}]"
        )
    lines.append("")
    lines.append(
        "Return the refinement JSON. Rename only where a clearer name is warranted; "
        "add embedInto/embed hints where a child clearly belongs to a parent; set "
        "nary=true for associative relationships joining more than two entities."
    )
    return "\n".join(lines)


def validate_refinement(
    data: dict[str, Any], entity_names: list[str], rel_names: list[str]
) -> list[str]:
    errors: list[str] = []
    ent = data.get("entities", {})
    rel = data.get("relationships", {})
    if not isinstance(ent, dict):
        errors.append("'entities' must be an object")
        ent = {}
    if not isinstance(rel, dict):
        errors.append("'relationships' must be an object")
        rel = {}

    def _check(block: dict[str, Any], names: list[str], key: str, label: str) -> None:
        final: dict[str, int] = {}
        current = set(names)
        # start from every element's current name, override with valid renames
        for n in names:
            final[n] = final.get(n, 0) + 1
        for k, v in block.items():
            if k not in current:
                continue  # unknown element names are ignored, not errors
            if not isinstance(v, dict):
                errors.append(f"{label} '{k}' refinement must be an object")
                continue
            new = v.get(key)
            if new is not None:
                if not isinstance(new, str) or not new.strip():
                    errors.append(f"{label} '{k}' has an invalid new {key}")
                    continue
                final[k] -= 1  # this element no longer occupies its old name
                final[new] = final.get(new, 0) + 1
        for name, count in final.items():
            if count > 1:
                errors.append(f"{label} name collision on '{name}'")

    _check(ent, entity_names, "name", "entity")
    _check(rel, rel_names, "type", "relationship")
    return errors


def apply_refinement(
    conceptual: dict[str, Any], physical_mapping: dict[str, Any], data: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    """Apply validated renames/hints to deep copies; return (conceptual, pm, counts)."""
    c = copy.deepcopy(conceptual)
    pm = copy.deepcopy(physical_mapping)
    ent_ref = data.get("entities", {}) if isinstance(data.get("entities"), dict) else {}
    rel_ref = data.get("relationships", {}) if isinstance(data.get("relationships"), dict) else {}

    entity_rename: dict[str, str] = {}
    entities_refined = 0
    for e in c.get("entities", []):
        ref = ent_ref.get(e["name"])
        if not isinstance(ref, dict):
            continue
        entities_refined += 1
        e["source"] = "llm"
        if isinstance(ref.get("description"), str):
            e["description"] = ref["description"]
        if isinstance(ref.get("embedInto"), str) and ref["embedInto"].strip():
            e["embedInto"] = ref["embedInto"]
        new = ref.get("name")
        if isinstance(new, str) and new.strip() and new != e["name"]:
            entity_rename[e["name"]] = new
            e["name"] = new
            e["labels"] = [new]

    if entity_rename:
        pm_entities = pm.get("entities", {})
        pm["entities"] = {entity_rename.get(k, k): v for k, v in pm_entities.items()}
        for e in c.get("entities", []):
            if isinstance(e.get("subClassOf"), str):
                e["subClassOf"] = entity_rename.get(e["subClassOf"], e["subClassOf"])
        for r in c.get("relationships", []):
            r["fromEntity"] = entity_rename.get(r.get("fromEntity"), r.get("fromEntity"))
            r["toEntity"] = entity_rename.get(r.get("toEntity"), r.get("toEntity"))

    rel_rename: dict[str, str] = {}
    rels_refined = 0
    for r in c.get("relationships", []):
        ref = rel_ref.get(r["type"])
        if not isinstance(ref, dict):
            continue
        rels_refined += 1
        r["source"] = "llm"
        if isinstance(ref.get("description"), str):
            r["description"] = ref["description"]
        if isinstance(ref.get("embed"), bool):
            r["embed"] = ref["embed"]
        if isinstance(ref.get("nary"), bool):
            r["nary"] = ref["nary"]
        new = ref.get("type")
        if isinstance(new, str) and new.strip() and new != r["type"]:
            rel_rename[r["type"]] = new
            r["type"] = new

    if rel_rename:
        pm_rels = pm.get("relationships", {})
        pm["relationships"] = {rel_rename.get(k, k): v for k, v in pm_rels.items()}

    return c, pm, {"entitiesRefined": entities_refined, "relationshipsRefined": rels_refined}


def _repair_prompt(errors: list[str], previous: str) -> str:
    errs = "\n".join(f"- {e}" for e in errors) or "- (unknown)"
    return (
        "Your previous refinement JSON was invalid. Fix these errors:\n"
        f"{errs}\n\nPrevious output:\n{previous}\n\n"
        "Return ONLY the corrected JSON object. No markdown, no extra text."
    )


def refine(
    conceptual: dict[str, Any],
    physical_mapping: dict[str, Any],
    *,
    provider: LLMProvider,
    model: str,
    timeout_ms: int,
    max_repair_attempts: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    """Generate -> parse -> validate -> repair; apply on success. Raises on failure."""
    entity_names = [e["name"] for e in conceptual.get("entities", [])]
    rel_names = [r["type"] for r in conceptual.get("relationships", [])]
    prompt = build_prompt(conceptual, physical_mapping)
    attempts = 0
    while True:
        resp = provider.generate(
            model=model, system=SYSTEM_PROMPT, prompt=prompt, timeout_ms=timeout_ms
        )
        try:
            data = _extract_json(resp.text)
            errors = validate_refinement(data, entity_names, rel_names)
        except LLMError as e:
            data, errors = {}, [str(e)]
        if not errors:
            c, pm, counts = apply_refinement(conceptual, physical_mapping, data)
            return c, pm, {"repairAttempts": attempts, **counts}
        if attempts >= max_repair_attempts:
            raise LLMError(
                "refinement failed validation after repairs: " + "; ".join(errors),
                code="VALIDATION_ERROR",
            )
        attempts += 1
        prompt = _repair_prompt(errors, resp.text or "")
