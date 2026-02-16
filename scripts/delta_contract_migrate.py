#!/usr/bin/env python3
"""Migrate delta contract artifacts to the current schema policy."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from delta_contracts import validate_delta_contract_policy, validate_extension_manifest
from delta_contracts_lib.common import normalize_slug
from migration_sync_lib import load_json, resolve_repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Migrate delta contract files to current schema policy.')
    parser.add_argument('--repo', type=Path, default=Path('.'), help='Repository root or subdirectory')
    parser.add_argument('--extensions-dir', type=Path, default=Path('extensions'))
    parser.add_argument(
        '--contract-policy',
        type=Path,
        default=Path('config/delta_contract_policy.json'),
        help='Delta contract policy JSON file',
    )
    parser.add_argument('--write', action='store_true', help='Apply migrations in place')
    return parser.parse_args()


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else (repo_root / path).resolve()


def _stable_command_key(namespace: str, raw_key: str, seen: set[str]) -> str:
    candidate_suffix = normalize_slug(raw_key)
    if not candidate_suffix:
        candidate_suffix = 'command'

    counter = 1
    while True:
        suffix = candidate_suffix if counter == 1 else f'{candidate_suffix}-{counter}'
        command_key = f'{namespace}/{suffix}'
        if command_key not in seen:
            seen.add(command_key)
            return command_key
        counter += 1


def _migrate_extension_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    commands = manifest.get('commands')
    if not isinstance(commands, dict) or not commands:
        return manifest, False

    extension_name = str(manifest.get('name', '')).strip()
    namespace = normalize_slug(extension_name)
    if not namespace:
        raise ValueError('Cannot derive namespace from extension name')

    existing_contract = manifest.get('command_contract')
    contract_valid = (
        isinstance(existing_contract, dict)
        and existing_contract.get('version') == 1
        and str(existing_contract.get('namespace', '')).strip() == namespace
    )

    seen_keys: set[str] = set()
    migrated_commands: dict[str, str] = {}
    changed = False

    for key, rel_path in commands.items():
        if not isinstance(key, str) or not isinstance(rel_path, str):
            raise ValueError('Extension commands must map string keys to string paths')

        normalized_key = key.strip()
        if normalized_key.startswith(f'{namespace}/'):
            command_key = _stable_command_key(namespace, normalized_key.split('/', 1)[1], seen_keys)
            if command_key != normalized_key:
                changed = True
        else:
            command_key = _stable_command_key(namespace, normalized_key, seen_keys)
            changed = True

        migrated_commands[command_key] = rel_path

    if changed or not contract_valid:
        manifest['command_contract'] = {
            'version': 1,
            'namespace': namespace,
        }
        manifest['commands'] = migrated_commands
        changed = True

    validate_extension_manifest(manifest, context='migrated extension manifest')
    return manifest, changed


def main() -> int:
    args = parse_args()
    repo_root = resolve_repo_root(args.repo.resolve())

    extensions_dir = _resolve(repo_root, args.extensions_dir)
    policy_path = _resolve(repo_root, args.contract_policy)

    policy = validate_delta_contract_policy(load_json(policy_path), context=str(policy_path))
    target_cfg = policy.get('targets', {}).get('extension_command_contract', {})
    target_version = int(target_cfg.get('current_version', 1))
    if target_version != 1:
        raise ValueError(
            f'Unsupported target version `{target_version}` for extension command contract migration',
        )

    if not extensions_dir.exists() or not extensions_dir.is_dir():
        raise FileNotFoundError(f'Extensions directory missing: {extensions_dir}')

    changed_files: list[Path] = []
    inspected = 0

    for extension_dir in sorted(path for path in extensions_dir.iterdir() if path.is_dir()):
        manifest_path = extension_dir / 'manifest.json'
        if not manifest_path.exists():
            continue

        inspected += 1
        manifest_payload = load_json(manifest_path)
        migrated_payload, changed = _migrate_extension_manifest(dict(manifest_payload))
        if not changed:
            continue

        changed_files.append(manifest_path)
        if args.write:
            manifest_path.write_text(f'{json.dumps(migrated_payload, indent=2)}\n', encoding='utf-8')

    mode = 'WRITE' if args.write else 'DRY RUN'
    print(f'Delta contract migrate ({mode})')
    print(f'Repo: {repo_root}')
    print(f'Policy: {policy_path}')
    print(f'Extensions inspected: {inspected}')
    print(f'Extension manifests needing migration: {len(changed_files)}')

    if changed_files:
        for path in changed_files:
            print(f'- {path}')

    if not args.write:
        print('No files were modified. Re-run with --write to apply migrations.')

    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
