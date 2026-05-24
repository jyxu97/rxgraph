"""
Node 4: Risk Ranking + Safety Guardrail

Two responsibilities:
  1. Guardrail: drop any interaction that has no source_id.
     Every output claim must be traceable to a source record.
  2. Ranking: sort interactions by severity so the most serious
     appear first in the report.

Why is this a separate node and not inline in report_generation?
The guardrail is independently testable — you can call verify_and_rank()
with any state dict in a unit test without running the full pipeline.
It also makes the safety constraint explicit in the graph structure:
report generation is only reachable after this gate passes.

Why drop unsupported claims rather than raising an error?
An interaction without a source_id is a data quality issue from ingestion,
not a user error. Dropping it and surfacing the remaining supported claims
is more useful than failing the entire query.
"""

from agent.state import AgentState, Interaction

SEVERITY_ORDER = {
    "major":    0,
    "moderate": 1,
    "minor":    2,
    "unknown":  3,
}


def verify_and_rank(state: AgentState) -> dict:
    """
    Node 4: verify source citations and rank by severity.

    Routing: graph.py uses the return value of route_after_guardrail()
    to decide whether to proceed to report_generation or error_handler.
    """
    interactions: list[Interaction] = state.get("graph_query_results", [])

    # Drop interactions with no source_id — they cannot be cited in the report.
    supported = [i for i in interactions if i.get("source_id")]
    dropped_count = len(interactions) - len(supported)

    if dropped_count > 0:
        print(f"  [guardrail] dropped {dropped_count} unsupported claim(s)")

    # Sort: major first, unknown last.
    ranked = sorted(
        supported,
        key=lambda i: SEVERITY_ORDER.get(i["severity"], 3),
    )

    return {"graph_query_results": ranked}


def route_after_guardrail(state: AgentState) -> str:
    """
    Conditional edge: decides which node runs after the guardrail.

    If a prior node set an error, route to END (LangGraph's terminal node).
    Otherwise proceed to report generation.

    This function is registered with graph.add_conditional_edges() in graph.py.
    It receives the full state and returns the name of the next node to run.
    """
    if state.get("error"):
        return "end"
    return "report_generation"
