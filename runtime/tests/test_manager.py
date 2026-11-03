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

    def test_stdin_is_forwarded_and_invalid_inputs_never_boot_vm(self):
        for value in [None, 5, '한'*22000]:
            with patch.object(manager,'prepare') as prepare:
                with self.assertRaises(ValueError):manager.execute({'canvasId':123,'language':'python','code':'input()','stdin':value})
                prepare.assert_not_called()
        with patch.object(manager,'prepare',return_value='vsock'), patch.object(manager,'settings',return_value={'languages':{'python':{}}}), patch.object(manager,'exchange',return_value={'exitCode':0}) as exchange:
            manager.execute({'canvasId':123,'language':'python','code':'input()','stdin':'one\ntwo\n'})
            self.assertEqual(exchange.call_args.args[1]['stdin'],'one\ntwo\n')

    def test_build_uses_stored_configuration_and_compile_timeout(self):
        config={'executionMode':'precompiled'}
        with patch.object(manager,'prepare',return_value='vsock'), patch.object(manager,'settings',return_value={'languages':{'python':config}}), patch.object(manager,'exchange',return_value={'exitCode':0}) as exchange:
            manager.execute({'canvasId':123,'action':'build','language':'python','code':'print(1)'})
            exchange.assert_called_once_with('vsock',{'action':'build','language':'python','code':'print(1)','runtime':config},response_timeout=70)

    def test_streaming_output_is_forwarded_before_final_guest_response(self):
        import json, socket, threading
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / 'guest.sock')
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path);server.listen(1)
            received = threading.Event();errors=[]
            def guest():
                try:
                    conn,_ = server.accept()
                    with conn, conn.makefile('rb') as reader:
                        self.assertEqual(reader.readline(), b'CONNECT 5000\n')
                        conn.sendall(b'OK 5000\n');request=json.loads(reader.readline())
                        self.assertTrue(request['stream'])
                        conn.sendall(b'{"type":"output","output":"first"}\n')
                        self.assertTrue(received.wait(2))
                        conn.sendall(b'{"exitCode":0,"output":"firstlast"}\n')
                except BaseException as error:errors.append(error)
            thread=threading.Thread(target=guest);thread.start()
            chunks=[]
            try:
                result=manager.exchange(path, {'stream':True},on_output=lambda text:(chunks.append(text),received.set()))
                self.assertEqual(chunks,['first']);self.assertEqual(result['output'],'firstlast')
            finally:server.close();thread.join(3)
            if errors:raise errors[0]

    def test_stream_request_is_bounded_and_forwarded_without_changing_old_protocol(self):
        with patch.object(manager,'prepare',return_value='vsock'), patch.object(manager,'settings',return_value={'languages':{'python':{}}}), patch.object(manager,'exchange',return_value={'exitCode':0}) as exchange:
            callback=lambda text:None
            manager.execute({'canvasId':123,'language':'python','code':'print(1)','stream':True},on_output=callback)
            self.assertTrue(exchange.call_args.args[1]['stream']);self.assertIs(exchange.call_args.kwargs['on_output'],callback)
        for value in [1,'true',None]:
            with self.assertRaises(ValueError):manager.execute({'canvasId':123,'language':'python','code':'print(1)','stream':value})

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
