"""
RxGraph evaluation runner.

Loads eval/dataset.json, invokes the agent for each case, checks assertions
against the returned state, and writes a timestamped JSON results file to
eval/results/.

Usage (from project root):
    python eval/run_eval.py                         # full system (default)
    python eval/run_eval.py --mode baseline         # direct LLM only
    python eval/run_eval.py --mode compare          # both, side-by-side table
    python eval/run_eval.py --cases eval-001 eval-007
    python eval/run_eval.py --dry-run               # validate dataset, no calls

Requirements:
    - OPENAI_API_KEY in environment / .env
    - Neo4j running (not needed for --mode baseline)
    - NEO4J_URI, NEO4J_PASSWORD for full / compare modes
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

DATASET_PATH = Path(__file__).parent / "dataset.json"
RESULTS_DIR  = Path(__file__).parent / "results"

# ---------------------------------------------------------------------------
# Baseline LLM prompt
# ---------------------------------------------------------------------------

BASELINE_SYSTEM_PROMPT = """
You are a drug interaction checker.
Given a query about medications, identify any known drug interactions.

Return JSON with exactly these fields:
{
  "risk_level": "major" | "moderate" | "minor" | "unknown" | "none",
  "interactions": [
    {"drug_a": "<generic name>", "drug_b": "<generic name>",
     "severity": "major" | "moderate" | "minor" | "unknown"}
  ],
  "unrecognized_drugs": ["<drug names you do not recognise as real medications>"]
}

Rules:
  - Use generic drug names (e.g. "warfarin" not "Coumadin", "ibuprofen" not "Advil").
  - Only include interactions you are confident about. Do not guess.
  - If a drug name is not a real medication, add it to unrecognized_drugs.
  - risk_level is the highest severity found, or "none" if no interactions.
  - Return only valid JSON, no markdown fences.
