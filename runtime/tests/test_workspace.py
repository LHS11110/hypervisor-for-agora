"""Real files and the bundled Automerge worker; no VM or production storage needed."""
import base64
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workspace', ROOT / 'guest/workspace.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.root = self.directory / 'workspace'
        self.root.mkdir()
        self.state = self.directory / 'state'
        self.workspace = module.Workspace(self.root, self.state)
        self.sequence = 0

    def tearDown(self):
        self.workspace.close()
        self.temporary.cleanup()

    def call(self, operation, path='/file.txt', **fields):
        self.sequence += 1
        return self.workspace.handle({'operation': operation, 'path': path, 'requestId': str(self.sequence), **fields})

    def edit(self, record, text):
        request = {'current': record['document'], 'text': text, 'fileId': record['fileId']}
        result = subprocess.run(['node', str(ROOT / 'guest/workspace-crdt.cjs')], input=json.dumps(request),
                                text=True, capture_output=True, check=True)
        return json.loads(result.stdout)['document']

    def test_concurrent_edits_merge_and_persist_across_restart(self):
        self.call('write', content=base64.b64encode('a\nb\n'.encode()).decode(), create=True)
        base = self.call('read')
        first, second = self.edit(base, 'AA\nb\n'), self.edit(base, 'a\nBB\n')
        self.call('write', fileId=base['fileId'], document=first)
        self.call('write', fileId=base['fileId'], document=second)
        self.workspace.close()
        self.workspace = module.Workspace(self.root, self.state)
        current = self.call('read')
        self.assertEqual(((self.root / 'file.txt').read_text(), base64.b64decode(current['content']).decode()), ('AA\nBB\n', 'AA\nBB\n'))

    def test_duplicate_create_and_rename_have_durable_receipts(self):
        request = {'operation': 'write', 'path': '/file.txt', 'create': True, 'overwrite': False,
                   'content': 'aGVsbG8=', 'requestId': 'user:creation'}
        first = self.workspace.handle(request)
        self.assertEqual(self.workspace.handle(request), {**first, 'duplicate': True})
        with self.assertRaisesRegex(module.WorkspaceError, 'REQUEST_MISMATCH'):
            self.workspace.handle({**request, 'content': ''})
        source = self.call('stat')
        rename = {'operation': 'rename', 'path': '/file.txt', 'target': '/other.txt',
                  'resourceId': source['resourceId'], 'requestId': 'user:rename'}
        moved = self.workspace.handle(rename)
        self.workspace.close()
        self.workspace = module.Workspace(self.root, self.state)
        self.assertEqual((self.workspace.handle(rename), self.call('read', '/other.txt')['fileId']),
                         ({**moved, 'duplicate': True}, first['fileId']))

    def test_external_text_edits_merge_but_recreated_file_rejects_old_replica(self):
        self.call('write', content='YQpiCg==', create=True)
        original = self.call('read')
        offline = self.edit(original, 'a\nBB\n')
        (self.root / 'file.txt').write_text('AA\nb\n')
        self.call('write', fileId=original['fileId'], document=offline)
        self.assertEqual((self.root / 'file.txt').read_text(), 'AA\nBB\n')
        (self.root / 'replacement').write_text('new file')
        os.replace(self.root / 'replacement', self.root / 'file.txt')
        with self.assertRaisesRegex(module.WorkspaceError, 'FILE_REPLACED'):
            self.call('write', fileId=original['fileId'], document=offline)
        self.assertEqual((self.root / 'file.txt').read_text(), 'new file')

    def test_binary_cas_and_path_isolation(self):
        self.call('write', content=base64.b64encode(b'\x00\xff').decode(), create=True)
        current = self.call('read')
        self.assertIsNone(current['document'])
        with self.assertRaisesRegex(module.WorkspaceError, 'FILE_CONFLICT'):
            self.call('write', fileId=current['fileId'], etag='stale', content='')
        (self.root / 'outside').symlink_to(self.directory)
        (self.root / 'link').symlink_to(self.root / 'file.txt')
        (self.root / 'pipe').touch()
        for path in ('/../secret', '/a/../../secret', '//file', '/a/', '/a\\b', '/\x00'):
            with self.assertRaises(module.WorkspaceError):
                self.call('read', path)
        for path in ('/link', '/outside/state/files.sqlite'):
            with self.assertRaises((OSError, module.WorkspaceError)):
                self.call('read', path)

    def test_folder_rename_delete_and_destination_conflicts(self):
        self.call('mkdir', '/src')
        self.call('write', '/src/main.lean', content='ZXhhbXBsZQ==', create=True)
        source = self.call('stat', '/src')
        self.call('mkdir', '/existing')
        with self.assertRaisesRegex(module.WorkspaceError, 'FILE_EXISTS'):
            self.call('rename', '/src', target='/existing', resourceId=source['resourceId'])
        self.call('rename', '/src', target='/renamed', resourceId=source['resourceId'])
        self.assertEqual(self.call('list', '/renamed')['entries'], [['main.lean', 'file']])
        source = self.call('stat', '/renamed')
        self.call('delete', '/renamed', recursive=True, resourceId=source['resourceId'])
        self.assertFalse((self.root / 'renamed').exists())

    def test_durable_intent_recovers_before_reply_and_preserves_external_changes(self):
        request = {'operation': 'write', 'path': '/file.txt', 'content': 'aGVsbG8=', 'create': True, 'requestId': 'user:crash'}
        with patch.object(self.workspace, 'finish', side_effect=RuntimeError('power loss')):
            with self.assertRaises(RuntimeError):
                self.workspace.handle(request)
        self.workspace.close()
        self.workspace = module.Workspace(self.root, self.state)
        self.assertTrue(self.workspace.handle(request)['duplicate'])
        self.assertEqual((self.root / 'file.txt').read_text(), 'hello')
        current = self.call('read')
        request = {'operation': 'write', 'path': '/file.txt', 'fileId': current['fileId'], 'etag': current['etag'],
                   'content': 'bmV4dA==', 'requestId': 'user:second-crash'}
        with patch.object(self.workspace, 'finish', side_effect=RuntimeError('power loss')):
            with self.assertRaises(RuntimeError):
                self.workspace.handle(request)
        (self.root / 'file.txt').write_text('changed while offline')
        self.assertEqual(self.workspace.handle(request)['error'], 'FILE_CONFLICT')
        self.assertEqual((self.root / 'file.txt').read_text(), 'changed while offline')

    def test_recovery_after_file_replace_before_database_commit(self):
        actual = self.workspace.db

        class PowerLoss:
            def __enter__(self):
                actual.__enter__()
                return self

            def __exit__(self, *args):
                return actual.__exit__(*args)

            def execute(self, sql, args=()):
                if sql.startswith('INSERT OR REPLACE INTO files'):
                    raise RuntimeError('power loss after file replace')
                return actual.execute(sql, args)

        request = {'operation': 'write', 'path': '/file.txt', 'content': 'aGVsbG8=', 'create': True, 'requestId': 'after-replace'}
        self.workspace.db = PowerLoss()
        with self.assertRaises(RuntimeError):
            self.workspace.handle(request)
        self.workspace.db = actual
        self.workspace.close()
        self.workspace = module.Workspace(self.root, self.state)
        receipt = self.workspace.handle(request)
        self.assertEqual((receipt['duplicate'], (self.root / 'file.txt').read_text(), self.call('read')['fileId']),
                         (True, 'hello', receipt['fileId']))


class ManagerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('workspace_manager', ROOT / 'manager.py')
        self.manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.manager)
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.manager.STATE = self.directory
        self.manager.CANVASES = self.directory / 'canvases'
        self.canvas = self.manager.CANVASES / '42'
        self.canvas.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_active_guest_file_channel_does_not_take_the_kernel_lock(self):
        import threading
        lock = threading.Lock()
        lock.acquire()
        self.manager.locks[42] = lock
        (self.canvas / 'agent-version').write_text('current')
        request = {'operation': 'read', 'path': '/proof.lean', 'requestId': 'server-scoped'}
        with patch.object(self.manager, 'running', return_value=True), patch.object(self.manager, 'agent_version', return_value='current'), \
             patch.object(self.manager, 'exchange', return_value={'ok': True}) as exchange:
            result = self.manager.execute({'canvasId': 42, 'action': 'workspace', 'request': request})
        exchange.assert_called_once_with(self.canvas / 'vsock', request, response_timeout=15, port=5003)
        self.assertEqual(result, {'ok': True})

    def test_active_old_guest_is_not_restarted_or_patched(self):
        with patch.object(self.manager, 'running', return_value=True), patch.object(self.manager, 'agent_version', return_value='new'), \
             patch.object(self.manager, 'command') as command, patch.object(self.manager, 'prepare') as prepare:
            result = self.manager.execute({'canvasId': 42, 'action': 'workspace', 'request': {'operation': 'stat', 'path': '/'}})
        self.assertEqual(result, {'error': 'WORKSPACE_AGENT_UPGRADE_REQUIRED'})
        command.assert_not_called()
        prepare.assert_not_called()

    def test_invalid_workspace_envelopes_do_not_boot_a_guest(self):
        with patch.object(self.manager, 'prepare') as prepare:
            for payload in ({'canvasId': 42, 'action': 'workspace', 'request': {}, 'owner': 99},
                            {'canvasId': -1, 'action': 'workspace', 'request': {}},
                            {'canvasId': 42, 'action': 'workspace', 'request': 'invalid'}):
                with self.assertRaises(ValueError):
                    self.manager.execute(payload)
        prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
