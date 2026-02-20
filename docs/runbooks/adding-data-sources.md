# Adding New Data Sources to the Knowledge Graph

This runbook covers everything needed to add a new data source to the Graphiti extraction pipeline. Each source becomes a **graph group** — an isolated namespace in Neo4j with its own ontology, ingest script, and cron schedule.

**Prerequisites:** Read [sessions-ingestion.md](sessions-ingestion.md) for architecture context. Your MCP server should be running in steady-state mode (`CONCURRENCY=1`).

---

## The Checklist

Every new data source needs exactly four things:

1. **A `group_id`** — namespace identifier for all nodes/edges from this source
2. **An ontology** — custom entity/relationship types (optional but strongly recommended)
3. **An ingest adapter script** — reads source data, formats episodes, sends to MCP
4. **A cron entry** — determines how often new data is ingested

---

## Step 1: Choose a `group_id`

Convention: `s1_<domain>_<source>` (snake_case).

Examples:
- `s1_taste_wine` — wine tasting notes and ratings
- `s1_taste_restaurants` — restaurant visits and reviews
- `s1_taste_films` — film ratings and notes
- `s1_crm_people` — CRM contact/interaction data
- `s1_gtd_tasks` — task management / GTD system
- `engineering_learnings` — CLR compound notes (no `s1_` prefix — legacy convention)

**Rules:**
- Must be unique across all groups (check `extraction_ontologies.yaml` and the groups table in `sessions-ingestion.md`)
- Keep it short but descriptive — this appears in every node/edge in Neo4j
- The `s1_` prefix is conventional for "system 1" (primary knowledge graph) but not enforced

---

## Step 2: Define an Ontology

Add a block to `mcp_server/config/extraction_ontologies.yaml`:

```yaml
s1_taste_wine:
  extraction_emphasis: >-
    Focus on tasting notes, producer style, terroir, vintage quality,
    food pairings, and price-to-quality assessments. Extract what makes
    wines memorable or disappointing.
  entity_types:
    - name: Wine
      description: "Specific wine (producer + cuvée + vintage)"
    - name: Producer
      description: "Winery or négociant"
    - name: Region
      description: "Appellation or geographic terroir"
    - name: TastingNote
      description: "Sensory descriptor or quality assessment"
  relationship_types:
    - name: PRODUCED_BY
    - name: FROM_REGION
    - name: PAIRS_WITH
```

### Ontology Design Principles

- **Be specific.** Generic entity types ("Thing", "Concept") produce noisy graphs. Domain-specific types ("Wine", "Producer") extract targeted, useful knowledge.
- **`extraction_emphasis` matters.** This is injected into the LLM prompt. Tell it what to focus on and what to ignore. Be opinionated.
- **Keep entity types to 5-10.** More types = more extraction cost + more fragmented graph. You can always add types later.
- **Relationship types are optional but valuable.** Without them, Graphiti uses generic relationships. With them, you get structured edges like `PRODUCED_BY` that enable meaningful graph traversals.
- **Groups without an explicit ontology fall back to global defaults.** This works fine for general-purpose content (session transcripts, memory files) but produces poor results for domain-specific data.

### How Ontology Resolution Works

The `OntologyRegistry` in `mcp_server/src/services/ontology_registry.py` resolves ontologies **per-episode at extraction time** (not per-shard or per-startup). This means:

1. A single MCP instance handles all groups correctly
2. You can add new ontology entries without restarting MCP (the YAML is re-read on each call)
3. Multiple groups with different ontologies can extract concurrently

### Verifying Your Ontology

After adding the YAML block:

```bash
# Verify YAML parses correctly
python3 -c "import yaml; yaml.safe_load(open('mcp_server/config/extraction_ontologies.yaml'))"

# Check that your group_id resolves
python3 -c "
from mcp_server.src.services.ontology_registry import OntologyRegistry
reg = OntologyRegistry()
profile = reg.resolve('s1_taste_wine')
print(f'Entity types: {[e.name for e in profile.entity_types]}')
print(f'Relationship types: {[r.name for r in profile.relationship_types]}')
print(f'Emphasis: {profile.extraction_emphasis[:80]}...')
"
```