"""

BASELINE_PROMPT_VERSION = "v1"

# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------

def _interaction_pairs_found(graph_results: list[dict]) -> set[tuple[str, str]]:
    """Return a lowercase (a, b) set from graph_query_results, both directions."""
    pairs: set[tuple[str, str]] = set()
    for r in graph_results:
        a = r.get("drug_a", "").lower()
        b = r.get("drug_b", "").lower()
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

    normalized        = set(state.get("normalized_drugs", []))
    unrecognized      = state.get("unrecognized_drugs", [])
    unrecognized_lower = {u.lower() for u in unrecognized}
    graph_results     = state.get("graph_query_results", [])
    report            = state.get("report")
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

    # interaction_pairs_include (data-dependent for full system)
    for pair in expected.get("interaction_pairs_include", []):
        a, b = pair[0].lower(), pair[1].lower()
        passed = (a, b) in interaction_pairs
        results.append({
            "name": f"interaction_pairs_include[{pair[0]},{pair[1]}]",
            "passed": passed,
            "expected": pair,
            "actual": sorted({p for p in interaction_pairs if p[0] <= p[1]}),
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

    # no_crash — implicit; recorded as passed if we reached this function
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
# Invocation functions
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict[str, Any] = {
    "uploaded_image_s3_key":    None,
    "extracted_drug_names":     [],
    "normalized_drugs":         [],
    "unrecognized_drugs":       [],
    "graph_query_results":      [],
    "contraindication_results": [],
    "report":                   None,
    "error":                    None,
    "trace":                    [],
}


def invoke_agent(case_input: dict) -> tuple[dict, float]:
    """Invoke the full LangGraph agent. Import deferred so --dry-run works."""
    from agent.graph import app  # noqa: PLC0415

    initial_state: dict[str, Any] = {
        **_EMPTY_STATE,
        "user_query":          case_input["query"],
        "session_conditions":  case_input.get("conditions", []),
        "session_allergies":   case_input.get("allergies", []),
        "session_age_group":   case_input.get("age_group"),
    }

    t0 = time.perf_counter()
    result = app.invoke(initial_state)
    return result, (time.perf_counter() - t0) * 1000


def invoke_baseline_llm(case_input: dict) -> tuple[dict, float]:
    """
    Call GPT-4o-mini directly — no entity normalization, no graph lookup,
    no source citations. Returns a pseudo-state compatible with evaluate_assertions().

    This is the baseline RxGraph's graph-augmented pipeline is compared against.
    Expected structural differences versus full system:
      - normalized_drugs: always empty (no normalization step)
      - sources: always empty (LLM has no real citations → source_citation_rate = 0%)
      - unrecognized_drug cases: LLM may hallucinate interactions for invented drug names
    """
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    t0 = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": BASELINE_SYSTEM_PROMPT},
                {"role": "user",   "content": case_input["query"]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception as exc:
        return {"error": str(exc)}, (time.perf_counter() - t0) * 1000

    interactions = [
        {
            "drug_a":    i.get("drug_a", "").lower(),
            "drug_b":    i.get("drug_b", "").lower(),
            "severity":  i.get("severity", "unknown"),
            "description": "",
            "source_id": "",   # LLM has no real source citation
        }
        for i in parsed.get("interactions", [])
        if i.get("drug_a") and i.get("drug_b")
    ]

    return {
        "normalized_drugs":         [],   # no normalization step
        "unrecognized_drugs":       [d.lower() for d in parsed.get("unrecognized_drugs", [])],
        "graph_query_results":      interactions,
        "contraindication_results": [],
        "report": {
            "risk_level": parsed.get("risk_level", "none"),
            "sources":    [],  # LLM has no real source citations
        },
        "error": None,
    }, (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def _run_cases(
    cases: list[dict],
    invoke_fn: Callable,
) -> tuple[list[dict], dict, dict, dict]:
    """
    Run all cases with invoke_fn. Print per-case results.
    Returns (case_results, summary, metrics, by_category).
    """
    case_results: list[dict] = []

    for case in cases:
        cr: dict[str, Any] = {
            "id":            case["id"],
            "category":      case["category"],
            "description":   case["description"],
            "status":        "pass",
            "assertions":    [],
            "latency_ms":    None,
            "error_message": None,
            "result_summary": {},
        }

        try:
            state, latency_ms = invoke_fn(case["input"])
            cr["latency_ms"] = round(latency_ms, 1)

            assertions = evaluate_assertions(case, state)
            cr["assertions"] = assertions
            cr["status"] = "fail" if any(not a["passed"] for a in assertions) else "pass"

            report = state.get("report")
            cr["result_summary"] = {
                "normalized_drugs":  state.get("normalized_drugs", []),
                "unrecognized_drugs": state.get("unrecognized_drugs", []),
                "interaction_count": len(state.get("graph_query_results", [])),
                "risk_level":        report["risk_level"] if report else None,
                "sources":           report["sources"]    if report else [],
                "error":             state.get("error"),
            }

        except Exception as exc:  # noqa: BLE001
            cr["status"] = "error"
            cr["error_message"] = str(exc)
            if case.get("expected", {}).get("no_crash"):
                cr["assertions"].append({
                    "name": "no_crash", "passed": False,
                    "expected": "no exception", "actual": str(exc),
                    "data_dependent": False,
                })

        print_case_result(cr)
        case_results.append(cr)

    total  = len(case_results)
    passed = sum(1 for cr in case_results if cr["status"] == "pass")
    failed = sum(1 for cr in case_results if cr["status"] == "fail")
    errors = sum(1 for cr in case_results if cr["status"] == "error")

    by_category: dict[str, dict] = {}
    for cr in case_results:
        cat = cr["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0}
        by_category[cat]["total"] += 1
        if cr["status"] == "pass":
            by_category[cat]["passed"] += 1

    summary = {
        "total": total, "passed": passed, "failed": failed, "errors": errors,
        "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
    }
    return case_results, summary, compute_metrics(case_results), by_category


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(case_results: list[dict]) -> dict:
    """Aggregate per-case assertion results into summary metrics."""

    def _rate(assertions: list[dict]) -> float | None:
        if not assertions:
            return None
        return round(sum(1 for a in assertions if a["passed"]) / len(assertions), 4)

    all_a = [a for cr in case_results for a in cr["assertions"]]

    refusal_cases = [
        cr for cr in case_results
        if cr["category"] == "unrecognized_drug" and cr["status"] != "error"
    ]
    refusal_correct = [
        cr for cr in refusal_cases
        if any(a["name"] == "interaction_count_is_zero" for a in cr["assertions"])
        and all(a["passed"] for a in cr["assertions"] if a["name"] == "interaction_count_is_zero")
    ]

    total     = len(case_results)
    non_error = sum(1 for cr in case_results if cr["status"] != "error")

    latencies = sorted(
        cr["latency_ms"] for cr in case_results
        if cr.get("latency_ms") is not None and cr["status"] != "error"
    )
    p50 = latencies[len(latencies) // 2] if latencies else None
    p95 = latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)] if latencies else None

    return {
        "entity_resolution_rate":         _rate([a for a in all_a if a["name"].startswith("normalized_drugs_include")]),
        "retrieval_recall":               _rate([a for a in all_a if a["name"].startswith("interaction_pairs_include")]),
        "risk_level_accuracy":            _rate([a for a in all_a if a["name"] == "risk_level_in"]),
        "source_citation_rate":           _rate([a for a in all_a if a["name"] == "sources_nonempty"]),
        "refusal_precision":              round(len(refusal_correct) / len(refusal_cases), 4) if refusal_cases else None,
        "structured_output_success_rate": round(non_error / total, 4) if total > 0 else None,
        "p50_latency_ms": round(p50, 1) if p50 is not None else None,
        "p95_latency_ms": round(p95, 1) if p95 is not None else None,
    }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

_COLOR = {
    "green":  "\033[32m", "red":   "\033[31m",
    "yellow": "\033[33m", "cyan":  "\033[36m",
    "reset":  "\033[0m",  "bold":  "\033[1m",
}


def _c(text: str, color: str) -> str:
    if sys.stdout.isatty():
        return f"{_COLOR[color]}{text}{_COLOR['reset']}"
    return text


def print_case_result(cr: dict) -> None:
    status_str = {
        "pass":  _c("PASS", "green"),
        "fail":  _c("FAIL", "red"),
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


_METRIC_LABELS = {
    "entity_resolution_rate":         "Entity resolution rate",
    "retrieval_recall":               "Retrieval recall (data-dep.)",
    "risk_level_accuracy":            "Risk level accuracy",
    "source_citation_rate":           "Source citation rate",
    "refusal_precision":              "Refusal precision",
    "structured_output_success_rate": "Structured output success",
    "p50_latency_ms":                 "p50 latency (ms)",
    "p95_latency_ms":                 "p95 latency (ms)",
}


def _fmt(key: str, val: float | None) -> str:
    if val is None:
        return "n/a"
    return f"{val:.0f} ms" if key.endswith("_ms") else f"{val:.1%}"


def print_summary(summary: dict, metrics: dict, by_category: dict) -> None:
    print()
    print(_c("=" * 60, "bold"))
    print(_c("RxGraph Evaluation Results", "bold"))
    print(_c("=" * 60, "bold"))
    print(f"Total cases : {summary['total']}")
    print(f"Passed      : {_c(str(summary['passed']), 'green')}")
    failed = summary["failed"]
    print(f"Failed      : {_c(str(failed), 'red') if failed else str(failed)}")
    errors = summary["errors"]
    print(f"Errors      : {_c(str(errors), 'yellow') if errors else str(errors)}")
    print(f"Pass rate   : {summary['pass_rate']:.1%}")
    print()
    print("Metrics")
    print("-" * 40)
    for key, label in _METRIC_LABELS.items():
        print(f"  {label:<34} {_fmt(key, metrics.get(key))}")
    print()
    print("By category")
    print("-" * 40)
    for cat, stats in sorted(by_category.items()):
        print(f"  {cat:<22} {stats['passed']}/{stats['total']}")
    print()


def print_comparison(
    full_metrics: dict,
    base_metrics: dict,
    full_summary: dict,
    base_summary: dict,
) -> None:
    """Print a side-by-side comparison table of full system vs. direct LLM."""
    L, C = 36, 14   # label col width, value col width
    sep = "  " + "-" * (L + C * 2 + 4)

    print()
    print(_c("=" * (L + C * 2 + 6), "bold"))
    print(_c("  Baseline Comparison", "bold"))
    print(_c("=" * (L + C * 2 + 6), "bold"))
    print(f"  {'Metric':<{L}} {'Full System':>{C}}  {'Direct LLM':>{C}}")
    print(sep)

    full_pr = f"{full_summary['pass_rate']:.1%} ({full_summary['passed']}/{full_summary['total']})"
    base_pr = f"{base_summary['pass_rate']:.1%} ({base_summary['passed']}/{base_summary['total']})"
    print(f"  {'Overall pass rate':<{L}} {full_pr:>{C}}  {base_pr:>{C}}")
    print(sep)

    for key, label in _METRIC_LABELS.items():
        fv, bv = full_metrics.get(key), base_metrics.get(key)
        full_s, base_s = _fmt(key, fv), _fmt(key, bv)

        # Arrow highlights meaningful gaps (>5pp) for rate metrics
        arrow = ""
        if fv is not None and bv is not None and not key.endswith("_ms"):
            if fv > bv + 0.05:
                arrow = _c("  ◀ full", "green")
            elif bv > fv + 0.05:
                arrow = _c("  ◀ base", "red")

        print(f"  {label:<{L}} {full_s:>{C}}  {base_s:>{C}}{arrow}")

    print()
    print(_c("  ◀ full = RxGraph wins by >5pp   ◀ base = baseline wins by >5pp", "yellow"))
    print(_c("  retrieval_recall for baseline reflects LLM recall, which may include hallucinations.", "yellow"))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_eval(
    case_ids: list[str] | None,
    dry_run: bool,
    mode: str = "full",
) -> dict:
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
    print(f"Mode            : {mode}")

    if dry_run:
        print("Dry run: dataset loaded and validated. No agent calls.")
        return {}

    output: dict[str, Any] = {
        "run_id":          datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset["version"],
        "mode":            mode,
    }

    if mode in ("full", "compare"):
        print(_c("\n── Full System ──────────────────────────────────────────", "bold"))
        full_results, full_summary, full_metrics, full_by_cat = _run_cases(cases, invoke_agent)
        print_summary(full_summary, full_metrics, full_by_cat)
        output["full_system"] = {
            "summary": full_summary, "metrics": full_metrics,
            "by_category": full_by_cat, "cases": full_results,
        }

    if mode in ("baseline", "compare"):
        print(_c("\n── Direct LLM Baseline ──────────────────────────────────", "bold"))
        base_results, base_summary, base_metrics, base_by_cat = _run_cases(cases, invoke_baseline_llm)
        print_summary(base_summary, base_metrics, base_by_cat)
        output["baseline"] = {
            "summary": base_summary, "metrics": base_metrics,
            "by_category": base_by_cat, "cases": base_results,
        }

    if mode == "compare":
        print_comparison(full_metrics, base_metrics, full_summary, base_summary)

    # Top-level keys for backwards compatibility (used by tools reading run_*.json)
    primary = output.get("full_system") or output.get("baseline", {})
    output.update({
        "summary":     primary.get("summary", {}),
        "metrics":     primary.get("metrics", {}),
        "by_category": primary.get("by_category", {}),
        "cases":       primary.get("cases", []),
    })

    RESULTS_DIR.mkdir(exist_ok=True)
    results_path = RESULTS_DIR / f"run_{output['run_id']}_{mode}.json"
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results written to {results_path}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="RxGraph evaluation runner")
    parser.add_argument(
        "--cases", nargs="+", metavar="ID",
        help="Run only these case IDs (e.g. eval-001 eval-007)",
    )
    parser.add_argument(
        "--mode", choices=["full", "baseline", "compare"], default="full",
        help="full = RxGraph agent; baseline = direct LLM; compare = both side-by-side",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate dataset format without invoking the agent",
    )
    args = parser.parse_args()
    run_eval(args.cases, args.dry_run, args.mode)


if __name__ == "__main__":
    main()
