#!/usr/bin/env python3
"""Graphiti extraction progress monitor (Neo4j backend).

Outputs a Telegram-ready status message.

Design goals:
- Query Neo4j for episode counts per group_id.
- Fast: uses a single Cypher query + lightweight HTTP health checks.
- Fallback: caches last-known counts so transient Neo4j issues don't lose state.
- Exit code is always 0 (alerts are in the message body, not the exit code).

Usage:
    python3 scripts/extraction_monitor.py

Environment:
    NEO4J_URI      (default: bolt://localhost:7687)
    NEO4J_USER     (default: neo4j)
    NEO4J_PASSWORD (required)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_HTTP_PORT = os.environ.get("NEO4J_HTTP_PORT", "7474")

# Target episode counts per group (update as new sources are added).
GROUPS = [
    ("s1_sessions_main", 5125),
    ("s1_chatgpt_history", 543),
    ("s1_memory_day1", 604),
    ("s1_inspiration_long_form", 491),
    ("s1_inspiration_short_form", 190),
    ("s1_writing_samples", 48),
    ("s1_content_strategy", 6),
    ("engineering_learnings", 80),
    ("learning_self_audit", 22),
    ("s1_curated_refs", 4),
]

MCP_PORTS = [8000, 8001, 8002, 8003]


def _clawd_root() -> Path:
    return Path(__file__).resolve().parents[3]


CACHE_PATH = (
    _clawd_root()
    / "projects"
    / "graphiti-openclaw-runtime"
    / "state"
    / "extraction_monitor_cache.json"
)


def load_cache() -> dict:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text())
    except Exception:
        pass
    return {}


def save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True))
        tmp.replace(CACHE_PATH)
    except Exception:
        pass


def fmt_time(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return "?"


def query_neo4j_counts() -> dict[str, int] | None:
    """Query Neo4j for episode counts per group_id via HTTP API.

    Returns dict of {group_id: count} or None on failure.
    """
    if not NEO4J_PASSWORD:
        return None

    import base64
    import urllib.request
    import urllib.error

    url = f"http://localhost:{NEO4J_HTTP_PORT}/db/neo4j/tx/commit"
    payload = json.dumps({
        "statements": [{
            "statement": "MATCH (e:Episodic) RETURN e.group_id AS gid, count(e) AS cnt"
        }]
    }).encode()

    auth = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            counts = {}
            for row in data.get("results", [{}])[0].get("data", []):
                gid, cnt = row["row"]
                if gid:
                    counts[gid] = cnt
            return counts
    except Exception:
        return None


def check_neo4j_health() -> tuple[bool, str | None]:
    """Check if Neo4j is responsive."""
    if not NEO4J_PASSWORD:
        return False, "NEO4J_PASSWORD not set"

    import base64
    import urllib.request
    import urllib.error

    url = f"http://localhost:{NEO4J_HTTP_PORT}/"
    auth = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASSWORD}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})

    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return True, None
            return False, f"HTTP {resp.status}"
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


def main() -> None:
    now_hhmm = datetime.now().strftime("%I:%M %p")
    now_day = datetime.now().strftime("%a")
    lines: list[str] = [f"📊 Graphiti extraction — {now_hhmm} ({now_day})"]

    cache = load_cache()
    cache.setdefault("graphs", {})
    cache["last_run_at"] = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    # --- 1) Neo4j health ---
    db_alive, err = check_neo4j_health()
    if db_alive:
        lines.append("Neo4j: ✅ alive")
        cache["last_ping_ok_at"] = cache["last_run_at"]
    else:
        last_ok = fmt_time(cache.get("last_ping_ok_at"))
        lines.append(f"Neo4j: ⚠️ {err or 'unreachable'} — last OK at {last_ok}")

    # --- 2) Episode counts ---
    lines.append("")

    counts = query_neo4j_counts() if db_alive else None

    total_done = 0
    total_target = 0
    all_complete = True

    for graph, target in GROUPS:
        total_target += target
        count = (counts or {}).get(graph)

        if count is not None:
            cache["graphs"][graph] = {
                "count": count,
                "target": target,
                "at": cache["last_run_at"],
            }
            total_done += count
            if count >= target:
                lines.append(f"  {graph}: ✅ {count}/{target}")
            else:
                pct = int(count / target * 100)
                lines.append(f"  {graph}: 🔄 {count}/{target} ({pct}%)")
                all_complete = False
        else:
            # Fallback to cache
            last = cache["graphs"].get(graph, {})
            last_count = last.get("count")
            last_at = fmt_time(last.get("at"))
            if last_count is not None:
                total_done += last_count
                if last_count >= target:
                    lines.append(f"  {graph}: ✅ {last_count}/{target} (cached {last_at})")
                else:
                    pct = int(last_count / target * 100)
                    lines.append(f"  {graph}: ⏳ {last_count}/{target} ({pct}%) (cached {last_at})")
                    all_complete = False
            else:
                lines.append(f"  {graph}: ❓ no data")
                all_complete = False

    # Summary line
    lines.insert(2, "")
    overall_pct = int(total_done / total_target * 100) if total_target else 0
    if all_complete:
        lines.insert(3, f"Overall: ✅ {total_done:,}/{total_target:,} — COMPLETE")
    else:
        lines.insert(3, f"Overall: 🔄 {total_done:,}/{total_target:,} ({overall_pct}%)")

    # --- 3) Graph size ---
    if counts is not None:
        try:
            import base64
            import urllib.request

            url = f"http://localhost:{NEO4J_HTTP_PORT}/db/neo4j/tx/commit"
            auth = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASSWORD}".encode()).decode()

            payload = json.dumps({
                "statements": [
                    {"statement": "MATCH (n) RETURN count(n) AS cnt"},
                    {"statement": "MATCH ()-[r]->() RETURN count(r) AS cnt"},
                ]
            }).encode()
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                nodes = data["results"][0]["data"][0]["row"][0]
                rels = data["results"][1]["data"][0]["row"][0]
                lines.append("")
                lines.append(f"Graph: {nodes:,} nodes / {rels:,} rels")
        except Exception:
            pass

    save_cache(cache)

    # --- 4) MCP health ---
    lines.append("")
    mcp_up = 0
    mcp_down = 0
    mcp_status: list[str] = []
    for port in MCP_PORTS:
        try:
            r = subprocess.run(
                [
                    "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                    "--max-time", "0.8", f"http://localhost:{port}/health",
                ],
                capture_output=True, text=True, timeout=1,
            )
            code = r.stdout.strip()
            if code == "200":
                mcp_status.append(f"{port}:✅")
                mcp_up += 1
            else:
                mcp_status.append(f"{port}:🔴")
                mcp_down += 1
        except Exception:
            mcp_status.append(f"{port}:🔴")
            mcp_down += 1

    lines.append("MCP: " + " ".join(mcp_status))

    # --- 5) Enqueue driver processes ---
    try:
        r1 = subprocess.run(
            ["pgrep", "-f", r"mcp_ingest_sessions\.py"],
            capture_output=True, text=True, timeout=1,
        )
        sessions_drivers = len(r1.stdout.strip().splitlines()) if r1.stdout.strip() else 0

        r2 = subprocess.run(
            ["pgrep", "-f", r"ingest_compound_notes\.py"],
            capture_output=True, text=True, timeout=1,
        )
        compound_drivers = len(r2.stdout.strip().splitlines()) if r2.stdout.strip() else 0

        r3 = subprocess.run(
            ["pgrep", "-f", r"ingest_content_groups\.py"],
            capture_output=True, text=True, timeout=1,
        )
        content_drivers = len(r3.stdout.strip().splitlines()) if r3.stdout.strip() else 0

        parts = []
        if sessions_drivers:
            parts.append(f"sessions={sessions_drivers}")
        if compound_drivers:
            parts.append(f"compound={compound_drivers}")
        if content_drivers:
            parts.append(f"content={content_drivers}")

        if parts:
            lines.append("Drivers: " + " ".join(parts))
        else:
            lines.append("Drivers: none (MCP draining async)")
    except Exception:
        lines.append("Drivers: unknown")

    print("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    main()
