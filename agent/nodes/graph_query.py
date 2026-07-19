"""
Node 3: Direct Interaction + Contraindication Query

Runs two Cypher queries against Neo4j:
  1. Pairwise INTERACTS_WITH between all normalized drugs
  2. CONTRAINDICATED_WITH and USE_WITH_CAUTION_IN for drugs × conditions

Why pass session context as Cypher parameters ($drug_names, $conditions)?
Session context (conditions, allergies) is never persisted to Neo4j — it's
in-memory for this query only. Passing it as parameters keeps sensitive health
context out of the graph entirely, while still using it to shape the results.

Why one query for interactions instead of N*(N-1) individual queries?
The WHERE clause filters both ends of the relationship in one traversal.
Neo4j handles this efficiently with index lookups on Drug.name. A separate
query per pair would multiply the round-trips without adding expressiveness.
"""

import logging
import time

from rxgraph import cache, neo4j_client
from agent.state import AgentState, Interaction, Contraindication

logger = logging.getLogger(__name__)


def query_interactions(state: AgentState) -> dict:
    """
    Node 3: fetch all relevant interactions and contraindications from Neo4j.
    """
    t0 = time.perf_counter()
    drug_names = state.get("normalized_drugs", [])

    if not drug_names:
        trace_entry = {
            "node": "graph_query",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "cache_hit": False,
            "fallback_used": False,
            "interactions_found": 0,
            "contraindications_found": 0,
        }
        return {
            "graph_query_results": [],
            "contraindication_results": [],
            "trace": state.get("trace", []) + [trace_entry],
        }

    interactions, cache_hit = _query_pairwise_interactions(drug_names)
    fallback_used = False
    if not interactions:
        logger.info("class fallback triggered drugs=[%s]", ", ".join(sorted(drug_names)))
        interactions, _ = _query_class_fallback(drug_names)
        fallback_used = bool(interactions)

    contraindications = _query_contraindications(
        drug_names,
        state.get("session_conditions", []),
    )

    trace_entry = {
        "node": "graph_query",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "cache_hit": cache_hit,
        "fallback_used": fallback_used,
        "interactions_found": len(interactions),
        "contraindications_found": len(contraindications),
    }
    return {
        "graph_query_results": interactions,
        "contraindication_results": contraindications,
        "trace": state.get("trace", []) + [trace_entry],
    }


