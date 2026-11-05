"""Root-only isolated real VM check; never touches existing canvas VMs."""
import json,socket
from pathlib import Path
CID=2147483068
state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(CID);marker=state/'deleted'/str(CID)
assert not directory.exists() and not marker.exists(),'Dedicated notebook test ID already exists'
def request(payload):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
  channel.settimeout(230);channel.connect('/run/agora-runtime/manager.sock');channel.sendall(json.dumps({'canvasId':CID,**payload}).encode()+b'\n');return json.loads(channel.makefile('rb').readline(400001))
def notebook(code='',operation='execute',key='test'):
 return request({'action':'notebook','operation':operation,'notebook':key,'code':code})
try:
 r=notebook(operation='prepare');assert r.get('prepared'),r
 print('PASS actual VM: Jupyter environment ready',flush=True)
 r=notebook('value = 40\ndef add(a,b): return a+b');assert r.get('exitCode')==0,r
 r=notebook('print(add(value,2))\nimport pandas as pd\ndisplay(pd.DataFrame({"a":[1,2]}))\nimport matplotlib.pyplot as plt\nplt.plot([1,2,3],[1,4,9]);plt.show()');assert r.get('exitCode')==0,r
 assert any('42' in o.get('text','') for o in r['outputs']),r
 assert any('text/html' in o.get('data',{}) for o in r['outputs']),r
 assert any('image/png' in o.get('data',{}) for o in r['outputs']),r
 print('PASS actual VM: persistent functions, stdout, DataFrame HTML and matplotlib PNG',flush=True)
 r=notebook('print(value)',key='other');assert r.get('exitCode')==1,r
 r=notebook('input()');assert r.get('exitCode')==1 and any(o.get('ename')=='StdinNotImplementedError' for o in r['outputs']),r
 r=notebook(operation='reset');assert r.get('reset'),r
 r=notebook('print(value)');assert r.get('exitCode')==1,r
 print('PASS notebook kernel isolation, reset, and disabled interactive stdin',flush=True)
finally:
 assert request({'action':'delete'})=={'deleted':True}
 marker.unlink(missing_ok=True)
 print('Removed isolated notebook test VM',flush=True)