---

## Step 3: Write an Ingest Adapter Script

### Pattern A: SQLite Database Source

For structured data in SQLite (taste DBs, CRM, GTD):

```python
#!/usr/bin/env python3
"""Ingest <source> into Graphiti via MCP add_memory.

Usage:
    python3 scripts/ingest_taste_wine.py [--mcp-url URL] [--dry-run]
"""

import argparse
import json
import sqlite3
import sys
import urllib.request
from uuid import uuid5, NAMESPACE_URL

GROUP_ID = "s1_taste_wine"
DEFAULT_MCP_URL = "http://localhost:8000/mcp"
MAX_BODY_CHARS = 10_000  # sub-chunk above this


def add_memory(mcp_url: str, name: str, body: str, group_id: str, source_desc: str) -> None:
    """Send one episode to MCP add_memory."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "add_memory",
            "arguments": {
                "name": name,
                "episode_body": body,
                "group_id": group_id,
                "source_description": source_desc,
            },
        },
    }
    req = urllib.request.Request(
        mcp_url,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
        if "error" in result:
            raise RuntimeError(f"MCP error: {result['error']}")


def sub_chunk(text: str, max_chars: int = MAX_BODY_CHARS) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    ap.add_argument("--db", default="/Users/archibald/clawd/data/taste/wine.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT id, name, notes, rating, region FROM wines").fetchall()

    print(f"Found {len(rows)} wines to ingest")

    for row in rows:
        body = f"Wine: {row['name']}\nRegion: {row['region']}\nRating: {row['rating']}\nNotes: {row['notes']}"

        # Deterministic UUID from source ID → idempotent re-runs
        chunk_key = str(uuid5(NAMESPACE_URL, f"wine:{row['id']}"))

        chunks = sub_chunk(body)
        for i, chunk in enumerate(chunks):
            name = row["name"]
            if len(chunks) > 1:
                name = f"{name} (part {i + 1}/{len(chunks)})"

            if args.dry_run:
                print(f"  [dry-run] {name} ({len(chunk)} chars)")
                continue

            add_memory(
                args.mcp_url,
                name=name,
                body=chunk,
                group_id=GROUP_ID,
                source_desc=f"wine_db:{row['id']}",
            )
            print(f"  Queued: {name}")

    print(f"Done: {len(rows)} wines {'would be' if args.dry_run else ''} ingested")


if __name__ == "__main__":
    main()
```

### Pattern B: JSONL / Evidence File Source

For pre-parsed evidence files (ChatGPT exports, session transcripts):

```python
# Use mcp_ingest_sessions.py with:
#   --group-id <your_group_id>
#   --evidence <path/to/evidence.json>
#   --force  (for initial load)

# Evidence JSON format:
# [
#   {
#     "name": "Episode title",
#     "episode_body": "Content to extract from...",
#     "source_description": "source:identifier",
#     "reference_time": "2026-02-20T00:00:00Z"
#   },
#   ...
# ]
```

### Pattern C: Compound Notes / Markdown Source

For markdown files that need sectional chunking (engineering learnings, memory files):

```python
# Use ingest_compound_notes.py as a template.
# Key: split at H2 headers, deterministic chunk keys from file path + section index.
```

### Adapter Design Principles

| Principle | Why |
|-----------|-----|
| **Deterministic chunk keys** from source IDs | Idempotent re-runs — same input never creates duplicates in the registry |
| **Sub-chunk large content** (>10k chars) | Prevents `context_length_exceeded` from the LLM; sub-chunks get `:p0/:p1/...` key suffixes |
| **Track cursor/watermark** for incremental ingest | Don't re-process the entire source every run — track the last-seen ID, timestamp, or file mtime |
| **Validate body size** against `MAX_EPISODE_BODY_CHARS` (12k default) | Episodes exceeding this are silently truncated by the MCP server |
| **Include `source_description`** | Provenance tracking — know where every episode came from |
| **Handle errors gracefully** | Network timeouts to MCP are normal under load; retry with backoff |

