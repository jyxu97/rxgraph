"""
FastAPI application — Phase 3

Endpoints:
  POST /api/query           — run the LangGraph agent, return InteractionReport
  POST /api/upload-image    — upload prescription label image to S3
  GET  /api/drugs/search    — search drugs by name
  GET  /api/interactions/{drug_id}  — list all interactions for a drug
  GET  /api/graph/{drug_id}         — graph nodes + edges for visualization
"""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

import os
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Header
from fastapi.middleware.cors import CORSMiddleware

from rxgraph import cache as redis_cache, neo4j_client
from agent.graph import app as agent_app
from api.models import (
    QueryRequest, ReportResponse, InteractionResponse, ContraindicationResponse,
    UploadResponse, DrugSearchResult, GraphResponse, GraphNode, GraphEdge,
)
from api import s3_client


# ---------------------------------------------------------------------------
# Lifespan: open Neo4j connection on startup, close on shutdown
#
# Why lifespan and not @app.on_event("startup")?
# @app.on_event is deprecated in FastAPI. The lifespan context manager is the
# current recommended pattern — it's cleaner and guarantees teardown even if
# startup raises an exception.
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    neo4j_client.get_driver().verify_connectivity()
    try:
        redis_cache.get_client().ping()
    except Exception:
        pass  # Redis unavailable — degrade gracefully
    yield
    redis_cache.close()
    neo4j_client.close_driver()


app = FastAPI(title="RxGraph API", lifespan=lifespan)

# Token cost constants (USD per token). Update if OpenAI changes pricing.
_TOKEN_COST_USD: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.15 / 1_000_000, "completion": 0.60 / 1_000_000},
    "gpt-4o":      {"prompt": 2.50 / 1_000_000, "completion": 10.00 / 1_000_000},
}

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# POST /api/query
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


def _build_initial_state(req: QueryRequest) -> dict:
    return {
        "user_query":             req.query,
        "uploaded_image_s3_key":  req.image_s3_key,
        "session_conditions":     req.conditions,
        "session_allergies":      req.allergies,
        "session_age_group":      req.age_group,
        "extracted_drug_names":   [],
        "normalized_drugs":       [],
        "unrecognized_drugs":     [],
        "graph_query_results":    [],
        "contraindication_results": [],
        "report":                 None,
        "error":                  None,
        "trace":                  [],
    }


# Sync def (not async): FastAPI runs this in a thread pool automatically,
# which allows the synchronous Neo4j driver calls inside the agent to run
# without blocking the event loop.
def query(req: QueryRequest) -> ReportResponse:
    request_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()

    result = agent_app.invoke(_build_initial_state(req))

    total_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    _log_trace(request_id, total_latency_ms, result)

    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])

    report = result.get("report")
    if not report:
        raise HTTPException(status_code=500, detail="Agent returned no report.")

    return ReportResponse(
        risk_level=report["risk_level"],
        summary=report["summary"],
        interactions=[InteractionResponse(**i) for i in report["interactions"]],
        contraindications=[ContraindicationResponse(**c) for c in report["contraindications"]],
        unrecognized_drugs=report["unrecognized_drugs"],
        sources=report["sources"],
        disclaimer=report["disclaimer"],
    )


def _log_trace(request_id: str, total_latency_ms: float, result: dict) -> None:
    """Emit one structured log line with the full per-request trace."""
    trace: list[dict] = result.get("trace", [])

    total_prompt = sum(e.get("prompt_tokens", 0) for e in trace)
    total_completion = sum(e.get("completion_tokens", 0) for e in trace)
    estimated_cost = sum(
        e.get("prompt_tokens", 0) * _TOKEN_COST_USD.get(e.get("model") or "", {}).get("prompt", 0)
        + e.get("completion_tokens", 0) * _TOKEN_COST_USD.get(e.get("model") or "", {}).get("completion", 0)
        for e in trace
    )

    if result.get("error"):
        outcome = "error"
    elif result.get("report"):
        outcome = "report"
    else:
        outcome = "no_interactions"

    _logger.info(
        "request_trace %s",
        json.dumps({
            "request_id": request_id,
            "total_latency_ms": total_latency_ms,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "estimated_cost_usd": round(estimated_cost, 6),
            "outcome": outcome,
            "nodes": trace,
        }, separators=(",", ":")),
    )


app.add_api_route("/api/query", query, methods=["POST"], response_model=ReportResponse)


# ---------------------------------------------------------------------------
# POST /api/admin/cache/flush
# ---------------------------------------------------------------------------

def flush_cache(authorization: str = Header(...)) -> dict:
    """
    Flush all cached interaction results from Redis.

    Called by the weekly Lambda after it finishes writing new data to Neo4j,
    so stale cached results are never served. Protected by a shared secret
    passed as a Bearer token — Lambda cannot reach Redis directly since it
    runs in AWS while Redis runs on Lightsail.
    """
    secret = os.environ.get("ADMIN_SECRET", "")
    if not secret or authorization != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    deleted = redis_cache.flush_interaction_cache()
    logging.getLogger(__name__).info("admin cache flush: %d keys deleted", deleted)
    return {"deleted": deleted}


