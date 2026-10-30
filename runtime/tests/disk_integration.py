"""Root-only disk expansion regression. Uses and removes only canvas 2147483007."""
import hashlib,json,shutil,socket,subprocess
from pathlib import Path

cid=2147483007;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid)
settings=state/'settings'/f'{cid}.json';launch=state/'launch'/f'{cid}.env'
assert not directory.exists() and not settings.exists() and not launch.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
  connection.settimeout(90);connection.connect('/run/agora-runtime/manager.sock')
  connection.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n')
  return json.loads(connection.makefile('rb').readline(500000))
def cell(code):
 r=request({'language':'python','code':code});assert r.get('exitCode')==0 and not r.get('environmentReset'),r
 return r['output']
try:
 assert request({'action':'settings_get'})['diskGiB']==6
 cell('from pathlib import Path\nimport os\nPath("disk-proof.bin").write_bytes(bytes(range(256))*16)\nvalue=7')
 for size,revision in [(12,0),(24,1)]:
  response=request({'action':'disk_set','sizeGiB':size,'revision':revision});assert response.get('diskGiB')==size,response
  assert response['revision']==revision+1
  assert subprocess.run(['systemctl','is-active','--quiet',f'agora-runtime@{cid}.service']).returncode!=0
  disk=directory/'guest.raw';assert disk.stat().st_size==size*1024**3
  assert disk.stat().st_blocks*512>=size*1024**3,'Must reserve actual host space'
  actual=json.loads(cell('import json,os,hashlib\nfrom pathlib import Path\ns=os.statvfs("/workspace");print(json.dumps({"bytes":s.f_blocks*s.f_frsize,"hash":hashlib.sha256(Path("disk-proof.bin").read_bytes()).hexdigest(),"old":"value" in globals()}))\nvalue=9'))
  assert (size-.5)*1024**3<actual['bytes']<=size*1024**3,actual
  assert actual['hash']==hashlib.sha256(bytes(range(256))*16).hexdigest() and not actual['old'],actual
  print(f'PASS {size} GiB: actual ext4 and block reservation, preserved file checksum, cleared memory, stopped then rebooted VM',flush=True)
 for size,revision in [(6,2),(24,0),(48,2)]:
  assert request({'action':'disk_set','sizeGiB':size,'revision':revision}).get('error')=='RUNTIME_CONFIG_INVALID'
 assert cell('print(value)')=='9\n'
 assert request({'action':'disk_set','sizeGiB':24,'revision':2})['revision']==2
 assert cell('print(value)')=='9\n'
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 assert request({'action':'settings_get'})['diskGiB']==24
 assert '24' in cell('import os\ns=os.statvfs("/workspace");print(round(s.f_blocks*s.f_frsize/1024**3))')
 print('PASS no shrinking, invalid/stale rejection, same-size no-op, persistent disk size after VM restart',flush=True)
finally:
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 if directory.exists():shutil.rmtree(directory)
 settings.unlink(missing_ok=True);launch.unlink(missing_ok=True)
 subprocess.run(['systemctl','revert',f'agora-runtime@{cid}.service'],check=True,stdout=subprocess.DEVNULL)
 print('Removed only the temporary disk verification VM and settings',flush=True)
