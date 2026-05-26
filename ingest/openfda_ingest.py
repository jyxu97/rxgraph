"""
OpenFDA ingestion script — Phase 1

Flow:
  1. For each drug in SEED_DRUGS, fetch its label record from OpenFDA.
  2. Extract the drug_interactions free-text field.
  3. Call GPT-4o-mini to parse (drug_a, drug_b, severity_if_stated, description) tuples.
  4. Write Drug nodes and INTERACTS_WITH relationships to Neo4j using MERGE.
  5. Save raw API responses to data/raw/ for audit and re-ingestion.

Design decisions:
  - GPT-4o-mini for extraction: extraction is a structured output task, not reasoning.
  - Severity stored as-is from source text; default "unknown" if not stated.
  - MERGE (not CREATE) prevents duplicate nodes on re-runs.
  - drug_a in each tuple is always the seed drug we queried.
  - Raw JSON saved locally (S3 is Phase 3).
"""

import json
import os
import time
from datetime import date
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

from rxgraph import cache as redis_cache
from seed_drugs import SEED_DRUGS

# Load .env from project root (parent of ingest/)
load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Configuration — read from environment variables
# ---------------------------------------------------------------------------

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "rxgraph_dev")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

OPENFDA_BASE = "https://api.fda.gov/drug/label.json"
RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw" / str(date.today())

# Pause between OpenFDA requests to stay well under the rate limit (240 req/min).
OPENFDA_DELAY_SECONDS = 0.3


# ---------------------------------------------------------------------------
# OpenFDA fetcher
# ---------------------------------------------------------------------------

