"""Root-only isolated VM check for unprivileged, streamed apt and persisted packages."""
import base64,json,socket,subprocess,time
from pathlib import Path
CID=2147483140;state=Path('/var/lib/agora-runtime');offset=0
assert not (state/'canvases'/str(CID)).exists() and not (state/'deleted'/str(CID)).exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
  conn.settimeout(70);conn.connect('/run/agora-runtime/manager.sock');conn.sendall((json.dumps({'canvasId':CID,**data})+'\n').encode());return json.loads(conn.makefile('rb').readline(500000))
def tty(operation,data=''):
 global offset
 result=request({'action':'terminal','owner':2147483647,'terminal':'apt-test','operation':operation,'data':data,'cols':120,'rows':40,'offset':offset})
 assert 'error' not in result,result;offset=result.get('offset',offset);return base64.b64decode(result.get('data','')).decode(errors='replace')
def command(code,marker,timeout=180):
 tty('input',code+'\n');output='';deadline=time.monotonic()+timeout
 while marker not in output:
  if time.monotonic()>deadline:raise AssertionError(output[-6000:])
  output+=tty('poll');time.sleep(.1)
 return output
try:
 tty('open');tty('input','stty -echo\n');time.sleep(.3);tty('poll')
 result=command("printf 'UID='; id -u; python3 -c \"print(next(x.strip() for x in open('/proc/self/status') if x.startswith('NoNewPrivs:')))\"; apt-get update; printf 'UPDATE_RESULT:%s\\n' \"$?\"; apt-get install -y hello; printf 'INSTALL_RESULT:%s\\n' \"$?\"; hello; printf 'APT_TEST_DONE\\n'",'APT_TEST_DONE')
 assert 'UID=1000' in result and 'NoNewPrivs:\t1' in result,result
 assert 'UPDATE_RESULT:0' in result and 'INSTALL_RESULT:0' in result and 'Hello, world!' in result,result
 print('PASS UID 1000 + no_new_privs=1: apt-get update, install hello and execute installed binary',flush=True)
 guard="import socket,json;s=socket.socket(socket.AF_UNIX);s.connect('/run/agora-packages.sock');s.sendall((json.dumps({'tool':'apt','args':['-oAPT::Update::Pre-Invoke::=/usr/bin/id','update']})+'\\n').encode());r=json.loads(s.makefile('rb').readline());print('GUARD_OK' if 'Unsupported apt option' in r.get('message','') else r)"
 result=command('python3 -c '+__import__('shlex').quote(guard),'GUARD_OK');assert 'GUARD_OK' in result
 print('PASS package RPC refuses arbitrary root hooks',flush=True)
 tty('close');subprocess.run(['systemctl','stop',f'agora-runtime@{CID}.service'],check=True);offset=0;tty('open');tty('input','stty -echo\n');time.sleep(.3);tty('poll')
 result=command("hello; printf 'PERSISTED\\n'",'PERSISTED');assert 'Hello, world!' in result,result
 print('PASS installed package survives VM restart',flush=True)
finally:
 try:tty('close')
 except Exception:pass
 result=request({'action':'delete'});assert result=={'deleted':True},result;(state/'deleted'/str(CID)).unlink(missing_ok=True)
 print('Removed dedicated apt test VM',flush=True)
