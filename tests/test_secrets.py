from __future__ import annotations
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from deploy_os import cli
from deploy_os.adapters import adapter_for, AdapterError
from deploy_os.adapters.files import FileAdapter
from deploy_os.adapters.ssh_transport import connection_for
from deploy_os.catalog import CatalogError, load_catalog
from deploy_os.model import Secret
from deploy_os.sops_store import SopsStore, SopsError


class SecretTest(unittest.TestCase):
    def test_file_permissions_and_workspace_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'replica'
            target.write_bytes(b'before')
            target.chmod(0o640)
            adapter = FileAdapter(root / 'state')
            adapter.write(target.as_uri(), b'after')
            self.assertEqual(b'after', target.read_bytes())
            self.assertEqual(0o640, target.stat().st_mode & 0o777)
            other = root / 'new'
            adapter.write(other.as_uri(), b'new')
            self.assertEqual(0o600, other.stat().st_mode & 0o777)
            with self.assertRaises(AdapterError):
                adapter.read('workspace-file://app/../../escape')
            with self.assertRaises(AdapterError):
                adapter.read('workspace-file://../escape')
            with self.assertRaises(AdapterError):
                adapter_for(root, 'unsupported://app/path')
            with self.assertRaises(AdapterError):
                connection_for(root, '../escape')

    def test_sops_failure_hides_process_output(self):
        sentinel = b'synthetic-sensitive-sentinel'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = Secret('sample.token', 'sample-app', 'active', 'operator', root / 'vault/sample.json', 'text', 90, (), ())
            failed = subprocess.CompletedProcess([], 1, sentinel, sentinel)
            with mock.patch('deploy_os.sops_store.subprocess.run', return_value=failed):
                with self.assertRaises(SopsError) as captured:
                    SopsStore(root).read(secret)
                self.assertNotIn(sentinel.decode(), str(captured.exception))
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual({}, SopsStore(root)._environment())

    def test_real_sops_roundtrip_dry_run_and_replica_contract(self):
        if not (shutil.which('age-keygen') and shutil.which('sops')):
            if os.environ.get('REQUIRE_SOPS_TEST') == '1':
                self.fail('Required SOPS/age tools are unavailable')
            self.skipTest('SOPS/age tools unavailable; set REQUIRE_SOPS_TEST=1 for release gate')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'AGENTS.md').write_text('# synthetic\n')
            identity = root / 'identity'
            generated = subprocess.run(['age-keygen', '-o', str(identity)], capture_output=True)
            self.assertEqual(0, generated.returncode, 'temporary identity generation failed')
            recipient = subprocess.run(['age-keygen', '-y', str(identity)], capture_output=True, text=True)
            self.assertEqual(0, recipient.returncode, 'temporary recipient generation failed')
            (root / '.sops.yaml').write_text('creation_rules:\n  - path_regex: vault/.*\\.json$\n    age: ' + recipient.stdout.strip() + '\n')
            replica = root / 'replica'
            sentinel = b'synthetic-roundtrip-value'
            replica.write_bytes(sentinel)
            (root / 'catalog.toml').write_text(f'''schema_version=1
[[projects]]
id="sample-app"
status="test"
[[secrets]]
id="sample.token"
project="sample-app"
owner="operator"
sops_path="vault/sample.json"
payload_format="text"
rotation_days=90
[[secrets.replicas]]
uri="{replica.as_uri()}"
purpose="synthetic"
writable=true
''')
            environment = {key: value for key, value in os.environ.items() if not key.startswith('SOPS_')}
            environment['SOPS_AGE_KEY_FILE'] = str(identity)
            output = StringIO()
            with mock.patch.dict(os.environ, environment, clear=True), redirect_stdout(output), redirect_stderr(output):
                catalog = load_catalog(root)
                self.assertEqual(0, cli._import(catalog, 'sample.token', replica.as_uri(), False, False))
                self.assertFalse((root / 'vault/sample.json').exists())
                self.assertEqual(0, cli._import(catalog, 'sample.token', replica.as_uri(), True, False))
                self.assertEqual(sentinel, SopsStore(root).read(catalog.secrets[0]))
                self.assertNotIn(sentinel, (root / 'vault/sample.json').read_bytes())
                self.assertEqual(0, cli._check(catalog, 'sample.token'))
                replica.write_bytes(b'changed')
                self.assertEqual(1, cli._check(catalog, 'sample.token'))
                self.assertEqual(0, cli._sync(catalog, 'sample.token', replica.as_uri(), False))
                self.assertEqual(b'changed', replica.read_bytes())
                self.assertEqual(0, cli._sync(catalog, 'sample.token', replica.as_uri(), True))
                self.assertEqual(sentinel, replica.read_bytes())
                with self.assertRaises(CatalogError):
                    cli._sync(catalog, 'sample.token', (root / 'unknown').as_uri(), True)
                with self.assertRaises(CatalogError):
                    cli._import(catalog, 'sample.token', replica.as_uri(), True, False)
            self.assertNotIn(sentinel.decode(), output.getvalue())

    def test_malformed_catalog_error_is_safe_and_boolean_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'AGENTS.md').write_text('# synthetic\n')
            sentinel = 'synthetic-sensitive-parser-input'
            (root / 'catalog.toml').write_text('invalid = ' + sentinel)
            output = StringIO()
            with redirect_stderr(output):
                self.assertEqual(1, cli.main(['--root', str(root), 'list']))
            self.assertNotIn(sentinel, output.getvalue())
            (root / 'catalog.toml').write_text('schema_version=1\n[[mirrors]]\nid="backup"\nadapter="git"\nrequired="false"\n')
            with self.assertRaises(CatalogError):
                load_catalog(root)

    def test_ssh_writer_uses_unique_temp_and_handles_apostrophe(self):
        from deploy_os.adapters.ssh_file import SSHFileAdapter
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "operator's replica"
            target.write_bytes(b'before')
            target.chmod(0o640)
            victim = root / 'unrelated'
            victim.write_bytes(b'keep')
            Path(str(target) + '.secretsctl-new').symlink_to(victim)
            result = subprocess.run(['sh', '-c', SSHFileAdapter._remote_writer(str(target), True)], input=b'after', capture_output=True)
            self.assertEqual(0, result.returncode, 'local synthetic writer failed')
            self.assertEqual(b'after', target.read_bytes())
            self.assertEqual(b'keep', victim.read_bytes())
            self.assertEqual(0o640, target.stat().st_mode & 0o777)
            self.assertEqual([], list(root.glob('.secretsctl.*')))
