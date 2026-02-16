#!/usr/bin/env bash
set -euo pipefail

python3 scripts/delta_tool.py contracts-check -- \
  --policy config/migration_sync_policy.json \
  --state-manifest config/state_migration_manifest.json \
  --extensions-dir extensions \
  --strict

python3 scripts/delta_tool.py sync-doctor -- \
  --repo . \
  --policy config/migration_sync_policy.json \
  --dry-run \
  --allow-missing-upstream \
  --allow-dirty \
  --output-json /tmp/upstream-sync-doctor.json

python3 scripts/delta_tool.py history-export -- \
  --repo . \
  --mode filtered-history \
  --dry-run \
  --report /tmp/filtered-history.md \
  --summary-json /tmp/filtered-history.json

python3 scripts/delta_tool.py history-export -- \
  --repo . \
  --mode clean-foundation \
  --dry-run \
  --report /tmp/clean-foundation.md \
  --summary-json /tmp/clean-foundation.json

python3 scripts/delta_tool.py history-scorecard -- \
  --filtered-summary /tmp/filtered-history.json \
  --clean-summary /tmp/clean-foundation.json \
  --policy config/migration_sync_policy.json \
  --out /tmp/history-scorecard.md \
  --summary-json /tmp/history-scorecard.json

python3 scripts/delta_tool.py state-export -- \
  --manifest config/state_migration_manifest.json \
  --out /tmp/graphiti-state-export \
  --dry-run \
  --force

python3 scripts/delta_tool.py state-check -- \
  --package /tmp/graphiti-state-export \
  --dry-run

python3 scripts/delta_tool.py state-import -- \
  --in /tmp/graphiti-state-export \
  --dry-run
