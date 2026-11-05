"""Run in python:3.12-slim-trixie with /packages, /work and writable /workspace mounts.
Uses real Jupyter kernels without requiring KVM; notebook_integration.py covers KVM.
"""
import json,socket,subprocess,os
parent,child=socket.socketpair();process=subprocess.Popen(['/usr/local/bin/python','-u','/work/notebook_worker.py',str(child.fileno())],pass_fds=(child.fileno(),),env={**os.environ,'PYTHONPATH':'/packages','HOME':'/workspace','MPLBACKEND':'module://matplotlib_inline.backend_inline'})
child.close();parent.settimeout(50);reader=parent.makefile('rb')
def run(code,operation='execute',key='smoke'):
 parent.sendall(json.dumps({'notebook':key,'operation':operation,'code':code}).encode()+b'\n');return json.loads(reader.readline(400000))
try:
 a=run('x = 40\ndef add(a,b): return a+b');assert a['exitCode']==0,a
 b=run('print(add(x,2))\nimport pandas as pd\ndisplay(pd.DataFrame({"a":[1,2]}))\nimport matplotlib.pyplot as plt\nplt.plot([1,2,3],[1,4,9]);plt.show()');assert b['exitCode']==0,b
 assert any('42' in o.get('text','') for o in b['outputs']),b
 assert any('text/html' in o.get('data',{}) for o in b['outputs']),b
 assert any('image/png' in o.get('data',{}) for o in b['outputs']),[(o['output_type'],list(o.get('data',{}))) for o in b['outputs']]
 print('PASS actual Jupyter kernel: persistent variables/functions, stdout, DataFrame HTML and matplotlib PNG',flush=True)
 c=run('input()');assert c['exitCode']==1 and any(o.get('ename')=='StdinNotImplementedError' for o in c['outputs']),c
 run('',operation='reset');d=run('print(x)');assert d['exitCode']==1,d
 print('PASS kernel reset and disabled interactive stdin',flush=True)
 for index in range(5):
  entry=run(f'v={index}',key=f'lru-{index}');assert entry['exitCode']==0,entry
 entry=run('print(v)',key='lru-0');assert entry['exitCode']==1,entry
 print('PASS bounded kernel LRU eviction',flush=True)
finally:parent.close();process.terminate();process.wait(5)
