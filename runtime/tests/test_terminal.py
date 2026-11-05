import base64,importlib.util,unittest,time
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('agora_terminal',Path(__file__).resolve().parents[1]/'guest/terminal.py');terminal=importlib.util.module_from_spec(spec);spec.loader.exec_module(terminal)
class FakePty:
 def __init__(self,cols,rows,python_engine=None):self.last=time.monotonic();self.closed=False;self.process=type('Process',(),{'poll':lambda _:None})();self.size=(cols,rows)
 def result(self,offset):return {'offset':offset,'data':''}
 def resize(self,cols,rows):self.size=(cols,rows)
 def close(self):self.closed=True
class TerminalTests(unittest.TestCase):
 def request(self,**values):return {'session':'7:object-id','operation':'open','data':'','cols':80,'rows':24,'offset':0,**values}
 def test_validation_prevents_shell_creation(self):
  service=terminal.TerminalService()
  with patch.object(terminal,'PtySession') as create:
   for values in ({'session':'../bad'},{'operation':'execute'},{'cols':0},{'rows':151},{'offset':-1},{'data':'가'*3000}):
    with self.assertRaises(ValueError):service.handle(self.request(**values))
   create.assert_not_called()
 def test_session_identity_limit_and_idle_cleanup(self):
  service=terminal.TerminalService()
  with patch.object(terminal,'PtySession',FakePty):
   service.handle(self.request());first=service.sessions['7:object-id'];service.handle(self.request());self.assertIs(first,service.sessions['7:object-id'])
   self.assertTrue(service.handle(self.request(session='8:object-id',operation='poll'))['expired'])
   service.handle(self.request(session='8:object-id'));self.assertIsNot(first,service.sessions['8:object-id'])
   service.handle(self.request(operation='close'));self.assertTrue(first.closed)
   service.sessions['8:object-id'].last-=terminal.IDLE+1;service.reap();self.assertEqual(service.sessions,{})
 def test_output_offsets_are_bounded_and_independent(self):
  s=terminal.PtySession.__new__(terminal.PtySession);s.lock=__import__('threading').Lock();s.buffer=bytearray(b'abcdef');s.offset=106;s.process=type('Process',(),{'poll':lambda _:None})()
  result=s.result(0);self.assertTrue(result['truncated']);self.assertEqual(base64.b64decode(result['data']),b'abcdef');self.assertEqual(result['offset'],106)
  self.assertEqual(base64.b64decode(s.result(103)['data']),b'def');self.assertEqual(s.result(106)['data'],'')
if __name__=='__main__':unittest.main()
