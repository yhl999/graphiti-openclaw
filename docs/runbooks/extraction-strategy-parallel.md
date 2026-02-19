# Extraction Strategy: Parallel "Speed Run"

**Status:** Experimental / High-Risk
**Goal:** Maximize ingestion throughput to clear large backlogs (3000+ episodes).
**Trade-off:** Speed vs Data Integrity (requires cleanup).

## Overview

By default, Graphiti processes episodes sequentially to ensure:
1.  **Deduplication:** Avoiding duplicate entities via `dedupe_nodes_bulk` (which has race conditions under parallelism).
2.  **Timeline Continuity:** Linking `NEXT_EPISODE` edges to the most recent episode.

The "Speed Run" strategy breaks this sequentiality to parallelize the high-latency LLM extraction step.

## Configuration

Enable via environment variable in `queue_service.py`:
```bash
GRAPHITI_QUEUE_CONCURRENCY=10
```
(Default is 1, strictly serial).

With 4 shards and `concurrency=10`, we achieve **40 parallel pipelines**.

## Risks & Mitigations

| Risk | Consequence | Mitigation (Post-Process) |
|------|-------------|---------------------------|
| **Duplicate Entities** | Multiple nodes for "Yuan" or "Crypto" due to race conditions. | Run `dedupe_nodes.py` to merge duplicates by name/group. |
| **Broken Timeline** | Episodes form disconnected islands instead of a linear chain. | Run `repair_timeline.py` to sort by time and relink `NEXT_EPISODE` edges. |
| **Context Loss** | Episode B cannot see Episode A (processed concurrently) for coref resolution. | Accepted loss. Use large chunk sizes (10k chars) to minimize dependency. |

## Execution Protocol

1.  **Set Concurrency:** `export GRAPHITI_QUEUE_CONCURRENCY=10`
2.  **Launch Shards:** 4-6 shards recommended (e.g. ports 8000-8005).
3.  **Monitor:** Watch for FalkorDB PING timeouts. If frequent, reduce concurrency.
4.  **Cleanup (Mandatory):**
    *   Stop ingestion.
    *   Run `dedupe_nodes.py`.
    *   Run `repair_timeline.py`.
    *   Run `reindex_search.py` (optional, if using external search).

## Code Changes

- **Queue Service:** Patched to use `asyncio.Semaphore` and `asyncio.create_task` instead of `await process_func()`.
- **Driver:** FalkorDB driver does *not* enforce unique constraints, hence the need for application-side deduplication logic or cleanup.
