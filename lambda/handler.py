"""
AWS Lambda handler for weekly OpenFDA sync.

Triggered by EventBridge Scheduler on a weekly cron schedule.

Flow:
  1. Read last_sync_date from SSM Parameter Store.
  2. Call ingest_since(since_date) — fetches FDA labels updated after that date,
     skips source_ids already in Neo4j, writes new interactions to the graph.
  3. Write today's date back to SSM so next run only fetches newer labels.

Why SSM Parameter Store for state?
Lambda is stateless — it has no persistent disk. SSM is the standard AWS-native
store for small config values and lightweight state. It's free for standard
parameters and requires no extra infra (unlike DynamoDB or RDS).

Why Docker container instead of zip deployment?
The combined dependency footprint (neo4j driver + openai + requests + boto3) is
large enough to exceed the 250 MB Lambda zip limit when including transitive
dependencies. A Docker image has a 10 GB limit and also makes local testing
straightforward: `docker run --env-file .env rxgraph-sync`.

Environment variables (set in Lambda console or via SAM/CDK):
  OPENAI_API_KEY   — GPT-4o-mini extraction
  NEO4J_URI        — Neo4j Aura bolt+ssc:// URI
  NEO4J_USER       — Neo4j username (default: neo4j)
  NEO4J_PASSWORD   — Neo4j Aura password
  SSM_PARAM_NAME   — SSM parameter path (default: /rxgraph/last_sync_date)
  AWS_REGION       — AWS region for SSM client
"""

import os
import sys
from datetime import date, timedelta

# Lambda copies ingest/ and rxgraph/ to /var/task.
# Add /var/task/ingest to sys.path so openfda_ingest.py can import seed_drugs
# (a sibling module) without needing an ingest/__init__.py package structure.
sys.path.insert(0, "/var/task/ingest")

import boto3
import requests
from dotenv import load_dotenv

# load_dotenv is a no-op in Lambda (no .env file present); it only takes effect
# when running the container locally with --env-file .env for development testing.
load_dotenv()

from openfda_ingest import ingest_since  # noqa: E402  (after sys.path setup)

SSM_PARAM_NAME = os.environ.get("SSM_PARAM_NAME", "/rxgraph/last_sync_date")
DEFAULT_LOOKBACK_DAYS = 365  # first-run fallback: process up to 1 year of history
API_URL = os.environ.get("API_URL", "https://rxgraph.duckdns.org")


def handler(event, context):
    """Lambda entry point called by EventBridge on the weekly schedule."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)

    # --- Read last sync date ---
    try:
        response = ssm.get_parameter(Name=SSM_PARAM_NAME)
        last_sync_date = response["Parameter"]["Value"]
        print(f"Last sync date from SSM: {last_sync_date}")
    except ssm.exceptions.ParameterNotFound:
        last_sync_date = str(date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS))
        print(f"SSM parameter not found — using default lookback: {last_sync_date}")

    # --- Run incremental ingestion ---
    print(f"Syncing FDA labels updated since {last_sync_date}")
    stats = ingest_since(since_date=last_sync_date)
    print(f"Sync stats: {stats}")

    # --- Flush Redis cache via API ---
    # Lambda cannot reach Redis directly (different networks), so we call the
    # protected API endpoint which runs on the same host as Redis.
    if stats.get("interactions_written", 0) > 0:
        admin_secret = os.environ.get("ADMIN_SECRET", "")
        if admin_secret:
            try:
                resp = requests.post(
                    f"{API_URL}/api/admin/cache/flush",
                    headers={"Authorization": f"Bearer {admin_secret}"},
                    timeout=10,
                )
                print(f"Cache flush: {resp.json()}")
            except Exception as e:
                print(f"Cache flush failed (non-fatal): {e}")

    # --- Advance the sync cursor ---
    today = str(date.today())
    ssm.put_parameter(
        Name=SSM_PARAM_NAME,
        Value=today,
        Type="String",
        Overwrite=True,
    )
    print(f"Updated SSM {SSM_PARAM_NAME} → {today}")

    return {
        "statusCode": 200,
        "body": {
            "synced_through": today,
            **stats,
        },
    }
