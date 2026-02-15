#!/usr/bin/env python3
"""Generate migration-candidate reports for public history cutover planning."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from migration_sync_lib import dump_json, now_utc_iso, resolve_repo_root, run_git
from public_boundary_policy import (
    ALLOW,
    AMBIGUOUS,
    BLOCK,
    classify_path,
    collect_git_files,
    read_yaml_list,
    summarize_decisions,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate migration candidate report for public history cutover.')
    parser.add_argument('--repo', type=Path, default=Path('.'), help='Repository root or subdirectory')
    parser.add_argument('--mode', choices=('filtered-history', 'clean-foundation'), required=True)
    parser.add_argument(
        '--manifest',
        type=Path,
        default=Path('config/public_export_allowlist.yaml'),
        help='Allowlist policy path',
    )
    parser.add_argument(
        '--denylist',
        type=Path,
        default=Path('config/public_export_denylist.yaml'),
        help='Denylist policy path',
    )
    parser.add_argument('--report', type=Path, required=True, help='Markdown output path')
    parser.add_argument('--summary-json', type=Path, help='Optional JSON summary output path')
    parser.add_argument('--dry-run', action='store_true', help='Emit planning report only (no git rewrites)')
    return parser.parse_args()


def _git_count(repo_root: Path, *args: str) -> int:
    result = run_git(repo_root, *args, check=False)
    if result.returncode != 0:
        return 0
    output = result.stdout.strip()
    if output.isdigit():
        return int(output)
    lines = [line for line in output.splitlines() if line.strip()]
    return len(lines)


def _calc_filtered_metrics(commit_count: int, block_count: int, ambiguous_count: int) -> dict[str, int]:
    privacy = max(0, 100 - (block_count * 35) - int(ambiguous_count * 0.5))
    simplicity = max(0, 100 - min(commit_count // 15, 35) - int(ambiguous_count * 0.3))
    merge_conflict = max(0, 100 - min(commit_count // 20, 30) - int(ambiguous_count * 0.2))
    auditability = max(0, 100 - (block_count * 20) - int(ambiguous_count * 0.4))
    return {
        'privacy_risk': privacy,
        'simplicity': simplicity,
        'merge_conflict_risk': merge_conflict,
        'auditability': auditability,
    }


def _calc_clean_metrics(commit_count: int) -> dict[str, int]:
    # Clean-foundation starts from curated baseline; history complexity is intentionally lower.
    simplicity_bonus = min(commit_count // 100, 6)
    return {
        'privacy_risk': 97,
        'simplicity': 90 + simplicity_bonus,
        'merge_conflict_risk': 92,
        'auditability': 90,
    }


def main() -> int:
    args = parse_args()
    repo_root = resolve_repo_root(args.repo.resolve())

    manifest_path = args.manifest if args.manifest.is_absolute() else (repo_root / args.manifest).resolve()
    denylist_path = args.denylist if args.denylist.is_absolute() else (repo_root / args.denylist).resolve()
    report_path = args.report if args.report.is_absolute() else (Path.cwd() / args.report).resolve()

    allowlist = read_yaml_list(manifest_path, 'allowlist')
    denylist = read_yaml_list(denylist_path, 'denylist')

    files = collect_git_files(repo_root, include_untracked=False)
    decisions = [classify_path(path, allowlist=allowlist, denylist=denylist) for path in files]
    counts, blocked, ambiguous = summarize_decisions(decisions)

    commit_count = _git_count(repo_root, 'rev-list', '--count', 'HEAD')
    author_count = _git_count(repo_root, 'shortlog', '-sn', 'HEAD')

    if args.mode == 'filtered-history':
        metrics = _calc_filtered_metrics(commit_count, len(blocked), len(ambiguous))
        unresolved_high = len(blocked) > 0
        rationale = (
            'Filtered-history keeps more provenance but inherits policy ambiguity and privacy review burden.'
        )
    else:
        metrics = _calc_clean_metrics(commit_count)
        unresolved_high = False
        rationale = 'Clean-foundation minimizes carry-over complexity and should reduce long-term merge friction.'

    summary = {
        'mode': args.mode,
        'generated_at': now_utc_iso(),
        'dry_run': bool(args.dry_run),
        'repo_root': str(repo_root),
        'policy': {
            'allowlist': str(manifest_path),
            'denylist': str(denylist_path),
        },
        'git': {
            'commit_count': commit_count,
            'author_count': author_count,
        },
        'boundary_counts': {
            ALLOW: counts[ALLOW],
            BLOCK: counts[BLOCK],
            AMBIGUOUS: counts[AMBIGUOUS],
        },
        'metrics': metrics,
        'risk_flags': {
            'unresolved_high': unresolved_high,
        },
        'rationale': rationale,
    }

    report_lines = [
        f'# Public History Candidate Report — {args.mode}',
        '',
        f'- Generated: `{summary["generated_at"]}`',
        f'- Repo: `{repo_root}`',
        f'- Dry run: `{args.dry_run}`',
        '',
        '## Inputs',
        '',
        f'- Allowlist: `{manifest_path}`',
        f'- Denylist: `{denylist_path}`',
        '',
        '## Repository baseline',
        '',
        f'- Commits: `{commit_count}`',
        f'- Authors: `{author_count}`',
        f'- Paths scanned: `{len(files)}`',
        '',
        '## Boundary counts',
        '',
        f'- ALLOW: `{counts[ALLOW]}`',
        f'- BLOCK: `{counts[BLOCK]}`',
        f'- AMBIGUOUS: `{counts[AMBIGUOUS]}`',
        '',
        '## Candidate metrics (0-100, higher is better)',
        '',
        f'- privacy_risk: `{metrics["privacy_risk"]}`',
        f'- simplicity: `{metrics["simplicity"]}`',
        f'- merge_conflict_risk: `{metrics["merge_conflict_risk"]}`',
        f'- auditability: `{metrics["auditability"]}`',
        '',
        '## Rationale',
        '',
        f'- {rationale}',
        f'- unresolved_high: `{unresolved_high}`',
        '',
        '## Next step',
        '',
        '- Feed this summary JSON into `scripts/public_history_scorecard.py` for winner selection.',
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text('\n'.join(report_lines).rstrip() + '\n', encoding='utf-8')

    if args.summary_json:
        summary_path = args.summary_json if args.summary_json.is_absolute() else (Path.cwd() / args.summary_json).resolve()
        dump_json(summary_path, summary)
        print(f'Summary JSON written: {summary_path}')

    print(f'Report written: {report_path}')
    print(
        'Metrics: '
        f"privacy={metrics['privacy_risk']} simplicity={metrics['simplicity']} "
        f"merge_conflict={metrics['merge_conflict_risk']} auditability={metrics['auditability']}",
    )
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
