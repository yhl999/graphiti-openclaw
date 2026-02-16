from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MIGRATE_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'delta_contract_migrate.py'
EXTENSION_CHECK_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'extension_contract_check.py'


class DeltaContractMigrateTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> None:
        subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)

    def _seed(self, root: Path) -> None:
        (root / 'config').mkdir(parents=True, exist_ok=True)
        (root / 'extensions' / 'sample').mkdir(parents=True, exist_ok=True)
        (root / 'scripts').mkdir(parents=True, exist_ok=True)

        (root / 'scripts' / 'tool.py').write_text('print("ok")\n', encoding='utf-8')
        (root / 'config' / 'delta_contract_policy.json').write_text(
            (
                '{\n'
                '  "version": 1,\n'
                '  "targets": {\n'
                '    "extension_command_contract": {\n'
                '      "current_version": 1,\n'
                '      "migration_script": "scripts/delta_contract_migrate.py",\n'
                '      "notes": "Commands must use <namespace>/<command>."\n'
                '    }\n'
                '  }\n'
                '}\n'
            ),
            encoding='utf-8',
        )
        (root / 'extensions' / 'sample' / 'manifest.json').write_text(
            f'{json.dumps({"name": "sample-extension", "version": "0.1.0", "capabilities": ["sync"], "entrypoints": {"doctor": "scripts/tool.py"}, "commands": {"doctor-run": "scripts/tool.py"}}, indent=2)}\n',
            encoding='utf-8',
        )

    def test_migrates_extension_commands_to_namespaced_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            self._seed(repo)

            dry_run = subprocess.run(
                [
                    sys.executable,
                    str(MIGRATE_SCRIPT),
                    '--repo',
                    str(repo),
                    '--contract-policy',
                    'config/delta_contract_policy.json',
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(dry_run.returncode, 0, msg=dry_run.stderr)
            self.assertIn('Extension manifests needing migration: 1', dry_run.stdout)

            write_run = subprocess.run(
                [
                    sys.executable,
                    str(MIGRATE_SCRIPT),
                    '--repo',
                    str(repo),
                    '--contract-policy',
                    'config/delta_contract_policy.json',
                    '--write',
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(write_run.returncode, 0, msg=write_run.stderr)

            manifest = json.loads((repo / 'extensions' / 'sample' / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['command_contract']['version'], 1)
            self.assertEqual(manifest['command_contract']['namespace'], 'sample-extension')
            self.assertIn('sample-extension/doctor-run', manifest['commands'])

            extension_check = subprocess.run(
                [
                    sys.executable,
                    str(EXTENSION_CHECK_SCRIPT),
                    '--repo',
                    str(repo),
                    '--extensions-dir',
                    'extensions',
                    '--strict',
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(extension_check.returncode, 0, msg=extension_check.stderr)


if __name__ == '__main__':
    unittest.main()
