"""Root-only isolated VM check: scikit-learn regression and joblib multiprocessing.

Install through the PTY so large scientific wheels are not cut off by the
15-second code-cell timeout. The temporary VM is removed in finally.
"""
import json,socket,subprocess,time,base64,ast,math
from pathlib import Path
cid=2147483021;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(70);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
def cell(language,code):
 r=request({'language':language,'code':code});assert r.get('exitCode')==0 and not r.get('environmentReset'),r;return r['output']
try:
 offset=0
 def tty(operation,data=''):
  global offset
  result=request({'action':'terminal','owner':1,'terminal':'sklearn-test','operation':operation,'data':data,'cols':120,'rows':40,'offset':offset})
  offset=result.get('offset',offset)
  return base64.b64decode(result.get('data','')).decode(errors='replace')
 tty('open');tty('input',"python3 -m pip install --only-binary=:all: --no-input scikit-learn; printf 'PIP_DONE:%s\\n' \"$?\"\n")
 output='';deadline=time.monotonic()+150
 while 'PIP_DONE:0' not in output:
  if time.monotonic()>deadline:raise AssertionError(output[-4000:])
  output+=tty('poll');time.sleep(.2)
 tty('close')
 print('PASS installed sklearn in isolated VM',flush=True)
 output=cell('python',"import warnings\nwith warnings.catch_warnings(record=True) as captured:\n warnings.simplefilter('always')\n from sklearn.linear_model import LinearRegression\n from joblib import Parallel,delayed\n model=LinearRegression().fit([[0],[1],[2]], [1,3,5])\n print(model.predict([[3]]).tolist())\n print(Parallel(n_jobs=2)(delayed(abs)(i) for i in [-1,-2]))\n print([str(w.message) for w in captured])")
 assert math.isclose(ast.literal_eval(output.splitlines()[0])[0],7.0,abs_tol=1e-9) and '[1, 2]' in output and output.strip().endswith('[]') and 'serial mode' not in output,output
 print('PASS linear regression and joblib multiprocessing: '+output.replace('\n',' | '),flush=True)
finally:
 assert request({'action':'delete'})=={'deleted':True}
 marker.unlink(missing_ok=True)
 print('Removed the dedicated scikit-learn test VM and packages',flush=True)
