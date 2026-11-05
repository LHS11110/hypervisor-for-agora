"""Bounded unprivileged Jupyter kernel worker, independent of code-object workers."""
import functools,json,os,selectors,socket,subprocess,time
from pathlib import Path
REQUIREMENTS=['jupyter-client==8.6.3','ipykernel==6.30.1','matplotlib==3.10.7','pandas==2.3.3']
class NotebookService:
 def __init__(self,runner):self.runner=runner;self.entry=None;self.engine=None
 def stop(self):
  if not self.entry:return
  process,control,group=self.entry;self.entry=None
  try:(group/'cgroup.kill').write_text('1');process.wait(timeout=5)
  finally:
   control.close()
   try:group.rmdir()
   except OSError:pass
 def handle(self,request):
  import re
  if set(request)!={'action','operation','notebook','code','runtime'} or request['operation'] not in ('prepare','execute','reset') or not isinstance(request['notebook'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',request['notebook']) or not isinstance(request['code'],str) or len(request['code'].encode())>65536:raise ValueError('Invalid notebook request')
  runner=self.runner;config=runner.options.normalize('python',request['runtime']);engine=config['engine'];binary=runner.options.PYTHON[engine]
  env=runner.options.python_environment(engine,{'HOME':'/workspace','PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','MPLBACKEND':'module://matplotlib_inline.backend_inline'})
  if engine=='py312' and Path('/opt/agora/notebook-py312').is_dir():env['PYTHONPATH']+=':/opt/agora/notebook-py312'
  if request['operation']=='prepare':
   self.stop();runner.ensure_job_cgroup();group=runner.CGROUP/'notebook-install';group.mkdir(exist_ok=True)
   try:
    check=subprocess.run([binary,'-c','import jupyter_client,ipykernel,matplotlib,pandas'],cwd=runner.WORKSPACE,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,preexec_fn=functools.partial(runner._restrict,group))
    if check.returncode==0:return {'prepared':True,'exitCode':0,'output':'주피터 환경이 준비되어 있습니다.','engine':engine}
    result=subprocess.run([binary,'-m','pip','install','--disable-pip-version-check','--no-input',*REQUIREMENTS],cwd=runner.WORKSPACE,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180,preexec_fn=functools.partial(runner._restrict,group))
    os.sync();return {'prepared':result.returncode==0,'exitCode':result.returncode,'output':result.stdout[-16384:].decode(errors='replace'),'engine':engine}
   except subprocess.TimeoutExpired:return {'prepared':False,'exitCode':1,'error':'노트북 환경 준비 시간이 초과되었습니다. 다시 시도하세요.'}
   finally:
    try:(group/'cgroup.kill').write_text('1');group.rmdir()
    except OSError:pass
  if self.entry and self.engine!=engine:self.stop()
  if not self.entry:
   runner.ensure_job_cgroup();group=runner.CGROUP/'notebook';group.mkdir(exist_ok=True)
   parent,child=socket.socketpair()
   try:process=subprocess.Popen([binary,'-u',str(Path(__file__).with_name('notebook_worker.py')),str(child.fileno())],cwd=runner.WORKSPACE,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(child.fileno(),),preexec_fn=functools.partial(runner._restrict,group))
   except BaseException:parent.close();raise
   finally:child.close()
   self.entry=(process,parent,group);self.engine=engine
  process,control,_=self.entry;control.settimeout(45)
  try:
   control.sendall(json.dumps(request).encode()+b'\n');data=bytearray()
   while not data.endswith(b'\n'):
    part=control.recv(65536)
    if not part or len(data)>350000:raise OSError('Notebook worker failed')
    data.extend(part)
   result=json.loads(data);result['engine']=engine;os.sync();return result
  except (OSError,ValueError):
   self.stop();return {'exitCode':1,'outputs':[],'error':'주피터 환경이 준비되지 않았거나 커널이 종료되었습니다. 환경 준비 후 다시 실행하세요.'}
