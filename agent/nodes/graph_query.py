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

from rxgraph import neo4j_client
from agent.state import AgentState, Interaction, Contraindication


def query_interactions(state: AgentState) -> dict:
    """
    Node 3: fetch all relevant interactions and contraindications from Neo4j.
    """
    drug_names = state.get("normalized_drugs", [])

    if not drug_names:
        return {
            "graph_query_results": [],
            "contraindication_results": [],
        }

    interactions = _query_pairwise_interactions(drug_names)
    contraindications = _query_contraindications(
        drug_names,
        state.get("session_conditions", []),
    )

    return {
        "graph_query_results": interactions,
        "contraindication_results": contraindications,
    }


def _query_pairwise_interactions(drug_names: list[str]) -> list[Interaction]:
    """
    Find all INTERACTS_WITH relationships between any two drugs in the list.

    Uses undirected match -(r:INTERACTS_WITH)- because directionality in the
    graph reflects which drug's label was the source, not clinical direction.
    We deduplicate by storing seen (a, b) pairs (sorted) to avoid returning
    both (warfarin→aspirin) and (aspirin→warfarin) for the same relationship.
    """
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

    return interactions


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