app.add_api_route("/api/admin/cache/flush", flush_cache, methods=["POST"])


# ---------------------------------------------------------------------------
# POST /api/upload-image
# ---------------------------------------------------------------------------

async def upload_image(file: UploadFile = File(...)) -> UploadResponse:
    """
    Upload a prescription label image to S3.

    Generates a session_id (random UUID) per upload. This ID is not linked
    to any user identity — it's just a namespace for the S3 key.
    Returns the s3_key for the caller to pass into POST /api/query.
    """
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:   # 10 MB limit
        raise HTTPException(status_code=413, detail="Image too large. Maximum size is 10 MB.")

    session_id = uuid.uuid4().hex
    s3_key = s3_client.upload_image(contents, file.filename or "upload.jpg", session_id)
    return UploadResponse(s3_key=s3_key)


app.add_api_route("/api/upload-image", upload_image, methods=["POST"], response_model=UploadResponse)


# ---------------------------------------------------------------------------
# GET /api/drugs/search
# ---------------------------------------------------------------------------

def search_drugs(q: str = Query(..., min_length=1)) -> list[DrugSearchResult]:
    driver = neo4j_client.get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Drug)
            WHERE d.name CONTAINS toLower($q)
               OR any(b IN d.brand_names WHERE toLower(b) CONTAINS toLower($q))
            RETURN d.name AS name,
                   coalesce(d.brand_names, []) AS brand_names,
                   coalesce(d.drug_class, '') AS drug_class
            ORDER BY d.name
            LIMIT 20
            """,
            q=q.lower(),
        )
        return [
            DrugSearchResult(
                name=r["name"],
                brand_names=r["brand_names"],
                drug_class=r["drug_class"],
            )
            for r in result
        ]


app.add_api_route("/api/drugs/search", search_drugs, methods=["GET"], response_model=list[DrugSearchResult])


# ---------------------------------------------------------------------------
# GET /api/interactions/{drug_id}
# ---------------------------------------------------------------------------

def get_interactions(drug_id: str) -> list[InteractionResponse]:
    driver = neo4j_client.get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Drug {name: toLower($drug_id)})-[r:INTERACTS_WITH]-(other:Drug)
            RETURN d.name AS drug_a,
                   other.name AS drug_b,
                   coalesce(r.severity, 'unknown') AS severity,
                   coalesce(r.description, '') AS description,
                   coalesce(r.source_id, '') AS source_id
            ORDER BY
              CASE r.severity
                WHEN 'major'    THEN 0
                WHEN 'moderate' THEN 1
                WHEN 'minor'    THEN 2
                ELSE 3
              END
            """,
            drug_id=drug_id,
        )
        records = list(result)

    if not records:
        raise HTTPException(status_code=404, detail=f"Drug '{drug_id}' not found.")

    return [
        InteractionResponse(
            drug_a=r["drug_a"],
            drug_b=r["drug_b"],
            severity=r["severity"],
            description=r["description"],
            source_id=r["source_id"],
        )
        for r in records
    ]


app.add_api_route(
    "/api/interactions/{drug_id}",
    get_interactions,
    methods=["GET"],
    response_model=list[InteractionResponse],
)


# ---------------------------------------------------------------------------
# GET /api/graph/{drug_id}
# ---------------------------------------------------------------------------

def get_graph(drug_id: str) -> GraphResponse:
    """
    Return nodes and edges for React Flow visualization.

    Fetches the queried drug and all its direct interaction neighbors.
    The frontend uses this to render the drug relationship graph.
    """
    driver = neo4j_client.get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Drug {name: toLower($drug_id)})-[r:INTERACTS_WITH]-(other:Drug)
            RETURN d.name AS center,
                   coalesce(d.drug_class, '') AS center_class,
                   other.name AS neighbor,
                   coalesce(other.drug_class, '') AS neighbor_class,
                   coalesce(r.severity, 'unknown') AS severity,
                   coalesce(r.description, '') AS description
            """,
            drug_id=drug_id,
        )
        records = list(result)

    if not records:
        raise HTTPException(status_code=404, detail=f"Drug '{drug_id}' not found.")

    # Build deduplicated node and edge lists
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    seen_edges: set[tuple] = set()

    for r in records:
        if r["center"] not in nodes:
            nodes[r["center"]] = GraphNode(
                id=r["center"], label=r["center"], drug_class=r["center_class"]
            )
        if r["neighbor"] not in nodes:
            nodes[r["neighbor"]] = GraphNode(
                id=r["neighbor"], label=r["neighbor"], drug_class=r["neighbor_class"]
            )

        pair = tuple(sorted([r["center"], r["neighbor"]]))
        if pair not in seen_edges:
            seen_edges.add(pair)
            edges.append(GraphEdge(
                id=f"{pair[0]}-{pair[1]}",
                source=pair[0],
                target=pair[1],
                severity=r["severity"],
                description=r["description"],
            ))

    return GraphResponse(nodes=list(nodes.values()), edges=edges)


app.add_api_route(
    "/api/graph/{drug_id}",
    get_graph,
    methods=["GET"],
    response_model=GraphResponse,
)
