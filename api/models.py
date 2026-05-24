"""
Pydantic models for FastAPI request/response validation.

These are separate from agent/state.py TypedDicts because:
  - state.py models are internal pipeline contracts (TypedDict)
  - api/models.py are external API contracts (Pydantic, with validation + serialization)
Keeping them separate means we can evolve the API shape independently of the
internal agent state without breaking either.
"""

from typing import Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    image_s3_key: Optional[str] = None
    # Session context — optional, never stored
    conditions: list[str] = []
    allergies: list[str] = []
    age_group: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class InteractionResponse(BaseModel):
    drug_a: str
    drug_b: str
    severity: str
    description: str
    source_id: str


class ContraindicationResponse(BaseModel):
    drug: str
    condition: str
    risk_type: str


class ReportResponse(BaseModel):
    risk_level: str
    summary: str
    interactions: list[InteractionResponse]
    contraindications: list[ContraindicationResponse]
    unrecognized_drugs: list[str]
    sources: list[str]
    disclaimer: str


class UploadResponse(BaseModel):
    s3_key: str


class DrugSearchResult(BaseModel):
    name: str
    brand_names: list[str]
    drug_class: str


class GraphNode(BaseModel):
    id: str
    label: str
    drug_class: str


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    severity: str
    description: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
