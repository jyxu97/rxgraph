"""
Node 2: Drug Name Normalization

For each extracted drug name:
  1. Try exact match in Neo4j on Drug.name (already lowercase)
  2. Try match on Drug.brand_names array
  3. Fall back to RxNorm → resolve to generic → retry Neo4j
  4. If still not found: add to unrecognized_drugs

Why Neo4j-first, RxNorm-second?
The graph may contain drugs under a name RxNorm would resolve differently.
Checking the graph first avoids an unnecessary API call for names we already have.
RxNorm is only called when the graph lookup fails.
"""

import time

from rxgraph import neo4j_client, rxnorm
from agent.state import AgentState


def _find_in_graph(name: str) -> str | None:
    """
    Look up a drug name in Neo4j. Checks both .name and .brand_names.
    Returns the canonical graph name (lowercase) if found, else None.
    """
    driver = neo4j_client.get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Drug)
            WHERE d.name = toLower($name)
               OR toLower($name) IN [b IN d.brand_names | toLower(b)]
            RETURN d.name
            LIMIT 1
            """,
            name=name,
        )
        record = result.single()
        return record["d.name"] if record else None


def normalize_drugs(state: AgentState) -> dict:
    """
    Node 2: resolve raw drug names to canonical graph names.

    Populates:
      normalized_drugs   — names confirmed to exist in Neo4j
      unrecognized_drugs — names that couldn't be resolved
    """
    t0 = time.perf_counter()
    normalized = []
    unrecognized = []
    rxnorm_calls = 0

    for raw_name in state.get("extracted_drug_names", []):
        # Step 1: direct graph lookup
        graph_name = _find_in_graph(raw_name)
        if graph_name:
            normalized.append(graph_name)
            continue

        # Step 2: RxNorm → generic name → retry graph
        rxnorm_calls += 1
        generic = rxnorm.normalize(raw_name)
        if generic:
            graph_name = _find_in_graph(generic)
            if graph_name:
                normalized.append(graph_name)
                continue

        # Step 3: not found anywhere
        unrecognized.append(raw_name)

    trace_entry = {
        "node": "normalization",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "drug_count": len(state.get("extracted_drug_names", [])),
        "resolved_count": len(normalized),
        "rxnorm_calls": rxnorm_calls,
    }
    return {
        "normalized_drugs": normalized,
        "unrecognized_drugs": unrecognized,
        "trace": state.get("trace", []) + [trace_entry],
    }
