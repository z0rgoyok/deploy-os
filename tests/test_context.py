from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, env: dict | None = None, cwd: Path = ROOT):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / 'private state'
        self.state.mkdir()
        (self.state / 'AGENTS.md').write_text('# isolated state\n')
        profile = self.state / 'projects/sample-app/operations.sh'
        profile.parent.mkdir(parents=True)
        profile.write_text('#!/bin/sh\nset -eu\nprintf "%s|%s|%s\\n" "$VPS_OPS_ROOT" "$VPS_OPS_PROJECT" "$VPS_OPS_SOURCE_DIR"\n')
        profile.chmod(0o755)
        self.app = self.base / 'application with spaces'
        self.app.mkdir()
        run('git', 'init', '-q', cwd=self.app)
        (self.app / '.ops-project').write_text('project=sample-app\ncontract_version=1\n')
        self.env = {key: value for key, value in os.environ.items() if key not in {'DEPLOY_OS_ROOT', 'VPS_OPS_PROJECT', 'VPS_OPS_SOURCE_DIR'}}
        self.env['DEPLOY_OS_ROOT'] = str(self.state)

    def vps(self, source=None, *options, env=None):
        return run(str(ROOT / 'bin/vps-ops'), '--source', str(source or self.app), *options, 'plan', env=env or self.env)

    def test_separate_state_and_nested_source(self):
        nested = self.app / 'nested'
        nested.mkdir()
        result = self.vps(nested)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f'{self.state}|sample-app|{self.app.resolve()}', result.stdout.strip())

    def test_missing_state_and_toolkit_as_state_fail_closed(self):
        env = {k: v for k, v in self.env.items() if k != 'DEPLOY_OS_ROOT'}
        self.assertNotEqual(0, self.vps(env=env).returncode)
        env['DEPLOY_OS_ROOT'] = str(ROOT)
        self.assertNotEqual(0, self.vps(env=env).returncode)
        result = run(str(ROOT / 'bin/secretsctl'), 'list', env={k: v for k, v in env.items() if k != 'DEPLOY_OS_ROOT'})
        self.assertNotEqual(0, result.returncode)
        self.assertIn('DEPLOY_OS_ROOT', result.stderr)

    def test_invalid_marker_and_explicit_mismatch(self):
        for marker in ('project=../escape\ncontract_version=1\n', 'project=sample-app\ncontract_version=2\n', 'project=sample-app\nproject=another\ncontract_version=1\n'):
            (self.app / '.ops-project').write_text(marker)
            self.assertNotEqual(0, self.vps().returncode)
        (self.app / '.ops-project').write_text('project=sample-app\ncontract_version=1\n')
        self.assertNotEqual(0, self.vps(None, '--project', 'other-app').returncode)

    def test_nested_git_boundary_hides_parent_marker(self):
        nested = self.app / 'another repo'
        nested.mkdir()
        run('git', 'init', '-q', cwd=nested)
        result = self.vps(nested)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual('', result.stdout)

    def test_explicit_secrets_root_beats_environment(self):
        (self.state / 'catalog.toml').write_text('schema_version=1\n')
        result = run(str(ROOT / 'bin/secretsctl'), '--root', str(self.state), 'list', env={**self.env, 'DEPLOY_OS_ROOT': str(ROOT)})
        self.assertEqual(0, result.returncode, result.stderr)

    def test_bootstrap_repeat_and_late_collision_are_atomic(self):
        home = self.base / 'isolated install home'
        env = {**self.env, 'DEPLOY_OS_INSTALL_HOME': str(home)}
        first = run(str(ROOT / 'bin/bootstrap-deploy-os'), env=env)
        second = run(str(ROOT / 'bin/bootstrap-deploy-os'), env=env)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual((ROOT / 'bin/secretsctl').resolve(), (home / '.local/bin/secretsctl').resolve())
        other = self.base / 'collision'
        (other / '.local/bin').mkdir(parents=True)
        (other / '.local/bin/secretsctl').write_text('keep')
        result = run(str(ROOT / 'bin/bootstrap-deploy-os'), env={**env, 'DEPLOY_OS_INSTALL_HOME': str(other)})
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((other / '.codex').exists())
        self.assertEqual('keep', (other / '.local/bin/secretsctl').read_text())

    def test_bootstrap_parent_symlink_is_rejected(self):
        home = self.base / 'symlink home'
        home.mkdir()
        external = self.base / 'external'
        external.mkdir()
        (home / '.local').symlink_to(external, target_is_directory=True)
        result = run(str(ROOT / 'bin/bootstrap-deploy-os'), env={**self.env, 'DEPLOY_OS_INSTALL_HOME': str(home)})
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], list(external.iterdir()))
