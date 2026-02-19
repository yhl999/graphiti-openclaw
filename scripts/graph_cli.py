"""Thin CLI abstraction over Neo4j (cypher-shell) and FalkorDB (redis-cli).

Provides a unified interface so scripts can run Cypher queries, health checks,
and DB size lookups against either backend without caring about the transport.
"""

from __future__ import annotations

import os
import re
import subprocess

SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")

# Neo4j defaults
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")
NEO4J_HOST = os.environ.get("NEO4J_HOST", "localhost")
NEO4J_PORT = os.environ.get("NEO4J_PORT", "7687")
CYPHER_SHELL = os.environ.get("CYPHER_SHELL", "cypher-shell")

# FalkorDB defaults
REDIS_CLI = os.environ.get("REDIS_CLI", "/opt/homebrew/opt/redis/bin/redis-cli")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")


def run_cypher(backend: str, graph_name: str, query: str, *, timeout: int = 30) -> str:
    """Execute a Cypher query and return raw text output.

    For Neo4j: uses cypher-shell against a single database.
    For FalkorDB: uses redis-cli GRAPH.QUERY against the named graph.
    """
    if not SAFE_NAME_RE.match(graph_name):
        raise ValueError(f"unsafe graph name: {graph_name!r}")

    if backend == "neo4j":
        cmd = [
            CYPHER_SHELL,
            "-u", NEO4J_USER,
            "-p", NEO4J_PASSWORD,
            "-d", NEO4J_DATABASE,
            "-a", f"bolt://{NEO4J_HOST}:{NEO4J_PORT}",
            "--format", "plain",
            query,
        ]
        return subprocess.check_output(cmd, text=True, timeout=timeout)

    if backend == "falkordb":
        cmd = [REDIS_CLI, "-p", REDIS_PORT, "GRAPH.QUERY", graph_name, query]
        return subprocess.check_output(cmd, text=True, timeout=timeout)

    raise ValueError(f"unknown backend: {backend!r}")


def check_health(backend: str, *, timeout: int = 2) -> bool:
    """Return True if the database responds to a basic health probe."""
    try:
        if backend == "neo4j":
            cmd = [
                CYPHER_SHELL,
                "-u", NEO4J_USER,
                "-p", NEO4J_PASSWORD,
                "-d", NEO4J_DATABASE,
                "-a", f"bolt://{NEO4J_HOST}:{NEO4J_PORT}",
                "--format", "plain",
                "RETURN 1;",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0

        if backend == "falkordb":
            r = subprocess.run(
                [REDIS_CLI, "-p", REDIS_PORT, "PING"],
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout.strip() == "PONG"

        raise ValueError(f"unknown backend: {backend!r}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_db_size(backend: str, *, timeout: int = 10) -> int:
    """Return a rough node count for the database."""
    try:
        if backend == "neo4j":
            out = run_cypher(backend, "neo4j", "MATCH (n) RETURN count(n);", timeout=timeout)
            for line in out.splitlines():
                s = line.strip()
                if s.isdigit():
                    return int(s)
            return 0

        if backend == "falkordb":
            r = subprocess.check_output(
                [REDIS_CLI, "-p", REDIS_PORT, "DBSIZE"],
                text=True, timeout=timeout,
            )
            # Output: "(integer) 42" or just "42"
            for token in r.split():
                if token.isdigit():
                    return int(token)
            return 0

        raise ValueError(f"unknown backend: {backend!r}")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return 0


def list_graphs(backend: str, *, timeout: int = 10) -> list[str]:
    """Return the list of graph names available in the backend.

    For Neo4j: queries distinct group_id values from Episodic nodes.
    For FalkorDB: uses GRAPH.LIST.
    """
    if backend == "neo4j":
        out = run_cypher(
            backend, "neo4j",
            "MATCH (e:Episodic) WHERE e.group_id IS NOT NULL "
            "RETURN DISTINCT e.group_id ORDER BY e.group_id;",
            timeout=timeout,
        )
        return [
            line.strip().strip('"')
            for line in out.splitlines()
            if line.strip() and SAFE_NAME_RE.match(line.strip().strip('"'))
        ]

    if backend == "falkordb":
        raw = subprocess.check_output(
            [REDIS_CLI, "-p", REDIS_PORT, "GRAPH.LIST"],
            text=True, timeout=timeout,
        )
        return [
            x.strip()
            for x in raw.splitlines()
            if x.strip() and SAFE_NAME_RE.match(x.strip())
        ]

    raise ValueError(f"unknown backend: {backend!r}")


def parse_count(backend: str, output: str) -> int:
    """Parse a single integer result from query output.

    cypher-shell (plain) emits just the number on its own line.
    redis-cli GRAPH.QUERY emits a header then the number.
    """
    lines = output.splitlines()
    start = 1 if backend == "falkordb" else 0
    for line in lines[start:]:
        s = line.strip()
        if s.isdigit():
            return int(s)
    return 0
