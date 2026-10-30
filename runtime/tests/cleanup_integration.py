"""Root-only live cleanup test; owns only reserved canvas 2147483008 and its test backup directory."""
import json,shutil,socket,subprocess
from pathlib import Path
cid=2147483008;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid)
marker=state/'deleted'/str(cid);backup=state/f'backup-cleanup-test-{cid}'
assert not directory.exists() and not marker.exists() and not backup.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(65);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
try:
 settings=request({'action':'settings_set','language':'python','config':{'engine':'py313'},'revision':0});assert settings['revision']==1,settings
 result=request({'language':'python','code':'from pathlib import Path;Path("delete-proof.txt").write_text("owned test data");print("ready")'});assert result.get('exitCode')==0,result
 assert directory.exists() and (state/'settings'/f'{cid}.json').exists() and (state/'launch'/f'{cid}.env').exists()
 backup.mkdir();(backup/f'{cid}.raw').write_bytes(b'old canvas backup');(backup/'base.raw').write_bytes(b'OS backup');(backup/'2147483009.raw').write_bytes(b'other canvas')
 assert request({'action':'delete'})=={'deleted':True}
 for path in [directory,state/'settings'/f'{cid}.json',state/'launch'/f'{cid}.env',Path('/run/netns')/f'agora-runtime-{cid}',Path('/run/systemd/system.control')/f'agora-runtime@{cid}.service.d',backup/f'{cid}.raw']:assert not path.exists(),path
 assert (backup/'base.raw').exists() and (backup/'2147483009.raw').exists() and marker.exists()
 assert subprocess.run(['systemctl','is-active','--quiet',f'agora-runtime@{cid}.service']).returncode!=0
 assert request({'action':'delete'})=={'deleted':True}
 assert request({'language':'python','code':'print(1)'}).get('error')=='RUNTIME_CONFIG_INVALID'
 assert not directory.exists()
 print('PASS actual KVM: stopped VM; removed disk, workspace, settings, launch config, network, overrides and matching rollback backup; idempotent; stale execution cannot recreate VM; other backups preserved',flush=True)
finally:
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 if directory.exists():shutil.rmtree(directory)
 for path in [state/'settings'/f'{cid}.json',state/'launch'/f'{cid}.env',marker]:path.unlink(missing_ok=True)
 if backup.exists():shutil.rmtree(backup)
 subprocess.run(['systemctl','revert',f'agora-runtime@{cid}.service'],check=True,stdout=subprocess.DEVNULL)
 print('Removed only the test VM, marker and backup fixtures',flush=True)
