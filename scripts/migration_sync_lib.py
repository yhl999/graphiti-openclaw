#!/usr/bin/env python3
"""Shared helpers for public migration/sync tooling."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path


def now_utc_iso() -> str:
    """Return a compact UTC timestamp for manifests/reports."""

    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def resolve_repo_root(cwd: Path) -> Path:
    """Resolve git repository root for a working directory."""

    result = subprocess.run(
        ['git', '-C', str(cwd), 'rev-parse', '--show-toplevel'],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def run_git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command in the repository root."""

    return subprocess.run(
        ['git', '-C', str(repo_root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def load_json(path: Path) -> dict:
    """Load JSON file with strict object expectation."""

    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'Expected JSON object in {path}')
    return payload


def dump_json(path: Path, payload: dict) -> None:
    """Write pretty JSON to disk with stable formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{json.dumps(payload, indent=2, sort_keys=True)}\n', encoding='utf-8')


def sha256_file(path: Path) -> str:
    """Compute SHA-256 digest for a file path."""

    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def repo_relative(path: Path, repo_root: Path) -> str:
    """Return a POSIX-style relative path from repo root."""

    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def collect_manifest_files(
    repo_root: Path,
    required_files: list[str],
    optional_globs: list[str],
    exclude_globs: list[str],
) -> list[Path]:
    """Collect files declared by migration manifest rules."""

    selected: dict[str, Path] = {}

    for rel in required_files:
        candidate = (repo_root / rel).resolve()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f'Required manifest file missing: {rel}')
        selected[repo_relative(candidate, repo_root)] = candidate

    for pattern in optional_globs:
        for candidate in repo_root.glob(pattern):
            if not candidate.is_file():
                continue
            rel = repo_relative(candidate, repo_root)
            selected[rel] = candidate.resolve()

    filtered = [
        candidate
        for rel, candidate in selected.items()
        if not _matches_any(rel, exclude_globs)
    ]

    return sorted(filtered, key=lambda path: repo_relative(path, repo_root))


def collect_file_entries(files: list[Path], repo_root: Path) -> list[dict]:
    """Build normalized metadata entries for a list of files."""

    entries: list[dict] = []
    for path in files:
        rel = repo_relative(path, repo_root)
        entries.append(
            {
                'path': rel,
                'sha256': sha256_file(path),
                'size_bytes': path.stat().st_size,
            },
        )
    return entries


def ensure_safe_relative(rel_path: str) -> Path:
    """Validate migration entry paths are relative and traversal-safe."""

    path = Path(rel_path)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe package entry path: {rel_path}')
    return path


def copy_entry(src_root: Path, dst_root: Path, rel_path: str) -> None:
    """Copy a relative file path from src_root to dst_root preserving structure."""

    safe_rel = ensure_safe_relative(rel_path)
    src = (src_root / safe_rel).resolve()
    dst = (dst_root / safe_rel).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
