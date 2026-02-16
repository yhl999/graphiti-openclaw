#!/usr/bin/env python3
"""Single entrypoint for delta-layer migration/sync tooling."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COMMAND_TO_SCRIPT = {
    'boundary-audit': 'public_repo_boundary_audit.py',
    'boundary-lint': 'public_boundary_policy_lint.py',
    'contracts-check': 'delta_contract_check.py',
    'extension-check': 'extension_contract_check.py',
    'sync-doctor': 'upstream_sync_doctor.py',
    'history-export': 'public_history_export.py',
    'history-scorecard': 'public_history_scorecard.py',
    'state-export': 'state_migration_export.py',
    'state-check': 'state_migration_check.py',
    'state-import': 'state_migration_import.py',
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run delta-layer tools via a single stable command surface.',
    )
    parser.add_argument('command', choices=sorted(COMMAND_TO_SCRIPT))
    parser.add_argument(
        'tool_args',
        nargs=argparse.REMAINDER,
        help='Arguments forwarded to the selected tool. Prefix with `--` when needed.',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    forwarded = list(args.tool_args)
    if forwarded and forwarded[0] == '--':
        forwarded = forwarded[1:]

    scripts_dir = Path(__file__).resolve().parent
    script_path = scripts_dir / COMMAND_TO_SCRIPT[args.command]

    result = subprocess.run([sys.executable, str(script_path), *forwarded], check=False)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
