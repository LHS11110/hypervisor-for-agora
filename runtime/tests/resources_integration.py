"""Root-only real KVM template regression; creates and removes only reserved canvas 2147483006."""
import json,shutil,socket,subprocess
from pathlib import Path

cid=2147483006
state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid)
settings=state/'settings'/f'{cid}.json';launch=state/'launch'/f'{cid}.env'
assert not directory.exists() and not settings.exists() and not launch.exists(),'Test ID must be unused'

def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
  conn.settimeout(65);conn.connect('/run/agora-runtime/manager.sock')
  conn.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n')
  return json.loads(conn.makefile('rb').readline(500000))

def cell(code):
 result=request({'language':'python','code':code})
 assert result.get('exitCode')==0 and not result.get('environmentReset'),result
 return result['output']

revision=0
try:
 initial=request({'action':'settings_get'});assert initial['resourceTemplate']=='small' and not directory.exists()
 for key,cpu,memory,worker,pids in [('small',1,1024,768,64),('medium',2,2048,1536,128),('large',4,4096,3072,256)]:
  result=request({'action':'resources_set','template':key,'revision':revision});assert 'error' not in result,result
  revision=result['revision']
  assert subprocess.run(['systemctl','is-active','--quiet',f'agora-runtime@{cid}.service']).returncode!=0
  assert request({'action':'settings_get'})['resourceTemplate']==key
  output=cell('import os,json\nfrom pathlib import Path\nprint(json.dumps({"cpus":os.cpu_count(),"memory":int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:")))//1024,"worker":Path("/sys/fs/cgroup/agora-job/memory.max").read_text().strip(),"quota":Path("/sys/fs/cgroup/agora-job/cpu.max").read_text().strip(),"pids":Path("/sys/fs/cgroup/agora-job/pids.max").read_text().strip(),"oldVariable": "saved_value" in globals(),"file":Path("resource-proof.txt").read_text() if Path("resource-proof.txt").exists() else None}))\nsaved_value=9\nPath("resource-proof.txt").write_text("retained")')
  actual=json.loads(output);assert actual['cpus']==cpu,actual
  assert .7*memory<actual['memory']<=memory,actual
  assert int(actual['worker'])==worker*1024**2 and actual['quota']==f'{cpu*100000} 100000' and actual['pids']==str(pids),actual
  assert not actual['oldVariable'] and actual['file']==(None if key=='small' else 'retained'),actual
  cap=subprocess.check_output(['systemctl','show',f'agora-runtime@{cid}.service','-p','MemoryMax','--value'],text=True).strip()
  assert int(cap)==(memory+512)*1024**2,cap
  java=request({'language':'java','code':'System.out.println(Runtime.getRuntime().availableProcessors());'})
  assert java.get('exitCode')==0 and java['output']==str(cpu)+'\n',java
  print(f'PASS {key}: {cpu} vCPU / {memory} MiB, guest and host limits, preserved files, cleared memory',flush=True)
 # Same-template application preserves existing memory and does not restart a VM.
 assert request({'action':'resources_set','template':'large','revision':revision})['revision']==revision
 assert cell('print(saved_value)')=='9\n'
 for data in [{'action':'resources_set','template':'custom','revision':revision},{'action':'resources_set','template':'small','revision':0}]:
  assert request(data).get('error')=='RUNTIME_CONFIG_INVALID'
 assert cell('print(saved_value)')=='9\n'
 print('PASS unchanged-template no-op and invalid/stale template requests preserve the running environment',flush=True)
finally:
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 if directory.exists():shutil.rmtree(directory)
 settings.unlink(missing_ok=True);launch.unlink(missing_ok=True)
 # Remove only this test unit's ephemeral resource override via PID 1.
 subprocess.run(['systemctl','revert',f'agora-runtime@{cid}.service'],check=True,stdout=subprocess.DEVNULL)
 print('Removed only the temporary test VM, settings, launch file and resource override',flush=True)
