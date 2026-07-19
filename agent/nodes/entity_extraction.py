"""
Node 1: Entity Extraction

Two input modes:
  - Text query: GPT-4o-mini extracts drug names + session context from natural language
  - Image (S3 key): GPT-4o vision extracts drug names from prescription label / med list

Why GPT-4o for vision but GPT-4o-mini for text?
Vision requires GPT-4o's multimodal capability. Text extraction is an NLP task
that GPT-4o-mini handles reliably at lower cost.

Image scope: only text printed/written on a label is parsed.
Pill/tablet appearance recognition is explicitly out of scope.
If image quality is too poor to extract any names, a structured error is returned
asking the user to type their medications manually.
"""

import base64
import json
import os
import time

from openai import OpenAI

from agent.state import AgentState

_PROMPT_VERSION = "v1"  # bump when TEXT_SYSTEM_PROMPT or VISION_SYSTEM_PROMPT changes

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


TEXT_SYSTEM_PROMPT = """
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

VISION_SYSTEM_PROMPT = """
You are a medical label reader.
Extract all drug names visible in this prescription label or medication list image.

Return JSON with exactly these fields:
  drug_names: list of drug name strings found in the image (brand or generic)
  low_quality: true if the image is too blurry or unclear to read reliably, else false

Rules:
  - Only extract names visibly printed or written on the label.
  - Do not identify drugs by pill or tablet appearance — text only.
  - If low_quality is true, set drug_names to [].
  - Return only valid JSON, no markdown fences.
"""


def extract_entities(state: AgentState) -> dict:
    """
    Node 1: parse user input (text or image) into structured drug names + context.

    When both image and text are provided, extract from both and merge the drug
    name lists. Session context (conditions, allergies, age_group) comes from
    the text query only — images don't carry that information.
    """
    t0 = time.perf_counter()
    s3_key = state.get("uploaded_image_s3_key")
    query = state.get("user_query", "").strip()

    if s3_key and query:
        # Both provided — merge drug names from image and text
        image_result = _extract_from_image(s3_key)
        if image_result.get("error"):
            return image_result
        text_result = _extract_from_text(query)
        if text_result.get("error"):
            return text_result
        combined_drugs = list(set(
            image_result.get("extracted_drug_names", []) +
            text_result.get("extracted_drug_names", [])
        ))
        trace_entry = {
            "node": "entity_extraction",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "input_mode": "both",
            "model_text": "gpt-4o-mini",
            "model_image": "gpt-4o",
            "prompt_version": _PROMPT_VERSION,
            "prompt_tokens": (
                image_result.pop("_prompt_tokens", 0) +
                text_result.pop("_prompt_tokens", 0)
            ),
            "completion_tokens": (
                image_result.pop("_completion_tokens", 0) +
                text_result.pop("_completion_tokens", 0)
            ),
        }
        return {
            "extracted_drug_names": combined_drugs,
            "session_conditions":   text_result.get("session_conditions", []),
            "session_allergies":    text_result.get("session_allergies", []),
            "session_age_group":    text_result.get("session_age_group"),
            "trace": state.get("trace", []) + [trace_entry],
        }
    elif s3_key:
        result = _extract_from_image(s3_key)
        if result.get("error"):
            return result
        trace_entry = {
            "node": "entity_extraction",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "input_mode": "image",
            "model": "gpt-4o",
            "prompt_version": _PROMPT_VERSION,
            "prompt_tokens": result.pop("_prompt_tokens", 0),
            "completion_tokens": result.pop("_completion_tokens", 0),
        }
        return {**result, "trace": state.get("trace", []) + [trace_entry]}
    else:
        result = _extract_from_text(query)
        if result.get("error"):
            return result
        trace_entry = {
            "node": "entity_extraction",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "input_mode": "text",
            "model": "gpt-4o-mini",
            "prompt_version": _PROMPT_VERSION,
            "prompt_tokens": result.pop("_prompt_tokens", 0),
            "completion_tokens": result.pop("_completion_tokens", 0),
        }
        return {**result, "trace": state.get("trace", []) + [trace_entry]}


def _extract_from_text(query: str) -> dict:
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": TEXT_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"error": f"Entity extraction failed: {e}"}

    usage = response.usage
    return {
        "extracted_drug_names": parsed.get("drug_names", []),
        "session_conditions":   parsed.get("conditions", []),
        "session_allergies":    parsed.get("allergies", []),
        "session_age_group":    parsed.get("age_group"),
        # Prefixed with _ so extract_entities can pop them into the trace entry
        # without leaking them into AgentState.
        "_prompt_tokens":     usage.prompt_tokens if usage else 0,
        "_completion_tokens": usage.completion_tokens if usage else 0,
    }


def _extract_from_image(s3_key: str) -> dict:
    """
    Fetch image from S3, encode as base64, call GPT-4o vision.

    Why base64 inline instead of a pre-signed URL?
    Pre-signed URLs expire. If there's any delay between upload and agent invocation
    (e.g. queue backlog), a short-lived URL could expire before Node 1 runs.
    Base64-encoding the image bytes and passing them inline is reliable regardless
    of timing.
    """
    # Import here to avoid a hard dependency on boto3 for text-only usage
    from api.s3_client import get_image

    try:
        image_bytes = get_image(s3_key)
    except Exception as e:
        return {"error": f"Failed to fetch image from S3: {e}"}

    b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                        },
                        {
                            "type": "text",
                            "text": "Extract all drug names from this image.",
                        },
                    ],
                },
            ],
            temperature=0,
        )
        parsed = json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"error": f"Vision extraction failed: {e}"}

    if parsed.get("low_quality"):
        return {
            "error": "Image quality is too poor to read. Please type your medication names manually.",
        }

    usage = response.usage
    return {
        "extracted_drug_names": parsed.get("drug_names", []),
        "session_conditions":   [],
        "session_allergies":    [],
        "session_age_group":    None,
        "_prompt_tokens":     usage.prompt_tokens if usage else 0,
        "_completion_tokens": usage.completion_tokens if usage else 0,
    }
