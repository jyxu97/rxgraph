"""
Node 1: Entity Extraction

Extracts drug names and optional session context from the user's text query.

What this node does NOT do:
  - Image processing (Phase 3, requires S3 + GPT-4o vision)
  - Medical reasoning or validation

Why GPT-4o-mini here too?
Extracting drug names from natural language ("can I take Advil with my blood thinner?")
is an NLP extraction task, not reasoning. GPT-4o-mini handles it reliably and cheaply.
"""

import json
import os

from openai import OpenAI

from agent.state import AgentState

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


SYSTEM_PROMPT = """
You are a medical entity extraction assistant.
Extract drug names and optional patient context from the user's query.

Return JSON with exactly these fields:
  drug_names:  list of drug name strings explicitly mentioned (brand or generic)
  conditions:  list of medical conditions mentioned (e.g. ["hypertension", "diabetes"])
  allergies:   list of allergies mentioned
  age_group:   "pediatric" | "adult" | "elderly" | null

Rules:
  - Only extract what is explicitly stated. Never infer or add information.
  - Drug names can be brand names (e.g. "Advil") — do not convert them here.
  - Return empty lists if nothing found for that field.
  - Return only valid JSON, no markdown fences.
"""


def extract_entities(state: AgentState) -> dict:
    """
    Node 1: parse the user query into structured drug names + session context.

    Returns a partial state update — only the fields this node is responsible for.
    LangGraph merges this dict into the shared AgentState.
    """
    client = _get_client()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": state["user_query"]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"error": f"Entity extraction failed: {e}"}

    return {
        "extracted_drug_names": parsed.get("drug_names", []),
        "session_conditions":   parsed.get("conditions", []),
        "session_allergies":    parsed.get("allergies", []),
        "session_age_group":    parsed.get("age_group"),
    }
