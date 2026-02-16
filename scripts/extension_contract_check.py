#!/usr/bin/env python3
"""Validate extension manifests for public delta-layer tooling."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from delta_contracts import validate_extension_manifest
from migration_sync_lib import load_json, resolve_safe_child


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Check extension manifest contracts.')
    parser.add_argument('--extensions-dir', type=Path, default=Path('extensions'))
    parser.add_argument('--strict', action='store_true', help='Exit non-zero when issues are found')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path.cwd().resolve()
    extensions_dir = args.extensions_dir if args.extensions_dir.is_absolute() else (repo_root / args.extensions_dir).resolve()

    if not extensions_dir.exists():
        print(f'Extensions directory missing: {extensions_dir}')
        return 1 if args.strict else 0

    issues: list[str] = []
    discovered: list[str] = []
    names: set[str] = set()

    for extension_dir in sorted(path for path in extensions_dir.iterdir() if path.is_dir()):
        manifest_path = extension_dir / 'manifest.json'
        if not manifest_path.exists():
            issues.append(f'{extension_dir.name}: missing manifest.json')
            continue

        try:
            manifest = validate_extension_manifest(load_json(manifest_path), context=str(manifest_path))
        except (FileNotFoundError, ValueError) as exc:
            issues.append(str(exc))
            continue

        normalized_name = str(manifest['name']).strip()
        if normalized_name in names:
            issues.append(f'{extension_dir.name}: duplicate extension name `{normalized_name}`')
        names.add(normalized_name)

        entrypoints = manifest.get('entrypoints', {})
        if isinstance(entrypoints, dict):
            for key, rel in entrypoints.items():
                if not isinstance(key, str) or not isinstance(rel, str):
                    continue
                try:
                    candidate = resolve_safe_child(
                        repo_root,
                        rel,
                        context=f'extension `{normalized_name}` entrypoint `{key}`',
                    )
                except ValueError as exc:
                    issues.append(str(exc))
                    continue
                if not candidate.exists() or not candidate.is_file():
                    issues.append(f'{normalized_name}: entrypoint path missing `{rel}`')

        discovered.append(normalized_name)

    if issues:
        print('Extension contract issues found:', file=sys.stderr)
        for issue in issues:
            print(f'- {issue}', file=sys.stderr)
        return 1 if args.strict else 0

    print(f'Extension contract check OK ({len(discovered)} extension(s)): {", ".join(discovered)}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
