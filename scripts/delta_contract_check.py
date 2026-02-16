#!/usr/bin/env python3
"""Validate delta-layer config and extension contracts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from delta_contracts import (
    validate_extension_manifest,
    validate_migration_sync_policy,
    validate_state_migration_manifest,
)
from migration_sync_lib import load_json, resolve_repo_root, resolve_safe_child


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Validate delta-layer contract files.')
    parser.add_argument('--repo', type=Path, default=Path('.'), help='Repository root or subdirectory')
    parser.add_argument(
        '--policy',
        type=Path,
        default=Path('config/migration_sync_policy.json'),
        help='Migration/sync policy JSON path',
    )
    parser.add_argument(
        '--state-manifest',
        type=Path,
        default=Path('config/state_migration_manifest.json'),
        help='State migration manifest JSON path',
    )
    parser.add_argument('--extensions-dir', type=Path, default=Path('extensions'))
    parser.add_argument('--strict', action='store_true', help='Exit non-zero when issues are found')
    return parser.parse_args()


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else (repo_root / path).resolve()


def main() -> int:
    args = parse_args()
    repo_root = resolve_repo_root(args.repo.resolve())

    policy_path = _resolve(repo_root, args.policy)
    manifest_path = _resolve(repo_root, args.state_manifest)
    extensions_dir = _resolve(repo_root, args.extensions_dir)

    issues: list[str] = []
    extension_count = 0

    try:
        validate_migration_sync_policy(
            load_json(policy_path),
            context=str(policy_path),
            strict=True,
        )
    except (FileNotFoundError, ValueError) as exc:
        issues.append(str(exc))

    try:
        validate_state_migration_manifest(load_json(manifest_path), context=str(manifest_path))
    except (FileNotFoundError, ValueError) as exc:
        issues.append(str(exc))

    if not extensions_dir.exists() or not extensions_dir.is_dir():
        issues.append(f'Extensions directory missing: {extensions_dir}')
    else:
        for extension_dir in sorted(path for path in extensions_dir.iterdir() if path.is_dir()):
            extension_count += 1
            extension_manifest_path = extension_dir / 'manifest.json'
            if not extension_manifest_path.exists():
                issues.append(f'{extension_dir.name}: missing manifest.json')
                continue

            try:
                extension_manifest = validate_extension_manifest(
                    load_json(extension_manifest_path),
                    context=str(extension_manifest_path),
                )
            except (FileNotFoundError, ValueError) as exc:
                issues.append(str(exc))
                continue

            entrypoints = extension_manifest.get('entrypoints', {})
            if not isinstance(entrypoints, dict):
                issues.append(f'{extension_manifest_path}: entrypoints must be an object')
                continue

            for entry_name, rel_path in entrypoints.items():
                if not isinstance(entry_name, str) or not isinstance(rel_path, str):
                    continue
                try:
                    candidate = resolve_safe_child(
                        repo_root,
                        rel_path,
                        context=(
                            f'extension `{extension_manifest.get("name", extension_dir.name)}` '
                            f'entrypoint `{entry_name}`'
                        ),
                    )
                except ValueError as exc:
                    issues.append(str(exc))
                    continue

                if not candidate.exists() or not candidate.is_file():
                    issues.append(
                        f'{extension_manifest_path}: entrypoint path missing `{rel_path}`',
                    )

    if issues:
        print('Delta contract check: issues found', file=sys.stderr)
        for issue in issues:
            print(f'- {issue}', file=sys.stderr)
        return 1 if args.strict else 0

    print(
        'Delta contract check OK '
        f'(policy={policy_path.name}, state_manifest={manifest_path.name}, extensions={extension_count})',
    )
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
