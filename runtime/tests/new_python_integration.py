"""Root-only KVM test for Python 3.14/3.15 and version-isolated pip NumPy installs."""
import json,socket,subprocess
from pathlib import Path
cid=2147483012;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(75);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
def cell(language,code):
 result=request({'language':language,'code':code});assert result.get('exitCode')==0 and not result.get('environmentReset'),result;return result['output']
revision=0
def configure(engine):
 global revision
 result=request({'action':'settings_set','language':'python','config':{'engine':engine,'optimize':'2'},'revision':revision});assert 'error' not in result,result;revision=result['revision']
try:
 for version,engine in [('3.14','py314'),('3.15','py315')]:
  configure(engine)
  output=cell('python','import sys,ssl,sqlite3\nfrom compression import zstd\nassert zstd.decompress(zstd.compress(b"agora"))==b"agora"\nprint(sys.version.split()[0]);print(sys.flags.optimize);retained=41')
  assert output.startswith(version+'.') and output.endswith('\n2\n'),output
  assert cell('python','print(retained+1)')=='42\n'
  print('PASS interpreter, stdlib extensions, optimization and shared state: '+output.replace('\n',' | '),flush=True)
  command=f'LD_LIBRARY_PATH=/opt/python/{version}/lib PYTHONPATH=/workspace/.python/{version} PIP_TARGET=/workspace/.python/{version} /opt/python/{version}/bin/python{version} -m pip install --only-binary=:all: --no-input numpy'
  output=cell('bash',command)
  assert 'Successfully installed numpy-' in output,output
  print('PASS '+version+' pip: '+next(line for line in output.splitlines() if 'Successfully installed numpy-' in line),flush=True)
  output=cell('python','import numpy as np\nprint(np.__version__);print(np.dot([1,2,3],[4,5,6]));print(np.__file__)')
  assert '\n32\n' in output and f'/workspace/.python/{version}/numpy/__init__.py\n' in output,output
  print('PASS '+version+' actual NumPy calculation and isolated package path: '+output.replace('\n',' | '),flush=True)
 configure('py312')
 assert cell('python','import importlib.util;print(importlib.util.find_spec("numpy") is None)')=='True\n'
 for version,engine in [('3.14','py314'),('3.15','py315')]:
  configure(engine);subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
  assert cell('python','import numpy as np,sys;print(sys.version_info.minor);print(np.dot([1,2],[3,4]))')==version.split('.')[1]+'\n11\n'
 print('PASS Python package isolation, changing versions and importing NumPy after VM restart',flush=True)
finally:
 assert request({'action':'delete'})=={'deleted':True}
 marker.unlink(missing_ok=True)
 print('Removed only the dedicated Python version test VM and packages',flush=True)