---

## Step 4: Set Up Cron Schedule

Choose frequency based on how often the source data changes:

| Source Type | Recommended Frequency | Rationale |
|-------------|----------------------|-----------|
| Taste DBs (wine, restaurants, films) | Daily | Data changes infrequently; nightly sync is sufficient |
| CRM / interactions | Hourly or on-event | Captures meeting notes and contacts promptly |
| GTD / tasks | Every 30 min | Task status changes are time-sensitive for context |
| Session transcripts | Every 30 min | Delta ingestion with watermark tracking |
| Engineering learnings | After CLR runs (hourly) | Triggered by new compound notes |
| Self-audit | Nightly | Low volume, batch is fine |
| Curated snapshots | Nightly | SHA-256 hash-gated; only runs when files change |

### Creating the Cron Job

```bash
openclaw cron add \
  --name "graphiti-ingest-taste-wine" \
  --description "Daily wine DB ingest to knowledge graph" \
  --cron "30 4 * * *" \
  --tz "America/New_York" \
  --session main \
  --wake next-heartbeat \
  --system-event "Ingest wine DB: cd /Users/archibald/clawd/tools/graphiti && export \$(grep -v '^#' ~/.clawdbot/credentials/neo4j.env | xargs) && .venv-native/bin/python scripts/ingest_taste_wine.py --mcp-url http://localhost:8000/mcp"
```

---

## Step 5: Verify the Integration

After the first ingest run:

### Check Episode Counts

```bash
# Should show your new group_id with expected episode count
export $(grep -v '^#' ~/.clawdbot/credentials/neo4j.env | xargs)
python3 -c "
import json, base64, urllib.request
auth = base64.b64encode('neo4j:$NEO4J_PASSWORD'.encode()).decode()
req = urllib.request.Request(
    'http://localhost:7474/db/neo4j/tx/commit',
    json.dumps({'statements': [{'statement':
        \"MATCH (e:Episodic) WHERE e.group_id = 's1_taste_wine' RETURN count(e) AS cnt\"
    }]}).encode(),
    {'Content-Type': 'application/json', 'Authorization': f'Basic {auth}'},
)
data = json.loads(urllib.request.urlopen(req).read())
print(data['results'][0]['data'][0]['row'][0])
"
```

### Check Entity Extraction Quality

```bash
# Sample entities created by your ontology
MATCH (n:Entity {group_id: 's1_taste_wine'})
RETURN n.name, labels(n), n.entity_type
ORDER BY n.name LIMIT 20
```

### Contamination Check

```bash
# Should return 0 — no cross-group edges
MATCH (a)-[r:RELATES_TO]->(b)
WHERE a.group_id = 's1_taste_wine' AND b.group_id <> 's1_taste_wine'
RETURN count(r)
```

### Update Tracking

After confirming the integration works:

1. **Add to `extraction_monitor.py`** — add your group to the `GROUPS` list with expected target count
2. **Add to `graph_maintenance.py`** — add your group_id to `ALL_GROUPS` (and `CANDIDATES_GROUPS` if applicable)
3. **Update documentation** — add a row to the Graph Groups table in `sessions-ingestion.md` and the private README

---

## Reference: Existing Ingest Scripts

| Script | Source Type | Groups Using It |
|--------|-----------|-----------------|
| `mcp_ingest_sessions.py` | Evidence JSON (sessions, ChatGPT, memory) | sessions_main, chatgpt_history, memory_day1 |
| `ingest_content_groups.py` | Content batch evidence (sequential with drain) | inspiration_*, writing_samples, content_strategy |
| `ingest_compound_notes.py` | Markdown compound notes | engineering_learnings |
| `mcp_ingest_self_audit.py` | JSONL self-audit entries | learning_self_audit |
| `curated_snapshot_ingest.py` | Curated markdown snapshots (hash-gated) | curated_refs |

When writing a new adapter, start from the script closest to your source type. Copy the patterns (error handling, sub-chunking, cursor tracking) rather than writing from scratch.
