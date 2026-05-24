"""
Shared Neo4j driver — single connection reused across the agent and ingestion.

Why a module-level singleton?
Creating a Neo4j driver is expensive (opens a connection pool). We create it
once on first use and reuse it for the lifetime of the process. This is safe
because the neo4j driver is thread-safe by design.
"""

import os

from neo4j import GraphDatabase

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "rxgraph_dev")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def close_driver():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
