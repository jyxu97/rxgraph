"""
Node 5: Report Generation

The LLM's only job here is narration — converting structured Neo4j results
into plain English. It does NOT generate or infer any medical facts.

The strict constraint: every sentence in the summary must correspond to a
result already present in graph_query_results or contraindication_results.
The system prompt enforces this explicitly.

Why GPT-4o-mini here too?
Narrating a structured list doesn't require GPT-4o. The input is a JSON
summary of ranked interactions; the output is a readable paragraph.
GPT-4o is reserved for prescription label OCR (Phase 3).
"""

import json
import os
import time

from openai import OpenAI

from agent.state import AgentState, Interaction, InteractionReport

_PROMPT_VERSION = "v1"  # bump when SYSTEM_PROMPT changes

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


SYSTEM_PROMPT = """
You are a medication safety report writer.
You will be given structured drug interaction data from a medical database.
Your job is to narrate this data in clear, plain English for a general audience.

Critical rules:
  - Only describe interactions and contraindications present in the data.
  - Do not introduce any medical facts, warnings, or interactions not in the data.
  - Do not infer severity — use only the severity values provided.
  - Write in a calm, factual tone. Do not alarm unnecessarily.
  - Keep the summary to 3-5 sentences.

The disclaimer will be appended automatically. Do not write it yourself.
"""


def _highest_severity(interactions: list[Interaction]) -> str:
    order = {"major": 0, "moderate": 1, "minor": 2, "unknown": 3, "none": 4}
    if not interactions:
        return "none"
    return min(interactions, key=lambda i: order.get(i["severity"], 3))["severity"]


def generate_report(state: AgentState) -> dict:
    """
    Node 5: narrate ranked results as a plain-language report.
    """
    t0 = time.perf_counter()
    interactions = state.get("graph_query_results", [])
    contraindications = state.get("contraindication_results", [])
    unrecognized = state.get("unrecognized_drugs", [])

    if not interactions and not contraindications:
        summary = "No known drug interactions were found between the specified medications in our database."
        if unrecognized:
            summary += f" Note: the following drug(s) were not recognized and could not be checked: {', '.join(unrecognized)}."
        report = InteractionReport(
            risk_level="none",
            summary=summary,
            interactions=[],
            contraindications=[],
            unrecognized_drugs=unrecognized,
            sources=[],
            disclaimer="For informational purposes only. Consult a licensed pharmacist or healthcare professional before making any medication decisions.",
        )
        trace_entry = {
            "node": "report_generation",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "model": None,
            "prompt_version": _PROMPT_VERSION,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "skipped_llm": True,
        }
        return {"report": report, "trace": state.get("trace", []) + [trace_entry]}

    # Build a compact JSON summary to pass to the LLM.
    # We pass structured data, not free text, so the LLM cannot hallucinate facts.
    data_for_llm = {
        "interactions": [
            {
                "drug_a": i["drug_a"],
                "drug_b": i["drug_b"],
                "severity": i["severity"],
                "description": i["description"],
            }
            for i in interactions
        ],
        "contraindications": [
            {
                "drug": c["drug"],
                "condition": c["condition"],
                "risk_type": c["risk_type"],
            }
            for c in contraindications
        ],
        "unrecognized_drugs": unrecognized,
    }

    client = _get_client()
    prompt_tokens = completion_tokens = 0
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(data_for_llm)},
            ],
            temperature=0,
        )
        summary = response.choices[0].message.content.strip()
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
    except Exception as e:
        summary = f"Report generation failed: {e}. Please review the raw interaction data."

    sources = list({i["source_id"] for i in interactions if i.get("source_id")})

    report = InteractionReport(
        risk_level=_highest_severity(interactions),
        summary=summary,
        interactions=interactions,
        contraindications=contraindications,
        unrecognized_drugs=unrecognized,
        sources=sources,
        disclaimer="For informational purposes only. Consult a licensed pharmacist or healthcare professional before making any medication decisions.",
    )

    trace_entry = {
        "node": "report_generation",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "model": "gpt-4o-mini",
        "prompt_version": _PROMPT_VERSION,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "skipped_llm": False,
    }
    return {"report": report, "trace": state.get("trace", []) + [trace_entry]}
