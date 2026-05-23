"""
RxNorm normalization utility — Phase 1

Given any drug name (brand, generic, misspelling), resolve it to the
canonical generic name using the NIH RxNorm API.

Used in two places:
  - Phase 1 ingestion: normalize drug_b names extracted by the LLM
  - Phase 2 agent Node 2: normalize user-supplied drug names before Neo4j lookup

No API key required.
"""

import time

import requests

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
REQUEST_TIMEOUT = 5   # seconds
MAX_RETRIES = 2
RETRY_DELAY = 1.0     # seconds between retries


def normalize(drug_name: str) -> str | None:
    """
    Resolve a drug name to its canonical generic name via RxNorm.

    Returns the generic name string (lowercase), or None if RxNorm
    cannot resolve the input.

    Two-step lookup:
      Step 1 — exact: /rxcui.json?name=<drug>  →  get RxCUI
                      /rxcui/{rxcui}/related.json?tty=IN  →  get ingredient name
      Step 2 — approximate (fallback for misspellings):
                      /approximateTerm.json?term=<drug>  →  ranked RxCUI candidates
                      resolve each candidate to ingredient as above

    Why two steps instead of parsing /drugs.json concept groups?
    /rxcui.json returns a single canonical ID regardless of whether the input
    is a brand name or generic name. Resolving that ID to tty=IN (ingredient)
    is then a direct lookup. The /drugs.json approach requires parsing
    concept group arrays and is brittle when groups are empty or missing.
    """
    name = drug_name.strip()
    if not name:
        return None

    # Step 1: exact match
    rxcui = _get_rxcui_exact(name)
    if rxcui:
        generic = _rxcui_to_ingredient(rxcui)
        if generic:
            return generic

    # Step 2: approximate match (handles misspellings, alternate names)
    return _approximate_match(name)


def _get_rxcui_exact(name: str) -> str | None:
    """
    Look up the RxCUI for an exact drug name.
    Works for both brand names ("Advil") and generic names ("ibuprofen").
    Returns the RxCUI string, or None if not found.
    """
    data = _get(f"{RXNORM_BASE}/rxcui.json", {"name": name})
    if data is None:
        return None
    ids = data.get("idGroup", {}).get("rxnormId", [])
    return ids[0] if ids else None


def _rxcui_to_ingredient(rxcui: str) -> str | None:
    """
    Given any RxCUI (brand or otherwise), find its ingredient (generic) name.

    Calls /rxcui/{rxcui}/related.json?tty=IN which follows RxNorm's
    relationships to return the ingredient-level concept — the generic name.

    Why tty=IN (ingredient)?
    RxNorm has many term types: brand names (BN), clinical dose forms (SCD),
    branded dose forms (SBD), packs, etc. "IN" (ingredient) is the canonical
    generic name level — the granularity we store in Neo4j. Without filtering
    to IN, we'd get strings like "ibuprofen 200 MG Oral Tablet".
    """
    data = _get(
        f"{RXNORM_BASE}/rxcui/{rxcui}/related.json",
        {"tty": "IN"},
    )
    if data is None:
        return None

    groups = data.get("relatedGroup", {}).get("conceptGroup", [])
    for group in groups:
        concepts = group.get("conceptProperties", [])
        if concepts:
            return concepts[0]["name"].lower()
    return None


def _approximate_match(name: str) -> str | None:
    """
    Fuzzy search via RxNorm's approximateTerm endpoint.
    Returns the first candidate that resolves to a generic ingredient name.

    Note: the parameter is 'term', not 'name' — different from other endpoints.
    """
    data = _get(
        f"{RXNORM_BASE}/approximateTerm.json",
        {"term": name, "maxEntries": 5},
    )
    if data is None:
        return None

    candidates = data.get("approximateGroup", {}).get("candidate", [])
    for candidate in candidates:
        rxcui = candidate.get("rxcui")
        if rxcui:
            generic = _rxcui_to_ingredient(rxcui)
            if generic:
                return generic
    return None


def _get(url: str, params: dict) -> dict | None:
    """
    HTTP GET with retries. Returns parsed JSON or None on failure.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                print(f"  [rxnorm timeout] {url} params={params}")
                return None
        except Exception as e:
            print(f"  [rxnorm error] {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# Quick manual test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_cases = [
        ("Advil", "ibuprofen"),
        ("Tylenol", "acetaminophen"),
        ("Lipitor", "atorvastatin"),
        ("warfarin", "warfarin"),       # already generic
        ("ibuprofin", "ibuprofen"),     # misspelling
        ("asdfxyz123", None),           # unknown drug
    ]

    print(f"{'Input':<20} {'Expected':<20} {'Got':<20} {'Pass'}")
    print("-" * 70)
    for name, expected in test_cases:
        got = normalize(name)
        passed = "YES" if got == expected else "NO"
        print(f"{name:<20} {str(expected):<20} {str(got):<20} {passed}")
