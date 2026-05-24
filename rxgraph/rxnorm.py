"""
RxNorm normalization utility.

Given any drug name (brand, generic, misspelling), resolve it to the
canonical generic name using the NIH RxNorm API.

Used in two places:
  - ingest/openfda_ingest.py: normalize drug_b names extracted by the LLM
  - agent/nodes/normalization.py: normalize user-supplied names before Neo4j lookup

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
    """
    name = drug_name.strip()
    if not name:
        return None

    rxcui = _get_rxcui_exact(name)
    if rxcui:
        generic = _rxcui_to_ingredient(rxcui)
        if generic:
            return generic

    return _approximate_match(name)


def _get_rxcui_exact(name: str) -> str | None:
    data = _get(f"{RXNORM_BASE}/rxcui.json", {"name": name})
    if data is None:
        return None
    ids = data.get("idGroup", {}).get("rxnormId", [])
    return ids[0] if ids else None


def _rxcui_to_ingredient(rxcui: str) -> str | None:
    data = _get(f"{RXNORM_BASE}/rxcui/{rxcui}/related.json", {"tty": "IN"})
    if data is None:
        return None
    groups = data.get("relatedGroup", {}).get("conceptGroup", [])
    for group in groups:
        concepts = group.get("conceptProperties", [])
        if concepts:
            return concepts[0]["name"].lower()
    return None


def _approximate_match(name: str) -> str | None:
    data = _get(f"{RXNORM_BASE}/approximateTerm.json", {"term": name, "maxEntries": 5})
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
