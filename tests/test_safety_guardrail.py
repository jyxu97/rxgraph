"""
Unit tests for Node 4: Safety Guardrail

No mocks needed — verify_and_rank() and route_after_guardrail() are pure
functions that only read from the state dict and return a new dict.
"""

from agent.nodes.safety_guardrail import verify_and_rank, route_after_guardrail


def _make_interaction(drug_a, drug_b, severity, source_id="src-001"):
    return {
        "drug_a": drug_a,
        "drug_b": drug_b,
        "severity": severity,
        "description": "test description",
        "source_id": source_id,
    }


# ---------------------------------------------------------------------------
# verify_and_rank
# ---------------------------------------------------------------------------

def test_drops_interactions_without_source_id():
    state = {
        "graph_query_results": [
            _make_interaction("warfarin", "aspirin", "major", source_id="src-1"),
            _make_interaction("warfarin", "ibuprofen", "unknown", source_id=""),
            _make_interaction("warfarin", "metformin", "minor", source_id=None),
        ]
    }
    result = verify_and_rank(state)
    assert len(result["graph_query_results"]) == 1
    assert result["graph_query_results"][0]["drug_b"] == "aspirin"


def test_ranks_by_severity_major_first():
    state = {
        "graph_query_results": [
            _make_interaction("a", "b", "unknown",  source_id="s1"),
            _make_interaction("a", "c", "minor",    source_id="s2"),
            _make_interaction("a", "d", "major",    source_id="s3"),
            _make_interaction("a", "e", "moderate", source_id="s4"),
        ]
    }
    result = verify_and_rank(state)
    severities = [i["severity"] for i in result["graph_query_results"]]
    assert severities == ["major", "moderate", "minor", "unknown"]


def test_empty_interactions_returns_empty():
    result = verify_and_rank({"graph_query_results": []})
    assert result["graph_query_results"] == []


def test_all_interactions_dropped_if_no_source_ids():
    state = {
        "graph_query_results": [
            _make_interaction("a", "b", "major", source_id=""),
            _make_interaction("a", "c", "major", source_id=None),
        ]
    }
    result = verify_and_rank(state)
    assert result["graph_query_results"] == []


def test_supported_interactions_preserved_after_drop():
    state = {
        "graph_query_results": [
            _make_interaction("a", "b", "major",   source_id="s1"),
            _make_interaction("a", "c", "unknown", source_id=""),   # dropped
            _make_interaction("a", "d", "minor",   source_id="s3"),
        ]
    }
    result = verify_and_rank(state)
    assert len(result["graph_query_results"]) == 2
    assert result["graph_query_results"][0]["severity"] == "major"
    assert result["graph_query_results"][1]["severity"] == "minor"


# ---------------------------------------------------------------------------
# route_after_guardrail
# ---------------------------------------------------------------------------

def test_routes_to_report_generation_when_no_error():
    assert route_after_guardrail({"error": None}) == "report_generation"
    assert route_after_guardrail({}) == "report_generation"


def test_routes_to_end_when_error_present():
    assert route_after_guardrail({"error": "something failed"}) == "end"
