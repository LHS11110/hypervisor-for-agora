import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('manager', Path(__file__).resolve().parents[1] / 'manager.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ManagerTests(unittest.TestCase):
    def test_rejects_invalid_canvas_and_language_before_starting_a_vm(self):
        for request in ({'canvasId': -1, 'language': 'python', 'code': ''},
                        {'canvasId': 1, 'language': 'ruby', 'code': ''},
                        {'canvasId': True, 'language': 'python', 'code': ''},
                        {'canvasId': 1, 'language': 'python', 'code': '한' * 22000}):
            with self.assertRaises(ValueError):
                manager.execute(request)

    def test_reset_does_not_boot_an_inactive_canvas(self):
        with patch.object(manager, 'running', return_value=False), patch.object(manager, 'prepare') as prepare:
            self.assertEqual(manager.execute({'canvasId': 123, 'action': 'reset'}), {'reset': True})
            prepare.assert_not_called()

    def test_reset_is_serialized_and_forwarded_to_active_guest(self):
        with patch.object(manager, 'running', return_value=True), patch.object(manager, 'prepare', return_value='vsock'), \
             patch.object(manager, 'exchange', return_value={'reset': True}) as exchange:
            self.assertEqual(manager.execute({'canvasId': 123, 'action': 'reset'}), {'reset': True})
            exchange.assert_called_once_with('vsock', {'action': 'reset'})
        with self.assertRaises(ValueError):
            manager.execute({'canvasId': 123, 'action': 'unsupported'})

    def test_refuses_new_disk_if_storage_reserve_would_be_consumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'base.raw').write_bytes(b'x' * 16)
            canvases = root / 'canvases'
            canvases.mkdir()
            disk_usage = type('DiskUsage', (), {'free': manager.DISK_RESERVE + 15})()
            with patch.object(manager, 'STATE', root), patch.object(manager, 'CANVASES', canvases), \
                 patch.object(manager.shutil, 'disk_usage', return_value=disk_usage):
                with self.assertRaises(ValueError):
                    manager.prepare(123)
            self.assertFalse((canvases / '123').exists())


if __name__ == '__main__':
    unittest.main()
