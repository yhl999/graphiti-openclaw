from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'delta_tool.py'


def _valid_policy() -> dict:
    return {
        'version': 1,
        'origin': {'remote': 'origin', 'branch': 'main'},
        'upstream': {'remote': 'upstream', 'url': 'https://example.com/upstream.git', 'branch': 'main'},
        'sync_button_policy': {
            'require_clean_worktree': True,
            'max_origin_only_commits': 0,
            'require_upstream_only_commits': True,
        },
        'scorecard': {
            'clean_foundation_threshold': 80,
            'weights': {
                'privacy_risk': 0.35,
                'simplicity': 0.35,
                'merge_conflict_risk': 0.2,
                'auditability': 0.1,
            },
        },
        'schedule': {
            'timezone': 'America/New_York',
            'weekly_day': 'monday',
            'cron_utc': '0 14 * * 1',
        },
    }


def _valid_state_manifest() -> dict:
    return {
        'version': 1,
        'package_name': 'delta-state',
        'required_files': ['config/migration_sync_policy.json'],
        'optional_globs': ['scripts/*.py'],
        'exclude_globs': ['**/__pycache__/**'],
    }


class DeltaToolTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> None:
        subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)

    def test_dispatches_contract_check_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / 'config').mkdir(parents=True, exist_ok=True)
            (repo / 'extensions' / 'sample').mkdir(parents=True, exist_ok=True)
            (repo / 'scripts').mkdir(parents=True, exist_ok=True)

            (repo / 'scripts' / 'tool.py').write_text('print("ok")\n', encoding='utf-8')
            (repo / 'config' / 'migration_sync_policy.json').write_text(
                f'{json.dumps(_valid_policy(), indent=2)}\n',
                encoding='utf-8',
            )
            (repo / 'config' / 'state_migration_manifest.json').write_text(
                f'{json.dumps(_valid_state_manifest(), indent=2)}\n',
                encoding='utf-8',
            )
            (repo / 'extensions' / 'sample' / 'manifest.json').write_text(
                f'{json.dumps({"name": "sample", "version": "0.1.0", "capabilities": ["sync"], "entrypoints": {"doctor": "scripts/tool.py"}}, indent=2)}\n',
                encoding='utf-8',
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    'contracts-check',
                    '--',
                    '--repo',
                    str(repo),
                    '--strict',
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn('Delta contract check OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
