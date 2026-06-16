# RxGraph

**Live demo: http://rxgraph.duckdns.org**

A drug interaction knowledge graph agent. Upload a prescription label or type medication names — RxGraph queries a Neo4j knowledge graph populated from FDA data and returns a structured safety report with source citations and an interactive drug relationship graph.

## What makes it different from ChatGPT

| | ChatGPT | RxGraph |
|---|---|---|
| Data freshness | Knowledge cutoff | Synced from FDA |
| Answer traceability | Generated text, no source | Every claim linked to FDA record |
| Multi-drug reasoning | Text inference | Graph traversal, deterministic |
| Severity | Inferred | Stored as-is from source; never guessed |

## Architecture

```
React Frontend
      │ REST
FastAPI Backend
      │
LangGraph Agent (5 nodes)
  1. Entity Extraction     — GPT-4o-mini (text) or GPT-4o vision (image)
  2. Normalization         — Neo4j lookup + RxNorm fallback
  3. Graph Query           — Cypher pairwise interaction + contraindication query
  4. Safety Guardrail      — drop unsupported claims, rank by severity
  5. Report Generation     — GPT-4o-mini narrates Neo4j results
      │
Neo4j Knowledge Graph
      │
OpenFDA Ingestion Pipeline
```

## Stack

- **Agent**: LangGraph, OpenAI GPT-4o / GPT-4o-mini
- **Graph DB**: Neo4j 5
- **Backend**: FastAPI, Python 3.14
- **Frontend**: React, TypeScript, React Flow, Tailwind CSS
- **Storage**: AWS S3 (prescription label images + raw FDA data)
- **Drug normalization**: RxNorm API (NIH)
- **Data source**: OpenFDA API

## Local Setup

### Prerequisites
- Docker Desktop
- Python 3.11+
- Node.js 20+
- AWS account (for image upload)
- OpenAI API key

### 1. Start Neo4j

```bash
docker compose up -d
```

Neo4j browser: `http://localhost:7474` (login: `neo4j` / `rxgraph_dev`)

### 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment variables

```bash
cp .env.example .env
# fill in OPENAI_API_KEY, AWS credentials, Neo4j password
```

### 4. Ingest FDA drug interaction data

```bash
cd ingest
OPENAI_API_KEY=sk-... python openfda_ingest.py
```

Populates Neo4j with ~650 drug nodes and ~1,700 interactions from FDA labels.

### 5. Start the API

```bash
uvicorn api.main:app --reload
```

API docs: `http://localhost:8000/docs`

### 6. Start the frontend

```bash
cd frontend
npm install
npm start
```

App: `http://localhost:3000`

## Key Design Decisions

**Why Neo4j instead of PostgreSQL?**
Multi-drug interaction queries are graph traversal problems. A 3-drug combination check in Cypher is a single pattern match; in SQL it requires complex self-joins. Phase 2's multi-hop reasoning (A affects B's metabolism, B interacts with C) is native to graph databases and impractical in relational SQL.

**Why LangGraph instead of a plain pipeline?**
The safety guardrail is a routing gate — it must block output if any claim lacks a `source_id`. Modeling it as an explicit node with a conditional edge makes it independently testable and impossible to accidentally skip.

**Why is severity never inferred by the LLM?**
FDA label text rarely uses the words "major/moderate/minor" explicitly — ~92% of interactions in the current dataset have `severity: "unknown"`. A hallucinated severity in a medical context is a patient harm risk. Surfacing "unknown" explicitly is the safe behavior.

**Why is session context never persisted?**
Conditions, allergies, and age group are passed as Cypher parameters at query time and exist only in memory for that request. Medication combinations are sensitive health data — storing them without explicit user consent is a privacy risk.

## Graph Schema

```
(:Drug)-[:INTERACTS_WITH {severity, description, source_id}]->(:Drug)
(:Drug)-[:CONTRAINDICATED_WITH]->(:Condition)
(:Drug)-[:USE_WITH_CAUTION_IN]->(:Condition)
(:Drug)-[:BELONGS_TO]->(:DrugClass)
(:Drug)-[:ACTS_VIA]->(:Mechanism)
```

## Running Tests

```bash
.venv/bin/pytest tests/ -v
```

## Data Coverage (Phase 1)

- ~650 drug nodes (96 seeded + stub nodes from interaction text)
- ~1,700 interactions
- Source: OpenFDA drug label API
- Severity coverage: ~8% explicit (major/moderate/minor), ~92% unknown

Phase 2 will migrate to DrugBank (1.4M+ interactions, structured severity fields).

## Roadmap

- [x] Scheduled AWS Lambda + EventBridge sync for new FDA approvals
- [x] Deploy to AWS Lightsail with Docker
- [ ] DrugBank migration — structured severity + mechanism data
- [ ] Multi-hop interaction chain analysis (A→B→C indirect risk)
- [x] Redis caching for frequent drug pair queries
- [ ] Multilingual OCR support
