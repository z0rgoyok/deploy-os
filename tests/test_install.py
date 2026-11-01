from __future__ import annotations
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from deploy_os.install import install, rollback, InstallError


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'synthetic origin'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        files = ['README.md', 'public-files.txt', 'bin/vps-ops', 'bin/secretsctl', '.codex/skills/deploy-os/SKILL.md', '.codex/skills/deploy-os-creator/SKILL.md']
        for name in files:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('synthetic\n')
        (self.repo / 'public-files.txt').write_text('\n'.join(files) + '\n')
        self.a = self.commit('version a')
        (self.repo / 'README.md').write_text('version b\n')
        (self.repo / '.codex/skills/deploy-os-creator/SKILL.md').unlink()
        files.remove('.codex/skills/deploy-os-creator/SKILL.md')
        (self.repo / 'public-files.txt').write_text('\n'.join(files) + '\n')
        self.b = self.commit('version b')
        self.prefix = self.base / 'isolated prefix'
        self.origin = str(self.repo)

    def commit(self, message):
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', message], check=True)
        return subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()

    def test_repeat_update_rollback_and_dirty_refusal(self):
        first = install(self.prefix, self.origin, self.a, False)
        self.assertEqual(first, install(self.prefix, self.origin, self.a, False))
        updated = install(self.prefix, self.origin, self.b, True)
        self.assertEqual(self.a, updated['previous'])
        self.assertFalse((self.prefix / 'home/.codex/skills/deploy-os-creator').is_symlink())
        rolled = rollback(self.prefix, self.origin)
        self.assertEqual(self.a, rolled['current'])
        self.assertTrue((self.prefix / 'home/.codex/skills/deploy-os-creator/SKILL.md').is_file())
        install(self.prefix, self.origin, self.b, True)
        self.assertFalse((self.prefix / 'home/.codex/skills/deploy-os-creator').is_symlink())
        rollback(self.prefix, self.origin)
        current = self.prefix / 'releases' / self.a
        (current / 'README.md').write_text('operator edit')
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin, self.b, True)
        self.assertEqual(self.a, json.loads((self.prefix / 'install.json').read_text())['current'])

    def test_wrong_origin_receipt_traversal_and_failed_update_preserve_current(self):
        install(self.prefix, self.origin, self.a, False)
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin + '-other', self.b, True)
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin, '0' * 40, True)
        self.assertEqual(self.a, json.loads((self.prefix / 'install.json').read_text())['current'])
        receipt = json.loads((self.prefix / 'install.json').read_text())
        receipt['current'] = '../escape'
        (self.prefix / 'install.json').write_text(json.dumps(receipt))
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin, self.b, True)

    def test_collision_and_parent_symlink_leave_external_paths_untouched(self):
        target = self.prefix / 'home/.local/bin/secretsctl'
        target.parent.mkdir(parents=True)
        target.write_text('keep')
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin, self.a, False)
        self.assertFalse((self.prefix / 'home/.codex').exists())
        self.assertEqual('keep', target.read_text())
        other = self.base / 'other prefix'
        (other / 'home').mkdir(parents=True)
        external = self.base / 'external'
        external.mkdir()
        (other / 'home/.local').symlink_to(external, target_is_directory=True)
        with self.assertRaises(InstallError):
            install(other, self.origin, self.a, False)
        self.assertEqual([], list(external.iterdir()))

    def test_ignored_files_are_refused(self):
        install(self.prefix, self.origin, self.a, False)
        current = self.prefix / 'current'
        (current / '.git/info/exclude').write_text('ignored-runtime\n')
        (current / 'ignored-runtime').write_text('unsafe')
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin, self.b, True)

    def test_interrupted_activation_recovers_committed_receipt(self):
        from unittest import mock
        from deploy_os.install import recover
        install(self.prefix, self.origin, self.a, False)
        original = Path.replace
        def fail_receipt(path, target):
            if path.name == '.install-new.json':
                raise OSError('synthetic interruption')
            return original(path, target)
        with mock.patch.object(Path, 'replace', fail_receipt):
            with self.assertRaises(OSError):
                install(self.prefix, self.origin, self.b, True)
        self.assertEqual(self.a, json.loads((self.prefix / 'install.json').read_text())['current'])
        result = recover(self.prefix, self.origin)
        self.assertEqual(self.a, result['current'])
        self.assertEqual((self.prefix / 'releases' / self.a).resolve(), (self.prefix / 'current').resolve())
        self.assertFalse((self.prefix / 'activation.json').exists())
        self.assertTrue((self.prefix / 'home/.codex/skills/deploy-os-creator/SKILL.md').is_file())

    def test_lock_refuses_concurrent_operation_and_malformed_receipt(self):
        import fcntl
        install(self.prefix, self.origin, self.a, False)
        with (self.prefix / '.operation.lock').open('r+') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(InstallError):
                install(self.prefix, self.origin, self.b, True)
        (self.prefix / 'install.json').write_text('[]')
        with self.assertRaises(InstallError):
            install(self.prefix, self.origin, self.b, True)

    def test_fresh_install_exposes_one_skill(self):
        install(self.prefix, self.origin, self.b, False)
        skills = self.prefix / 'home/.codex/skills'
        self.assertEqual(['deploy-os'], sorted(item.name for item in skills.iterdir()))

    def test_foreign_creator_targets_are_preserved_on_update(self):
        for kind in ('file', 'directory', 'broken-symlink'):
            with self.subTest(kind=kind):
                prefix = self.base / ('foreign ' + kind)
                install(prefix, self.origin, self.a, False)
                target = prefix / 'home/.codex/skills/deploy-os-creator'
                target.unlink()
                if kind == 'file':
                    target.write_text('foreign')
                elif kind == 'directory':
                    target.mkdir()
                    (target / 'keep').write_text('foreign')
                else:
                    target.symlink_to(self.base / 'foreign missing target')
                before = (prefix / 'install.json').read_bytes()
                with self.assertRaises(InstallError):
                    install(prefix, self.origin, self.b, True)
                self.assertEqual(before, (prefix / 'install.json').read_bytes())
                if kind == 'file':
                    self.assertEqual('foreign', target.read_text())
                elif kind == 'directory':
                    self.assertEqual('foreign', (target / 'keep').read_text())
                else:
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(self.base / 'foreign missing target', target.readlink())

    def test_recover_links_before_and_after_receipt_in_both_directions(self):
        from unittest import mock
        from deploy_os.install import recover
        for direction in ('update', 'rollback'):
            for committed in (False, True):
                with self.subTest(direction=direction, committed=committed):
                    prefix = self.base / (direction + str(committed))
                    install(prefix, self.origin, self.a, False)
                    if direction == 'rollback':
                        install(prefix, self.origin, self.b, True)
                    old = self.a if direction == 'update' else self.b
                    new = self.b if direction == 'update' else self.a
                    if committed:
                        original = Path.unlink
                        def interrupt(path, *args, **kwargs):
                            if path.name == 'activation.json':
                                raise OSError('synthetic interruption after receipt')
                            return original(path, *args, **kwargs)
                        patcher = mock.patch.object(Path, 'unlink', interrupt)
                    else:
                        original = Path.replace
                        def interrupt(path, target):
                            if path.name == '.install-new.json':
                                raise OSError('synthetic interruption before receipt')
                            return original(path, target)
                        patcher = mock.patch.object(Path, 'replace', interrupt)
                    with patcher:
                        with self.assertRaises(OSError):
                            if direction == 'update':
                                install(prefix, self.origin, self.b, True)
                            else:
                                rollback(prefix, self.origin)
                    receipt = recover(prefix, self.origin)
                    expected = new if committed else old
                    self.assertEqual(expected, receipt['current'])
                    legacy = prefix / 'home/.codex/skills/deploy-os-creator'
                    self.assertEqual(expected == self.a, legacy.is_symlink())
