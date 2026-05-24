"""
Unit tests for Node 2: Drug Name Normalization

normalize_drugs() calls _find_in_graph() (Neo4j) and rxnorm.normalize() (HTTP).
We mock both so tests run without a live database or network.

Why mock at the module level with monkeypatch?
normalize_drugs() imports _find_in_graph and rxnorm as module-level names.
monkeypatch.setattr replaces them for the duration of each test, then restores
the originals — no risk of test pollution between cases.
"""

import pytest
from unittest.mock import patch

from agent.nodes import normalization


def _run(drug_names, graph_hits=None, rxnorm_map=None):
    """
    Helper: run normalize_drugs() with controlled graph and RxNorm responses.

    graph_hits: set of names that exist in Neo4j
    rxnorm_map: dict of raw_name → generic_name for RxNorm responses
    """
    graph_hits = graph_hits or set()
    rxnorm_map = rxnorm_map or {}

    def fake_find_in_graph(name):
        return name if name in graph_hits else None

    def fake_rxnorm_normalize(name):
        return rxnorm_map.get(name)

    with patch.object(normalization, "_find_in_graph", side_effect=fake_find_in_graph), \
         patch("rxgraph.rxnorm.normalize", side_effect=fake_rxnorm_normalize):
        return normalization.normalize_drugs({"extracted_drug_names": drug_names})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_direct_graph_match():
    result = _run(["warfarin"], graph_hits={"warfarin"})
    assert result["normalized_drugs"] == ["warfarin"]
    assert result["unrecognized_drugs"] == []


def test_rxnorm_fallback_resolves_brand_name():
    # "Advil" not in graph; RxNorm maps it to "ibuprofen" which is in graph
    result = _run(
        ["Advil"],
        graph_hits={"ibuprofen"},
        rxnorm_map={"Advil": "ibuprofen"},
    )
    assert result["normalized_drugs"] == ["ibuprofen"]
    assert result["unrecognized_drugs"] == []


def test_unrecognized_when_not_in_graph_or_rxnorm():
    result = _run(["asdfxyz"], graph_hits=set(), rxnorm_map={})
    assert result["normalized_drugs"] == []
    assert result["unrecognized_drugs"] == ["asdfxyz"]


def test_rxnorm_resolves_but_generic_not_in_graph():
    # RxNorm knows the drug but we haven't ingested it into Neo4j yet
    result = _run(
        ["Humira"],
        graph_hits=set(),
        rxnorm_map={"Humira": "adalimumab"},
    )
    assert result["normalized_drugs"] == []
    assert result["unrecognized_drugs"] == ["Humira"]


def test_mixed_recognized_and_unrecognized():
    result = _run(
        ["warfarin", "asdfxyz", "Advil"],
        graph_hits={"warfarin", "ibuprofen"},
        rxnorm_map={"Advil": "ibuprofen"},
    )
    assert set(result["normalized_drugs"]) == {"warfarin", "ibuprofen"}
    assert result["unrecognized_drugs"] == ["asdfxyz"]


def test_empty_input():
    result = _run([])
    assert result["normalized_drugs"] == []
    assert result["unrecognized_drugs"] == []
