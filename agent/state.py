"""
AgentState — the shared whiteboard that flows through every node.

Each node reads what it needs and returns a partial dict to update the state.
No node calls another node directly; they only read and write state.
"""

from typing import Optional
from typing_extensions import TypedDict


class Interaction(TypedDict):
    drug_a: str
    drug_b: str
    severity: str        # "major" | "moderate" | "minor" | "unknown"
    description: str
    source_id: str


class Contraindication(TypedDict):
    drug: str
    condition: str
    risk_type: str       # "contraindicated" | "use with caution"


class InteractionReport(TypedDict):
    risk_level: str              # highest severity found across all interactions
    summary: str                 # plain-language narrative from LLM
    interactions: list[Interaction]
    contraindications: list[Contraindication]
    unrecognized_drugs: list[str]
    sources: list[str]           # deduplicated source_ids
    disclaimer: str


class AgentState(TypedDict):
    # --- Input ---
    user_query: str
    uploaded_image_s3_key: Optional[str]  # set by /api/upload-image, None for text queries

    # Session context: in-memory only, never written to Neo4j.
    # Passed as Cypher parameters ($drug_names, $conditions) at query time.
    session_conditions: list[str]    # e.g. ["hypertension", "diabetes"]
    session_allergies: list[str]
    session_age_group: Optional[str] # "pediatric" | "adult" | "elderly" | None

    # --- Intermediate ---
    extracted_drug_names: list[str]       # raw strings from Node 1
    normalized_drugs: list[str]           # generic names confirmed in Neo4j
    unrecognized_drugs: list[str]         # not resolved or not in graph
    graph_query_results: list[Interaction]
    contraindication_results: list[Contraindication]

    # --- Output ---
    report: Optional[InteractionReport]
    error: Optional[str]

    # --- Observability ---
    # Each node appends one entry: {node, latency_ms, ...node-specific fields}
    # Read by the API layer after invoke() to emit a single structured log line.
    trace: list[dict]
