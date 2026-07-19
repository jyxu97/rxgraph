"""
RxGraph evaluation runner.

Loads eval/dataset.json, invokes the LangGraph agent for each case, checks
assertions against the returned AgentState, and writes a timestamped JSON
results file to eval/results/.

Usage (from project root):
    python eval/run_eval.py
    python eval/run_eval.py --cases eval-001 eval-007 eval-013
    python eval/run_eval.py --dry-run   # validate dataset only, no agent calls

Requirements:
    - Neo4j running and PYTHONPATH set to project root (or run from project root)
    - OPENAI_API_KEY, NEO4J_URI, NEO4J_PASSWORD set in environment / .env
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running from project root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

DATASET_PATH = Path(__file__).parent / "dataset.json"
RESULTS_DIR = Path(__file__).parent / "results"

# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------

def _interaction_pairs_found(graph_results: list[dict]) -> set[tuple[str, str]]:
    """Return a set of (a, b) tuples from graph_query_results, both directions."""
    pairs: set[tuple[str, str]] = set()
    for r in graph_results:
        a = r.get("drug_a", "")
        b = r.get("drug_b", "")
        if a and b:
            pairs.add((a, b))
            pairs.add((b, a))
    return pairs


def evaluate_assertions(case: dict, state: dict) -> list[dict]:
    """
    Check each assertion in case["expected"] against the agent state.

    Returns a list of assertion result dicts:
        {name, passed, expected, actual, data_dependent}
    """
    expected = case.get("expected", {})
    results: list[dict] = []

    normalized = set(state.get("normalized_drugs", []))
    unrecognized = state.get("unrecognized_drugs", [])
    unrecognized_lower = {u.lower() for u in unrecognized}
    graph_results = state.get("graph_query_results", [])
    report = state.get("report")
    interaction_pairs = _interaction_pairs_found(graph_results)

    # normalized_drugs_include
    for drug in expected.get("normalized_drugs_include", []):
        passed = drug.lower() in {n.lower() for n in normalized}
        results.append({
            "name": f"normalized_drugs_include[{drug}]",
            "passed": passed,
            "expected": drug,
            "actual": sorted(normalized),
            "data_dependent": False,
        })

    # unrecognized_drugs_include
    for drug in expected.get("unrecognized_drugs_include", []):
        passed = drug.lower() in unrecognized_lower
        results.append({
            "name": f"unrecognized_drugs_include[{drug}]",
            "passed": passed,
            "expected": drug,
            "actual": unrecognized,
            "data_dependent": False,
        })

    # unrecognized_drugs_empty
    if expected.get("unrecognized_drugs_empty"):
        passed = len(unrecognized) == 0
        results.append({
            "name": "unrecognized_drugs_empty",
            "passed": passed,
            "expected": [],
            "actual": unrecognized,
            "data_dependent": False,
        })

    # interaction_pairs_include (data-dependent: requires edge in Neo4j)
    for pair in expected.get("interaction_pairs_include", []):
        a, b = pair[0], pair[1]
        passed = (a, b) in interaction_pairs or (b, a) in interaction_pairs
        results.append({
            "name": f"interaction_pairs_include[{a},{b}]",
            "passed": passed,
            "expected": pair,
            "actual": [list(p) for p in interaction_pairs if p[0] <= p[1]],
            "data_dependent": True,
        })

    # risk_level_in
    if "risk_level_in" in expected:
        actual_risk = report["risk_level"] if report else None
        passed = actual_risk in expected["risk_level_in"]
        results.append({
            "name": "risk_level_in",
            "passed": passed,
            "expected": expected["risk_level_in"],
            "actual": actual_risk,
            "data_dependent": False,
        })

    # sources_nonempty
    if expected.get("sources_nonempty"):
        actual_sources = report["sources"] if report else []
        passed = len(actual_sources) > 0
        results.append({
            "name": "sources_nonempty",
            "passed": passed,
            "expected": "non-empty list",
            "actual": actual_sources,
            "data_dependent": False,
        })

    # report_not_null
    if expected.get("report_not_null"):
        passed = report is not None
        results.append({
            "name": "report_not_null",
            "passed": passed,
            "expected": "non-null",
            "actual": "non-null" if report else "null",
            "data_dependent": False,
        })

    # min_interaction_count
    if "min_interaction_count" in expected:
        actual_count = len(graph_results)
        passed = actual_count >= expected["min_interaction_count"]
        results.append({
            "name": "min_interaction_count",
            "passed": passed,
            "expected": expected["min_interaction_count"],
            "actual": actual_count,
            "data_dependent": True,
        })

    # interaction_count_is_zero
    if expected.get("interaction_count_is_zero"):
        passed = len(graph_results) == 0
        results.append({
            "name": "interaction_count_is_zero",
            "passed": passed,
            "expected": 0,
            "actual": len(graph_results),
            "data_dependent": False,
        })

    # no_crash is implicit: if we reach this function the agent didn't raise.
    # We record it as a passed assertion for completeness.
    if expected.get("no_crash"):
        results.append({
            "name": "no_crash",
            "passed": True,
            "expected": "no exception",
            "actual": "no exception",
            "data_dependent": False,
        })

    return results


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict[str, Any] = {
    "uploaded_image_s3_key": None,
    "extracted_drug_names": [],
    "normalized_drugs": [],
    "unrecognized_drugs": [],
    "graph_query_results": [],
    "contraindication_results": [],
    "report": None,
    "error": None,
}


def invoke_agent(case_input: dict) -> tuple[dict, float]:
    """
    Invoke the LangGraph agent and return (state, latency_ms).
    Imports are deferred so --dry-run works without a live database.
    """
    from agent.graph import app  # noqa: PLC0415

    initial_state: dict[str, Any] = {
        **_EMPTY_STATE,
        "user_query": case_input["query"],
        "session_conditions": case_input.get("conditions", []),
        "session_allergies": case_input.get("allergies", []),
        "session_age_group": case_input.get("age_group"),
    }

    t0 = time.perf_counter()
    result = app.invoke(initial_state)
    latency_ms = (time.perf_counter() - t0) * 1000
    return result, latency_ms


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(case_results: list[dict]) -> dict:
    """Aggregate per-case assertion results into summary metrics."""

    def _rate(assertions: list[dict]) -> float | None:
        if not assertions:
            return None
        return round(sum(1 for a in assertions if a["passed"]) / len(assertions), 4)

    all_assertions = [a for cr in case_results for a in cr["assertions"]]

    entity_assertions = [
        a for a in all_assertions if a["name"].startswith("normalized_drugs_include")
    ]
    retrieval_assertions = [
        a for a in all_assertions if a["name"].startswith("interaction_pairs_include")
    ]
    risk_assertions = [
        a for a in all_assertions if a["name"] == "risk_level_in"
    ]
    source_assertions = [
        a for a in all_assertions if a["name"] == "sources_nonempty"
    ]

    # Refusal precision: unrecognized_drug cases where ALL extracted drugs were
    # unrecognized — system must return zero interactions.
    refusal_cases = [
        cr for cr in case_results
        if cr["category"] == "unrecognized_drug" and cr["status"] != "error"
    ]
    refusal_correct = [
        cr for cr in refusal_cases
        if all(
            a["passed"]
            for a in cr["assertions"]
            if a["name"] == "interaction_count_is_zero"
        )
        and any(a["name"] == "interaction_count_is_zero" for a in cr["assertions"])
    ]

    # Structured-output success: cases that didn't error
    total = len(case_results)
    non_error = sum(1 for cr in case_results if cr["status"] != "error")

    # Latency
    latencies = sorted(
        cr["latency_ms"] for cr in case_results
        if cr.get("latency_ms") is not None and cr["status"] != "error"
    )
    p50 = latencies[len(latencies) // 2] if latencies else None
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_idx, len(latencies) - 1)] if latencies else None

    return {
        "entity_resolution_rate": _rate(entity_assertions),
        "retrieval_recall": _rate(retrieval_assertions),
        "risk_level_accuracy": _rate(risk_assertions),
        "source_citation_rate": _rate(source_assertions),
        "refusal_precision": (
            round(len(refusal_correct) / len(refusal_cases), 4) if refusal_cases else None
        ),
        "structured_output_success_rate": (
            round(non_error / total, 4) if total > 0 else None
        ),
        "p50_latency_ms": round(p50, 1) if p50 is not None else None,
        "p95_latency_ms": round(p95, 1) if p95 is not None else None,
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

_COLOR = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "reset": "\033[0m",
    "bold": "\033[1m",
}


def _c(text: str, color: str) -> str:
    if sys.stdout.isatty():
        return f"{_COLOR[color]}{text}{_COLOR['reset']}"
    return text


def print_case_result(cr: dict) -> None:
    status_str = {
        "pass": _c("PASS", "green"),
        "fail": _c("FAIL", "red"),
        "error": _c("ERR ", "yellow"),
    }.get(cr["status"], cr["status"])

    latency = f"{cr['latency_ms']:.0f}ms" if cr.get("latency_ms") else "—"
    print(f"  [{status_str}] {cr['id']:<12} {cr['category']:<20} {latency:>8}")

    for a in cr["assertions"]:
        if not a["passed"]:
            dd = " (data-dependent)" if a["data_dependent"] else ""
            print(f"         ✗ {a['name']}{dd}")
            print(f"           expected: {a['expected']}")
            print(f"           actual:   {a['actual']}")

    if cr.get("error_message"):
        print(f"         ✗ exception: {cr['error_message']}")


def print_summary(summary: dict, metrics: dict, by_category: dict) -> None:
    total = summary["total"]
    passed = summary["passed"]
    failed = summary["failed"]
    errors = summary["errors"]

    print()
    print(_c("=" * 60, "bold"))
    print(_c("RxGraph Evaluation Results", "bold"))
    print(_c("=" * 60, "bold"))
    print(f"Total cases : {total}")
    print(f"Passed      : {_c(str(passed), 'green')}")
    print(f"Failed      : {_c(str(failed), 'red') if failed else str(failed)}")
    print(f"Errors      : {_c(str(errors), 'yellow') if errors else str(errors)}")
    print(f"Pass rate   : {summary['pass_rate']:.1%}")
    print()

    print("Metrics")
    print("-" * 40)
    m_labels = {
        "entity_resolution_rate": "Entity resolution rate",
        "retrieval_recall": "Retrieval recall (data-dep.)",
        "risk_level_accuracy": "Risk level accuracy",
        "source_citation_rate": "Source citation rate",
        "refusal_precision": "Refusal precision",
        "structured_output_success_rate": "Structured output success",
        "p50_latency_ms": "p50 latency (ms)",
        "p95_latency_ms": "p95 latency (ms)",
    }
    for key, label in m_labels.items():
        val = metrics.get(key)
        if val is None:
            display = "n/a"
        elif key.endswith("_ms"):
            display = f"{val:.0f} ms"
        else:
            display = f"{val:.1%}"
        print(f"  {label:<34} {display}")
    print()

    print("By category")
    print("-" * 40)
    for cat, stats in sorted(by_category.items()):
        bar = f"{stats['passed']}/{stats['total']}"
        print(f"  {cat:<22} {bar}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_eval(case_ids: list[str] | None, dry_run: bool) -> dict:
    with open(DATASET_PATH) as f:
        dataset = json.load(f)

    cases = dataset["cases"]
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
        if not cases:
            print(f"No cases matched: {case_ids}", file=sys.stderr)
            sys.exit(1)

    print(f"Dataset version : {dataset['version']}")
    print(f"Cases to run    : {len(cases)}")
    if dry_run:
        print("Dry run: dataset loaded and validated. No agent calls.")
        return {}

    case_results: list[dict] = []
    print()

    for case in cases:
        cr: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "description": case["description"],
            "status": "pass",
            "assertions": [],
            "latency_ms": None,
            "error_message": None,
            "result_summary": {},
        }

        try:
            state, latency_ms = invoke_agent(case["input"])
            cr["latency_ms"] = round(latency_ms, 1)

            assertions = evaluate_assertions(case, state)
            cr["assertions"] = assertions

            failed = [a for a in assertions if not a["passed"]]
            cr["status"] = "fail" if failed else "pass"

            report = state.get("report")
            cr["result_summary"] = {
                "normalized_drugs": state.get("normalized_drugs", []),
                "unrecognized_drugs": state.get("unrecognized_drugs", []),
                "interaction_count": len(state.get("graph_query_results", [])),
                "risk_level": report["risk_level"] if report else None,
                "sources": report["sources"] if report else [],
                "error": state.get("error"),
            }

        except Exception as exc:  # noqa: BLE001
            cr["status"] = "error"
            cr["error_message"] = str(exc)

            # Mark no_crash assertion as failed for error cases
            if case.get("expected", {}).get("no_crash"):
                cr["assertions"].append({
                    "name": "no_crash",
                    "passed": False,
                    "expected": "no exception",
                    "actual": str(exc),
                    "data_dependent": False,
                })

        print_case_result(cr)
        case_results.append(cr)

    # Aggregate
    passed = sum(1 for cr in case_results if cr["status"] == "pass")
    failed = sum(1 for cr in case_results if cr["status"] == "fail")
    errors = sum(1 for cr in case_results if cr["status"] == "error")
    total = len(case_results)

    by_category: dict[str, dict] = {}
    for cr in case_results:
        cat = cr["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0}
        by_category[cat]["total"] += 1
        if cr["status"] == "pass":
            by_category[cat]["passed"] += 1

    metrics = compute_metrics(case_results)
    summary = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
    }

    print_summary(summary, metrics, by_category)

    output = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset["version"],
        "summary": summary,
        "metrics": metrics,
        "by_category": by_category,
        "cases": case_results,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    results_path = RESULTS_DIR / f"run_{output['run_id']}.json"
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results written to {results_path}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="RxGraph evaluation runner")
    parser.add_argument(
        "--cases",
        nargs="+",
        metavar="ID",
        help="Run only these case IDs (e.g. eval-001 eval-007)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate dataset format without invoking the agent",
    )
    args = parser.parse_args()
    run_eval(args.cases, args.dry_run)


if __name__ == "__main__":
    main()