def fetch_openfda_label(drug_name: str) -> dict | None:
    """
    Fetch the first label record for a drug from OpenFDA.
    Returns the raw result dict, or None if not found / error.

    Why search openfda.generic_name?
    OpenFDA normalizes generic names into the openfda.generic_name array.
    Searching there is more reliable than searching the raw label text for
    brand names or alternate spellings.
    """
    params = {
        "search": f'openfda.generic_name:"{drug_name}"',
        "limit": 1,
    }
    try:
        resp = requests.get(OPENFDA_BASE, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return results[0] if results else None
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            # OpenFDA returns 404 when a search has zero results — not a real error.
            return None
        print(f"  [HTTP error] {drug_name}: {e}")
        return None
    except Exception as e:
        print(f"  [fetch error] {drug_name}: {e}")
        return None


def save_raw(drug_name: str, record: dict) -> None:
    """Write raw API response to disk for audit trail."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = drug_name.replace(" ", "_").replace(".", "")
    path = RAW_DATA_DIR / f"{safe_name}.json"
    path.write_text(json.dumps(record, indent=2))


def source_id_exists(driver, source_id: str) -> bool:
    """
    Return True if any INTERACTS_WITH relationship with this source_id already
    exists in Neo4j.

    Why check before calling GPT?
    FDA label set_ids are stable identifiers — the same set_id means the same
    label document. If we've already extracted interactions from this document,
    calling GPT-4o-mini again would produce identical output and waste tokens.
    Skipping is safe because MERGE would overwrite with the same data anyway.
    """
    with driver.session() as session:
        result = session.run(
            "MATCH ()-[r:INTERACTS_WITH {source_id: $source_id}]->() "
            "RETURN count(r) AS n LIMIT 1",
            source_id=source_id,
        )
        return result.single()["n"] > 0


def fetch_labels_since(since_date: str, limit: int = 100) -> list[dict]:
    """
    Query OpenFDA for drug label records with effective_time after since_date.
    Returns a list of raw result dicts.

    Why effective_time?
    OpenFDA's effective_time is the date the label version became effective — it
    advances whenever the manufacturer submits a revised label. Filtering on this
    field catches genuinely new or revised labels since the last Lambda run.

    Date format: OpenFDA range queries use YYYYMMDD (no dashes).
    """
    yyyymmdd = since_date.replace("-", "")
    params = {
        "search": f"effective_time:[{yyyymmdd}+TO+20991231]",
        "limit": limit,
    }
    try:
        resp = requests.get(OPENFDA_BASE, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return []
        print(f"  [HTTP error] fetch_labels_since: {e}")
        return []
    except Exception as e:
        print(f"  [fetch error] fetch_labels_since: {e}")
        return []


# ---------------------------------------------------------------------------
# LLM-based interaction parser
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """
You are a medical data extraction assistant.
Given a drug interactions section from an FDA drug label, extract every
drug-drug interaction mentioned.

Return a JSON array. Each element must have exactly these fields:
  drug_b:      string  — the name of the interacting drug (generic preferred)
  severity:    string  — one of "major", "moderate", "minor", or "unknown"
                         Use "unknown" if the text does not explicitly state severity.
                         Never infer or guess severity.
  description: string  — one sentence summarizing the interaction from the source text

Rules:
  - drug_a is always the seed drug provided; do not include it in the output.
  - Only extract interactions explicitly stated in the text.
  - Do not invent or infer interactions not mentioned.
  - If no drug-drug interactions are described, return an empty array [].
  - Return only valid JSON, no markdown fences, no extra text.
"""


def parse_interactions(
    client: OpenAI,
    seed_drug: str,
    interactions_text: str,
) -> list[dict]:
    """
    Call GPT-4o-mini to extract structured interactions from free-text.
    Returns a list of dicts: [{drug_b, severity, description}, ...]

    Why GPT-4o-mini?
    This is a structured extraction task — pattern matching + entity recognition,
    not medical reasoning. GPT-4o-mini handles it reliably at ~10x lower cost
    than GPT-4o.
    """
    user_message = (
        f"Seed drug (drug_a): {seed_drug}\n\n"
        f"Drug interactions text:\n{interactions_text}"
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0,  # deterministic output for data extraction
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
        # The model may return {"interactions": [...]} or just [...]
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # look for the array under any top-level key
            for v in parsed.values():
                if isinstance(v, list):
                    return v
        return []
    except Exception as e:
        print(f"  [LLM parse error] {seed_drug}: {e}")
        return []


# ---------------------------------------------------------------------------
# Neo4j writer
# ---------------------------------------------------------------------------

def write_drug_node(tx, drug_name: str, record: dict) -> None:
    """
    Upsert a Drug node.

    Why MERGE and not CREATE?
    The ingestion script may be re-run (e.g. after a data refresh). MERGE ensures
    idempotency — a drug seen in two different label records won't create duplicates.
    SET updates properties on re-run so fresh data overwrites stale data.
    """
    openfda = record.get("openfda", {})
    brand_names = openfda.get("brand_name", [])
    drug_class = openfda.get("pharm_class_epc", [""])[0]  # established pharmacologic class

    tx.run(
        """
        MERGE (d:Drug {name: toLower($name)})
        SET d.brand_names = $brand_names,
            d.drug_class  = $drug_class,
            d.source      = 'openfda'
        """,
        name=drug_name.lower(),
        brand_names=[b.lower() for b in brand_names],
        drug_class=drug_class,
    )


def write_interaction(
    tx,
    drug_a: str,
    drug_b: str,
    severity: str,
    description: str,
    source_id: str,
) -> None:
    """
    Upsert both Drug nodes and the INTERACTS_WITH relationship between them.

    Why MERGE on the relationship?
    The same interaction pair (e.g. warfarin + aspirin) may appear in multiple
    FDA label records. We want one canonical edge, not duplicate edges.

    Why toLower() on drug names?
    Normalization happens in Phase 2 via RxNorm. For now, lowercase matching
    is the simplest deduplication we can do within the ingestion script.

    Note on directionality: INTERACTS_WITH is logically undirected (A risks B = B risks A),
    but Neo4j requires a direction for storage. We always store A→B where A is the
    seed drug. Queries use the undirected pattern -(r:INTERACTS_WITH)- to find both
    directions (see design-doc.md example Cypher queries).
    """
    valid_severities = {"major", "moderate", "minor", "unknown"}
    severity_clean = severity.lower() if severity.lower() in valid_severities else "unknown"

    tx.run(
        """
        MERGE (a:Drug {name: toLower($drug_a)})
        MERGE (b:Drug {name: toLower($drug_b)})
        MERGE (a)-[r:INTERACTS_WITH {source_id: $source_id}]->(b)
        SET r.severity    = $severity,
            r.description = $description
        """,
        drug_a=drug_a.lower(),
        drug_b=drug_b.lower(),
        severity=severity_clean,
        description=description,
        source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Main ingestion loop
# ---------------------------------------------------------------------------

def ingest(dry_run: bool = False) -> None:
    """
    Run the full ingestion pipeline.

    dry_run=True: fetch + parse but skip Neo4j writes (useful for testing the
    extraction logic without needing a running database).
    """
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    openai_client = OpenAI(api_key=OPENAI_API_KEY)

    driver = None
    if not dry_run:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        # Verify connectivity before processing hundreds of drugs
        driver.verify_connectivity()
        print(f"Connected to Neo4j at {NEO4J_URI}")

    total_drugs = 0
    total_interactions = 0
    total_skipped = 0
    not_found = []

    for drug_name in SEED_DRUGS:
        print(f"Processing: {drug_name}")

        record = fetch_openfda_label(drug_name)
        if record is None:
            print(f"  [not found] {drug_name}")
            not_found.append(drug_name)
            time.sleep(OPENFDA_DELAY_SECONDS)
            continue

        # Source ID: OpenFDA's set_id uniquely identifies the label document.
        source_id = record.get("set_id", f"openfda_{drug_name}")

        # Skip GPT call if this label version is already in Neo4j.
        if driver and source_id_exists(driver, source_id):
            print(f"  [skipped] {drug_name} — source_id already ingested")
            total_skipped += 1
            time.sleep(OPENFDA_DELAY_SECONDS)
            continue

        save_raw(drug_name, record)

        # The drug_interactions field is a list of strings in OpenFDA;
        # join them in case there are multiple paragraphs.
        interactions_text_list = record.get("drug_interactions", [])
        if not interactions_text_list:
            print(f"  [no interactions field] {drug_name}")
            # Still write the Drug node so it exists for normalization queries later.
            if driver:
                with driver.session() as session:
                    session.execute_write(write_drug_node, drug_name, record)
            total_drugs += 1
            time.sleep(OPENFDA_DELAY_SECONDS)
            continue

        interactions_text = " ".join(interactions_text_list)
        parsed = parse_interactions(openai_client, drug_name, interactions_text)

        if driver:
            with driver.session() as session:
                session.execute_write(write_drug_node, drug_name, record)
                for interaction in parsed:
                    drug_b = interaction.get("drug_b", "").strip()
                    if not drug_b:
                        continue
                    session.execute_write(
                        write_interaction,
                        drug_a=drug_name,
                        drug_b=drug_b,
                        severity=interaction.get("severity", "unknown"),
                        description=interaction.get("description", ""),
                        source_id=source_id,
                    )
                    total_interactions += 1

        print(f"  -> {len(parsed)} interactions extracted")
        total_drugs += 1
        time.sleep(OPENFDA_DELAY_SECONDS)

    if driver:
        driver.close()

    if not dry_run:
        deleted = redis_cache.flush_interaction_cache()
        print(f"Redis cache flushed:   {deleted} keys invalidated")

    print("\n--- Ingestion complete ---")
    print(f"Drugs processed:       {total_drugs}")
    print(f"Interactions written:  {total_interactions}")
    print(f"Skipped (existing):    {total_skipped}")
    print(f"Not found in OpenFDA:  {len(not_found)}")
    if not_found:
        print(f"  {not_found}")


def ingest_since(since_date: str, dry_run: bool = False) -> dict:
    """
    Incremental ingestion for Lambda. Queries OpenFDA for labels with
    effective_time after since_date, skips source_ids already in Neo4j,
    and writes new interactions to the graph.

    Called by lambda/handler.py on a weekly EventBridge schedule.
    Returns a stats dict so the Lambda can log and return structured results.

    Why separate from ingest()?
    ingest() is seeded from a curated drug list — it's for initial population.
    ingest_since() is date-driven — it discovers whatever FDA has updated since
    the last run, independent of the seed list. This catches label revisions and
    newly submitted drugs beyond the original 96 seeds.
    """
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    openai_client = OpenAI(api_key=OPENAI_API_KEY)

    driver = None
    if not dry_run:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        print(f"Connected to Neo4j at {NEO4J_URI}")

    stats = {
        "drugs_processed": 0,
        "interactions_written": 0,
        "skipped_existing": 0,
        "not_found": 0,
    }

    records = fetch_labels_since(since_date)
    print(f"Found {len(records)} FDA labels updated since {since_date}")

    for record in records:
        openfda = record.get("openfda", {})
        generic_names = openfda.get("generic_name", [])
        drug_name = generic_names[0].lower() if generic_names else None
        if not drug_name:
            continue

        source_id = record.get("set_id", f"openfda_{drug_name}")

        if driver and source_id_exists(driver, source_id):
            print(f"  [skipped] {drug_name} — already ingested")
            stats["skipped_existing"] += 1
            continue

        save_raw(drug_name, record)

        interactions_text_list = record.get("drug_interactions", [])
        if not interactions_text_list:
            if driver:
                with driver.session() as session:
                    session.execute_write(write_drug_node, drug_name, record)
            stats["drugs_processed"] += 1
            time.sleep(OPENFDA_DELAY_SECONDS)
            continue

        interactions_text = " ".join(interactions_text_list)
        parsed = parse_interactions(openai_client, drug_name, interactions_text)

        if driver:
            with driver.session() as session:
                session.execute_write(write_drug_node, drug_name, record)
                for interaction in parsed:
                    drug_b = interaction.get("drug_b", "").strip()
                    if not drug_b:
                        continue
                    session.execute_write(
                        write_interaction,
                        drug_a=drug_name,
                        drug_b=drug_b,
                        severity=interaction.get("severity", "unknown"),
                        description=interaction.get("description", ""),
                        source_id=source_id,
                    )
                    stats["interactions_written"] += 1

        print(f"  -> {len(parsed)} interactions from {drug_name}")
        stats["drugs_processed"] += 1
        time.sleep(OPENFDA_DELAY_SECONDS)

    if driver:
        driver.close()

    if not dry_run:
        deleted = redis_cache.flush_interaction_cache()
        print(f"Redis cache flushed: {deleted} keys invalidated")

    print(f"\n--- Incremental sync complete --- {stats}")
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest OpenFDA drug interactions into Neo4j")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse data but skip Neo4j writes",
    )
    args = parser.parse_args()

    ingest(dry_run=args.dry_run)
