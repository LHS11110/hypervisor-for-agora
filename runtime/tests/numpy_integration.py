"""Root-only real NAT/KVM check: pip install NumPy, execute it, restart and import again."""
import json,socket,subprocess
from pathlib import Path
cid=2147483010;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(70);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
def cell(language,code):
 r=request({'language':language,'code':code});assert r.get('exitCode')==0 and not r.get('environmentReset'),r;return r['output']
try:
 output=cell('bash','python3 -m pip install --only-binary=:all: --no-input numpy')
 assert 'Successfully installed numpy-' in output,output
 print('PASS real pip install: '+next(line for line in output.splitlines() if 'Successfully installed numpy-' in line),flush=True)
 output=cell('python','import numpy as np,sys\nprint(sys.version.split()[0],np.__version__);print(np.arange(6).reshape(2,3).tolist());print(np.linalg.solve([[3,1],[1,2]],[9,8]).tolist())')
 assert '[[0, 1, 2], [3, 4, 5]]\n' in output and '[2.0, 3.0]\n' in output,output
 print('PASS actual Python/NumPy import and matrix calculations: '+output.replace('\n',' | '),flush=True)
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 assert cell('python','import numpy as np;print(np.dot([1,2,3],[4,5,6]));print(np.__file__)')=='32\n/workspace/.python/numpy/__init__.py\n'
 print('PASS installed NumPy remains callable after VM restart',flush=True)
finally:
 assert request({'action':'delete'})=={'deleted':True}
 marker.unlink(missing_ok=True)
 print('Removed only the dedicated NumPy test VM and packages',flush=True)
