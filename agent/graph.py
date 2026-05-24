"""
LangGraph agent assembly — RxGraph Phase 2

Wires the five nodes into a StateGraph and exposes a compiled `app`
that can be invoked with an initial AgentState.

Graph structure:
  entity_extraction
        ↓
  normalization
        ↓
  graph_query
        ↓
  safety_guardrail ──(error)──→ END
        ↓ (ok)
  report_generation
        ↓
       END
"""

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes.entity_extraction import extract_entities
from agent.nodes.normalization import normalize_drugs
from agent.nodes.graph_query import query_interactions
from agent.nodes.safety_guardrail import verify_and_rank, route_after_guardrail
from agent.nodes.report_generation import generate_report


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("entity_extraction",  extract_entities)
    graph.add_node("normalization",      normalize_drugs)
    graph.add_node("graph_query",        query_interactions)
    graph.add_node("safety_guardrail",   verify_and_rank)
    graph.add_node("report_generation",  generate_report)

    graph.set_entry_point("entity_extraction")

    graph.add_edge("entity_extraction", "normalization")
    graph.add_edge("normalization",     "graph_query")
    graph.add_edge("graph_query",       "safety_guardrail")

    # Conditional edge: safety_guardrail routes to report_generation or END
    graph.add_conditional_edges(
        "safety_guardrail",
        route_after_guardrail,
        {
            "report_generation": "report_generation",
            "end": END,
        },
    )

    graph.add_edge("report_generation", END)

    return graph


# Compiled app — import this in the FastAPI layer (Phase 3)
app = build_graph().compile()


if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()

    result = app.invoke({
        "user_query": "Can I take warfarin with aspirin?",
        "session_conditions": [],
        "session_allergies": [],
        "session_age_group": None,
        "extracted_drug_names": [],
        "normalized_drugs": [],
        "unrecognized_drugs": [],
        "graph_query_results": [],
        "contraindication_results": [],
        "report": None,
        "error": None,
    })

    report = result.get("report")
    if report:
        print(f"\nRisk level: {report['risk_level'].upper()}")
        print(f"\nSummary:\n{report['summary']}")
        print(f"\nInteractions found: {len(report['interactions'])}")
        for i in report["interactions"]:
            print(f"  {i['drug_a']} + {i['drug_b']}: [{i['severity']}] {i['description'][:80]}...")
        print(f"\nSources: {report['sources']}")
        print(f"\n{report['disclaimer']}")
    else:
        print("Error:", result.get("error"))