def _query_pairwise_interactions(
    drug_names: list[str],
) -> tuple[list[Interaction], bool]:
    """
    Find all INTERACTS_WITH relationships between any two drugs in the list.

    Uses undirected match -(r:INTERACTS_WITH)- because directionality in the
    graph reflects which drug's label was the source, not clinical direction.
    We deduplicate by storing seen (a, b) pairs (sorted) to avoid returning
    both (warfarin→aspirin) and (aspirin→warfarin) for the same relationship.

    Results are cached in Redis (TTL 24 h) keyed on the sorted drug name set.

    Returns (interactions, cache_hit) so the caller can surface cache_hit in
    the per-request trace.
    """
    drugs_label = ", ".join(sorted(drug_names))
    key = cache.make_key("interactions", drug_names)
    cached = cache.cache_get(key)
    if cached is not None:
        logger.info("cache hit  interactions drugs=[%s] key=%s", drugs_label, key)
        return [Interaction(**item) for item in cached], True

    logger.info("cache miss interactions drugs=[%s] key=%s → querying Neo4j", drugs_label, key)
    driver = neo4j_client.get_driver()
    interactions: list[Interaction] = []
    seen: set[tuple] = set()

    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
            WHERE a.name IN $drug_names AND b.name IN $drug_names
            RETURN a.name AS drug_a,
                   b.name AS drug_b,
                   r.severity AS severity,
                   r.description AS description,
                   r.source_id AS source_id
            """,
            drug_names=drug_names,
        )
        for record in result:
            pair = tuple(sorted([record["drug_a"], record["drug_b"]]))
            if pair in seen:
                continue
            seen.add(pair)
            interactions.append(
                Interaction(
                    drug_a=record["drug_a"],
                    drug_b=record["drug_b"],
                    severity=record["severity"] or "unknown",
                    description=record["description"] or "",
                    source_id=record["source_id"] or "",
                )
            )

    logger.info("neo4j      interactions drugs=[%s] found=%d", drugs_label, len(interactions))
    cache.cache_set(key, [dict(i) for i in interactions])
    return interactions, False


def _query_class_fallback(
    drug_names: list[str],
) -> tuple[list[Interaction], bool]:
    """
    When no direct INTERACTS_WITH edges exist, check if any pair of drugs
    shares the same pharmacological class (drug_class string property).

    Returns a warning-level entry so the user knows to investigate further.
    source_id uses "class-inference:{class}" — non-empty so it passes the
    safety guardrail, but clearly not an FDA source record.

    Results are cached in Redis (TTL 24 h) under the "class-fallback" namespace.

    Returns (interactions, cache_hit).
    """
    drugs_label = ", ".join(sorted(drug_names))
    key = cache.make_key("class-fallback", drug_names)
    cached = cache.cache_get(key)
    if cached is not None:
        logger.info("cache hit  class-fallback drugs=[%s] key=%s", drugs_label, key)
        return [Interaction(**item) for item in cached], True

    logger.info("cache miss class-fallback drugs=[%s] key=%s → querying Neo4j", drugs_label, key)
    driver = neo4j_client.get_driver()
    results: list[Interaction] = []

    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:Drug), (b:Drug)
            WHERE a.name IN $drug_names AND b.name IN $drug_names
              AND a.name < b.name
              AND a.drug_class IS NOT NULL AND a.drug_class <> ''
              AND a.drug_class = b.drug_class
            RETURN a.name AS drug_a, b.name AS drug_b, a.drug_class AS drug_class
            """,
            drug_names=drug_names,
        )
        for record in result:
            results.append(
                Interaction(
                    drug_a=record["drug_a"],
                    drug_b=record["drug_b"],
                    severity="unknown",
                    description=(
                        f"No direct interaction found in database. "
                        f"Both are {record['drug_class']}. "
                        f"Same-class combinations may have additive effects — consult a pharmacist."
                    ),
                    source_id=f"class-inference:{record['drug_class']}",
                )
            )

    logger.info("neo4j      class-fallback drugs=[%s] found=%d", drugs_label, len(results))
    cache.cache_set(key, [dict(r) for r in results])
    return results, False


def _query_contraindications(
    drug_names: list[str],
    conditions: list[str],
) -> list[Contraindication]:
    """
    Find CONTRAINDICATED_WITH and USE_WITH_CAUTION_IN matches.

    Note: this will return empty results in Phase 1 — we haven't ingested
    Condition nodes or those relationships yet. The query is correct; the
    data will be populated in Phase 2 (DrugBank) or via manual seeding.
    """
    if not conditions:
        return []

    driver = neo4j_client.get_driver()
    contraindications: list[Contraindication] = []

    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Drug)-[:CONTRAINDICATED_WITH]->(c:Condition)
            WHERE d.name IN $drug_names
              AND toLower(c.name) IN $conditions
            RETURN d.name AS drug, c.name AS condition,
                   'contraindicated' AS risk_type

            UNION

            MATCH (d:Drug)-[:USE_WITH_CAUTION_IN]->(c:Condition)
            WHERE d.name IN $drug_names
              AND toLower(c.name) IN $conditions
            RETURN d.name AS drug, c.name AS condition,
                   'use with caution' AS risk_type
            """,
            drug_names=drug_names,
            conditions=[c.lower() for c in conditions],
        )
        for record in result:
            contraindications.append(
                Contraindication(
                    drug=record["drug"],
                    condition=record["condition"],
                    risk_type=record["risk_type"],
                )
            )

    return contraindications
