"""Hand-labelled answer key for singularizing a table name's last word.

Labelled from English usage, not from any tool under test (2026-10-23). The
expected answer is the singular a modeller would give the class for a table of
such rows. A tuple means the word is genuinely ambiguous and either is right.
"""

CASES: dict[str, dict[str, object]] = {
    "regular -s": {
        "accounts": "account", "orders": "order", "customers": "customer",
        "products": "product", "invoices": "invoice", "devices": "device",
        "services": "service", "prices": "price", "sources": "source",
        "resources": "resource", "choices": "choice", "offices": "office",
        "balances": "balance", "sessions": "session", "payments": "payment",
        "metrics": "metric", "surveys": "survey", "keys": "key", "holidays": "holiday",
        "photos": "photo", "pianos": "piano", "menus": "menu", "kiwis": "kiwi",
        "taxis": "taxi", "settings": "setting", "preferences": "preference",
        "permissions": "permission", "details": "detail", "terms": "term",
        "results": "result", "requirements": "requirement", "credentials": "credential",
        "specs": "spec",
    },
    "-e words (keep the e)": {
        "warehouses": "warehouse", "courses": "course", "cases": "case",
        "purchases": "purchase", "databases": "database", "responses": "response",
        "releases": "release", "licenses": "license", "phases": "phase",
        "houses": "house", "sizes": "size", "prizes": "prize", "caches": "cache",
        "niches": "niche", "uses": "use", "causes": "cause", "clauses": "clause",
        "horses": "horse", "nurses": "nurse", "expenses": "expense",
        "archives": "archive", "objectives": "objective", "drives": "drive",
        "gloves": "glove", "curves": "curve", "reserves": "reserve",
        "shoes": "shoe", "toes": "toe",
    },
    "sibilant -es": {
        "addresses": "address", "classes": "class", "processes": "process",
        "businesses": "business", "boxes": "box", "taxes": "tax", "indexes": "index",
        "batches": "batch", "matches": "match", "branches": "branch",
        "searches": "search", "dishes": "dish", "wishes": "wish", "buzzes": "buzz",
        "fixes": "fix", "quizzes": "quiz",
    },
    "singular ending in -s": {
        "status": "status", "bus": "bus", "campus": "campus", "virus": "virus",
        "alias": "alias", "bonus": "bonus", "census": "census", "lens": "lens",
        "gas": "gas", "canvas": "canvas", "atlas": "atlas", "cactus": "cactus",
        "genus": "genus", "octopus": "octopus", "radius": "radius",
        "stimulus": "stimulus", "syllabus": "syllabus", "thesaurus": "thesaurus",
        "nexus": "nexus", "apparatus": "apparatus", "prospectus": "prospectus",
        "analysis": "analysis", "crisis": "crisis", "thesis": "thesis",
        "hypothesis": "hypothesis", "diagnosis": "diagnosis", "basis": "basis",
        "address": "address", "process": "process", "business": "business",
        "access": "access",
    },
    "plural of an -s singular": {
        "statuses": "status", "buses": "bus", "campuses": "campus",
        "viruses": "virus", "aliases": "alias", "bonuses": "bonus",
        "censuses": "census", "lenses": "lens", "gases": "gas",
        "canvases": "canvas", "atlases": "atlas", "analyses": "analysis",
        "crises": "crisis", "theses": "thesis", "hypotheses": "hypothesis",
        "diagnoses": "diagnosis",
    },
    "-y -> -ies": {
        "categories": "category", "companies": "company",
        "opportunities": "opportunity", "territories": "territory", "cities": "city",
        "entities": "entity", "activities": "activity", "properties": "property",
        "policies": "policy", "countries": "country", "inventories": "inventory",
    },
    "-ie -> -ies": {
        "movies": "movie", "cookies": "cookie", "calories": "calorie",
        "brownies": "brownie", "zombies": "zombie", "pies": "pie", "ties": "tie",
        "selfies": "selfie",
    },
    "-f/-fe -> -ves": {
        "knives": "knife", "wives": "wife", "lives": "life", "halves": "half",
        "shelves": "shelf", "wolves": "wolf", "loaves": "loaf",
    },
    "-o -> -oes": {
        "heroes": "hero", "potatoes": "potato", "tomatoes": "tomato", "echoes": "echo",
    },
    "irregular / classical": {
        "people": "person", "children": "child", "men": "man", "women": "woman",
        "mice": "mouse", "geese": "goose", "feet": "foot", "teeth": "tooth",
        "oxen": "ox", "criteria": "criterion", "phenomena": "phenomenon",
        "matrices": "matrix", "vertices": "vertex", "indices": "index",
        "appendices": "appendix", "alumni": "alumnus", "curricula": "curriculum",
        "cacti": "cactus", "radii": "radius", "stimuli": "stimulus",
        "genera": "genus", "schemata": "schema",
    },
    "same in both forms (leave alone)": {
        "news": "news", "data": "data", "metadata": "metadata",
        "information": "information", "equipment": "equipment",
        "software": "software", "feedback": "feedback", "series": "series",
        "species": "species", "analytics": "analytics", "logistics": "logistics",
        "economics": "economics", "physics": "physics", "mathematics": "mathematics",
        "headquarters": "headquarters", "savings": "savings", "earnings": "earnings",
        "goods": "goods", "clothes": "clothes", "means": "means",
        "aircraft": "aircraft", "sheep": "sheep", "deer": "deer", "staff": "staff",
        "personnel": "personnel", "kudos": "kudos",
    },
    "ambiguous (either reading)": {
        "sales": ("sale", "sales"), "statistics": ("statistic", "statistics"),
        "media": ("medium", "media"), "contents": ("content", "contents"),
        "premises": ("premise", "premises"), "glasses": ("glass", "glasses"),
        "bases": ("base", "basis"), "axes": ("axe", "axis"), "leaves": ("leaf", "leave"),
        "customs": ("custom", "customs"), "operations": ("operation", "operations"),
    },
}
