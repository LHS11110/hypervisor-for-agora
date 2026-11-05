import base64,importlib.util,json,queue,socket,threading,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('terminal_stream',Path(__file__).resolve().parents[1]/'guest/terminal.py');terminal=importlib.util.module_from_spec(spec);spec.loader.exec_module(terminal)
class TerminalStreamTests(unittest.TestCase):
 def setUp(self):
  self.client,self.server=socket.socketpair();self.client.settimeout(2);self.reader=self.client.makefile('rb');self.outputs=queue.Queue();self.calls=[];self.errors=[];self.cursor=0
  self.service=terminal.TerminalService()
  def handle(request):
   self.calls.append(request)
   if request['operation']=='close':return {'closed':True}
   data=request['data'].encode() if request['operation']=='input' else self.outputs.get_nowait() if not self.outputs.empty() else b''
   self.cursor+=len(data);return {'data':base64.b64encode(data).decode(),'offset':self.cursor,'exited':False,'truncated':False}
  self.service.handle=handle
  def run():
   try:self.service.attach(self.server,{'session':'7:object','operation':'attach','data':'','cols':80,'rows':24,'offset':0})
   except Exception as error:self.errors.append(error)
   finally:self.server.close()
  self.thread=threading.Thread(target=run,daemon=True);self.thread.start();self.assertEqual(self.receive()['type'],'ready')
 def receive(self):return json.loads(self.reader.readline())
 def send(self,**values):self.client.sendall(json.dumps({'operation':'input','data':'','cols':80,'rows':24,**values}).encode()+b'\n')
 def tearDown(self):
  self.reader.close();self.client.close();self.thread.join(2);self.assertFalse(self.thread.is_alive())
 def test_output_push_and_ordered_unicode_input_on_one_socket(self):
  self.outputs.put('출력\n'.encode());frame=self.receive();self.assertEqual(base64.b64decode(frame['data']).decode(),'출력\n')
  for text in ('a','한글','\x03'):self.send(data=text);self.assertEqual(base64.b64decode(self.receive()['data']).decode(),text)
  self.send(operation='resize',cols=100,rows=35);self.send(operation='close');self.assertTrue(self.receive()['closed'])
  self.assertTrue(all(c['session']=='7:object' for c in self.calls));self.assertTrue(any(c['operation']=='resize' and c['cols']==100 for c in self.calls));self.assertEqual(self.errors,[])
 def test_stream_cannot_override_bound_session(self):
  self.send(session='8:foreign',data='bad');self.assertEqual(self.reader.readline(),b'');self.assertTrue(self.errors);self.assertIsInstance(self.errors[0],ValueError);self.assertTrue(all(c['session']=='7:object' for c in self.calls))
if __name__=='__main__':unittest.main()
