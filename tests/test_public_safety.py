from __future__ import annotations
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from deploy_os.public_safety import check_snapshot, main


def commit(root: Path):
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'synthetic'], check=True, capture_output=True)


class PublicSafetyTest(unittest.TestCase):
    def test_deny_state_even_when_allowlisted_and_secret_diagnostic_is_redacted(self):
        token = 'gh' + 'p_' + 'a' * 30
        snapshot = {'public-files.txt': (b'public-files.txt\nvault/test.json\n', '100644'), 'vault/test.json': (token.encode(), '100644')}
        failures = check_snapshot(snapshot)
        self.assertTrue(any('private-state-path' in item for item in failures))
        self.assertTrue(any('github-token' in item for item in failures))
        self.assertNotIn(token, '\n'.join(failures))
        snapshot['extra.txt'] = (b'hello', '100644')
        self.assertTrue(any('unexpected-file' in item for item in check_snapshot(snapshot)))
        snapshot['extra.txt'] = (b'', '120000')
        self.assertTrue(any('non-regular-file' in item for item in check_snapshot(snapshot)))

    def test_staged_secret_and_deleted_history_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            (root / 'public-files.txt').write_text('public-files.txt\nREADME.md\n')
            (root / 'README.md').write_text('safe\n')
            commit(root)
            sentinel = 'gh' + 'p_' + 'b' * 30
            (root / 'README.md').write_text(sentinel)
            subprocess.run(['git', '-C', str(root), 'add', 'README.md'], check=True)
            (root / 'README.md').write_text('safe\n')
            output = StringIO()
            with redirect_stderr(output), redirect_stdout(output):
                self.assertEqual(1, main(['--root', str(root)]))
            self.assertNotIn(sentinel, output.getvalue())
            (root / 'README.md').write_text(sentinel)
            commit(root)
            (root / 'README.md').write_text('safe\n')
            commit(root)
            with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                self.assertEqual(1, main(['--root', str(root), '--history']))

    def test_history_accepts_retired_paths_with_each_commit_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            legacy = root / '.codex/skills/deploy-os-creator/SKILL.md'
            legacy.parent.mkdir(parents=True)
            legacy.write_text('synthetic legacy skill')
            (root / 'public-files.txt').write_text('public-files.txt\n.codex/skills/deploy-os-creator/SKILL.md\n')
            commit(root)
            legacy.unlink()
            reference = root / '.codex/skills/deploy-os/references/create-state.md'
            reference.parent.mkdir(parents=True)
            reference.write_text('synthetic first-run scenario')
            (root / 'public-files.txt').write_text('public-files.txt\n.codex/skills/deploy-os/references/create-state.md\n')
            commit(root)
            with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                self.assertEqual(0, main(['--root', str(root), '--history']))
