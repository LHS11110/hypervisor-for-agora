#!/usr/bin/env python3
"""Patch a stopped guest through a dedicated mount-capable systemd oneshot."""
import hashlib,os,shutil,subprocess,sys,tempfile
from pathlib import Path
SOURCE=Path('/usr/local/lib/agora-hypervisor/guest')
STATE=Path('/var/lib/agora-runtime/canvases')
def run(*args):subprocess.run(args,check=True,capture_output=True)
def update(canvas_id):
 if not canvas_id.isdecimal() or not 0<int(canvas_id)<=2147483647:raise ValueError('Invalid canvas')
 canvas_id=str(int(canvas_id));directory=STATE/canvas_id;disk=directory/'guest.raw'
 if subprocess.run(['systemctl','is-active','--quiet',f'agora-runtime@{canvas_id}.service']).returncode==0:raise RuntimeError('Guest must be stopped')
 if not disk.is_file():raise ValueError('Guest disk missing')
 names=('runner.py','terminal.py','notebook.py','notebook_worker.py','package_manager.py','workspace.py','workspace-crdt.cjs')
 version=hashlib.sha256(b''.join((SOURCE/name).read_bytes() for name in names)).hexdigest()
 marker=directory/'agent-version'
 if marker.exists() and marker.read_text()==version:return
 with tempfile.TemporaryDirectory(prefix='agora-agent-patch-') as temporary:
  run('mount','-o','loop,rw,nosuid,nodev,noexec',str(disk),temporary)
  try:
   lib=Path(temporary)/'usr/local/lib'
   for source,name in [(name,'agora-runner.py' if name=='runner.py' else name) for name in names]:
    staged=lib/(name+'.new');staged.unlink(missing_ok=True)
    shutil.copyfile(SOURCE/source,staged);os.chmod(staged,0o644);os.replace(staged,lib/name)
   vendor=SOURCE/'notebook-py312'
   if vendor.is_dir() and not (Path(temporary)/'opt/agora/notebook-py312').is_dir():shutil.copytree(vendor,Path(temporary)/'opt/agora/notebook-py312')
   run('sync','-f',temporary)
  finally:run('umount',temporary)
 marker.write_text(version)
if __name__=='__main__':
 if os.geteuid()!=0 or len(sys.argv)!=2:raise SystemExit(2)
 update(sys.argv[1])
