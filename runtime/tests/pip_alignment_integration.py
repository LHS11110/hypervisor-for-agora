"""Root-only regression: plain pip installs into the active Python worker's environment."""
import json,socket,subprocess
from pathlib import Path
cid=2147483013;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(75);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
def cell(language,code):
 r=request({'language':language,'code':code});assert r.get('exitCode')==0 and not r.get('environmentReset'),r;return r['output']
revision=0
def select(engine):
 global revision
 r=request({'action':'settings_set','language':'python','config':{'engine':engine},'revision':revision});assert 'error' not in r,r;revision=r['revision']
try:
 select('py315')
 assert cell('python','import importlib.util;print(importlib.util.find_spec("numpy") is None);retained=99')=='True\n'
 before=cell('bash','kept=preserved;export PATH=/workspace/custom-bin:$PATH;export PYTHONPATH=/workspace/custom-lib:$PYTHONPATH;printf "%s\\n" "$BASHPID";python3 --version;pip --version')
 pid=before.splitlines()[0];assert 'Python 3.15.' in before and '(python 3.15)' in before,before
 output=cell('bash','pip install --only-binary=:all: --no-input numpy');assert 'Successfully installed numpy-' in output,output
 assert cell('python','import numpy as np;print(retained);print(np.dot([1,2,3],[4,5,6]));print(np.__file__)')=='99\n32\n/workspace/.python/3.15/numpy/__init__.py\n'
 print('PASS original failure reproduced then fixed: running Python 3.15 worker sees NumPy installed by plain pip without resetting its state',flush=True)
 select('py314')
 after=cell('bash','printf "%s %s\\n" "$BASHPID" "$kept";python --version;pip3 --version;printf "%s\\n%s\\n" "$PATH" "$PYTHONPATH"')
 assert after.startswith(pid+' preserved\n') and 'Python 3.14.' in after and '(python 3.14)' in after,after
 assert '/workspace/custom-bin' in after and '/workspace/.python/3.14:/workspace/custom-lib' in after,after
 output=cell('bash','pip3 install --only-binary=:all: --no-input numpy');assert 'Successfully installed numpy-' in output,output
 assert cell('python','import numpy as np,subprocess;print(np.dot([1,2],[3,4]));print(np.__file__);print("(python 3.14)" in subprocess.check_output(["pip","--version"],text=True))')=='11\n/workspace/.python/3.14/numpy/__init__.py\nTrue\n'
 print('PASS selecting 3.14 updates an existing Shell without losing variables or custom paths; pip3 and nested pip use 3.14',flush=True)
 assert 'Python 3.12.' in cell('bash','python3.12 --version')
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 assert cell('python','import numpy as np;print(np.dot([1,2],[3,4]))')=='11\n'
 assert '(python 3.14)' in cell('bash','pip --version')
 print('PASS explicit version commands and selected-version pip/NumPy after VM restart',flush=True)
finally:
 assert request({'action':'delete'})=={'deleted':True}
 marker.unlink(missing_ok=True)
 print('Removed only the dedicated pip routing test VM and packages',flush=True)
