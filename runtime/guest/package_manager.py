#!/usr/local/bin/python3.12 -I
"""Guest-only apt bridge: unprivileged clients, narrowly validated root operations."""
import base64,json,os,re,selectors,signal,socket,stat,subprocess,sys,threading,time
from pathlib import Path
SOCKET='/run/agora-packages.sock'
TOOLS={'apt','apt-get','apt-cache'}
ACTIONS={'update','install','reinstall','remove','purge','autoremove','autopurge','upgrade','full-upgrade','dist-upgrade','clean','autoclean','check'}
FLAGS={'-y','--yes','--assume-yes','-q','-qq','--quiet','-s','--simulate','--dry-run','--no-act','-f','--fix-broken','--fix-missing','--no-install-recommends','--no-install-suggests','--reinstall','--only-upgrade','--download-only','-d','--no-download','--no-remove','--allow-releaseinfo-change'}
PACKAGE=re.compile(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?(?:=[A-Za-z0-9.+:~_-]+)?(?:/[A-Za-z0-9._-]+)?\Z')

def validated_command(request):
 if not isinstance(request,dict) or set(request)!={'tool','args'} or not isinstance(request['tool'],str) or request['tool'] not in TOOLS:raise ValueError('Invalid package request')
 args=request['args']
 if not isinstance(args,list) or not 1<=len(args)<=128 or any(not isinstance(v,str) or len(v)>256 for v in args):raise ValueError('Invalid apt arguments')
 command=None;options=[];packages=[]
 for value in args:
  if value.startswith('-'):
   if value not in FLAGS:raise ValueError('Unsupported apt option: '+value)
   options.append(value)
  elif command is None:
   if value not in ACTIONS:raise ValueError('Unsupported apt operation')
   command=value
  elif not PACKAGE.fullmatch(value):raise ValueError('Only repository package names are allowed')
  else:packages.append(value)
 if command is None or request['tool']=='apt-cache':raise ValueError('Use apt-cache directly as the workspace user')
 if command not in {'install','reinstall','remove','purge'} and packages:raise ValueError('This apt operation does not accept package names')
 if command in {'install','reinstall','remove','purge'} and not packages and '-f' not in options and '--fix-broken' not in options:raise ValueError('Package name required')
 if command=='reinstall':command='install';options.append('--reinstall')
 if command in {'full-upgrade'}:command='dist-upgrade'
 if command=='autopurge':command='autoremove';options.append('--purge')
 return ['/usr/bin/apt-get','-y',*options,command,*packages]

def setup():
 # Debian base images use HTTP, while canvas egress intentionally permits HTTPS.
 files=[Path('/etc/apt/sources.list'),*Path('/etc/apt/sources.list.d').glob('*.list'),*Path('/etc/apt/sources.list.d').glob('*.sources')]
 for path in files:
  if path.is_file():
   before=path.read_text();after=before.replace('http://deb.debian.org/','https://deb.debian.org/').replace('http://security.debian.org/','https://security.debian.org/')
   if after!=before:path.write_text(after)
 source=Path(__file__).resolve();source.chmod(0o755)
 for name in TOOLS:
  target=Path('/usr/local/bin')/name
  if target.is_symlink() and target.resolve()==source:continue
  if target.exists() or target.is_symlink():raise RuntimeError('Package launcher already exists: '+str(target))
  target.symlink_to(source)

class PackageService:
 def __init__(self,limits):self.limits=limits;self.lock=threading.Lock();self.slots=threading.BoundedSemaphore(8)
 def send(self,conn,value):conn.sendall(json.dumps(value).encode()+b'\n')
 def restrict(self):
  os.setsid();group=Path('/sys/fs/cgroup/agora-packages');group.mkdir(exist_ok=True)
  (group/'memory.max').write_text(str(min(1024,self.limits['workerMemoryMiB'])*1024**2));(group/'pids.max').write_text('128');(group/'cpu.max').write_text(f"{self.limits['vcpus']*100000} 100000");(group/'cgroup.procs').write_text(str(os.getpid()))
 def connection(self,conn):
  process=None;locked=False;disconnected=False
  try:
   conn.settimeout(10);credentials=conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12);uid=int.from_bytes(credentials[4:8],sys.byteorder)
   if uid not in (0,1000):raise ValueError('Only the workspace user can manage VM packages')
   with conn.makefile('rb') as reader:line=reader.readline(16385)
   if len(line)>16384 or not line.endswith(b'\n'):raise ValueError('Package request too large')
   command=validated_command(json.loads(line))
   if not self.lock.acquire(blocking=False):raise ValueError('Another package operation is running; retry when it finishes')
   locked=True
   process=subprocess.Popen(command,cwd='/',env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8','DEBIAN_FRONTEND':'noninteractive'},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,preexec_fn=self.restrict)
   deadline=time.monotonic()+900
   with selectors.DefaultSelector() as select:
    select.register(process.stdout,selectors.EVENT_READ);select.register(conn,selectors.EVENT_READ)
    while True:
     if time.monotonic()>deadline:os.killpg(process.pid,signal.SIGKILL);raise ValueError('Package operation exceeded 15 minutes')
     events=select.select(.2);eof=False
     for key,_ in events:
      if key.fileobj is conn:
       # The client is output-only after its request. Ctrl+C/disconnect interrupts
       # apt rather than leaving an unseen installation holding the dpkg lock.
       try:data=conn.recv(1)
       except OSError:data=b''
       if not data:
        disconnected=True;select.unregister(conn)
        try:os.killpg(process.pid,signal.SIGINT)
        except ProcessLookupError:pass
        deadline=min(deadline,time.monotonic()+15)
       else:raise ValueError('Unexpected package input')
      else:
       data=os.read(process.stdout.fileno(),16384)
       if not data:eof=True;break
       if not disconnected:
        try:self.send(conn,{'type':'output','data':base64.b64encode(data).decode()})
        except OSError:disconnected=True
     if eof:break
   status=process.wait(timeout=5);os.sync()
   if not disconnected:self.send(conn,{'type':'exit','exitCode':status})
  except (ValueError,OSError,subprocess.SubprocessError) as error:
   try:self.send(conn,{'type':'error','message':str(error)[:300],'exitCode':1})
   except OSError:pass
  finally:
   if process:
    try:(Path('/sys/fs/cgroup/agora-packages')/'cgroup.kill').write_text('1')
    except OSError:pass
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    process.wait();process.stdout.close()
   if locked:self.lock.release()
   conn.close();self.slots.release()
 def serve(self):
  path=Path(SOCKET)
  if path.exists():
   if not stat.S_ISSOCK(path.lstat().st_mode):raise RuntimeError('Invalid package socket path')
   path.unlink()
  listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(SOCKET);os.chown(SOCKET,0,1000);os.chmod(SOCKET,0o660);listener.listen(8)
  while True:
   conn,_=listener.accept()
   if not self.slots.acquire(blocking=False):conn.close();continue
   threading.Thread(target=self.connection,args=(conn,),daemon=True).start()

def exit_status(status):return min(255,status if status>=0 else 128-status)

def client(tool,args):
 if tool not in TOOLS:raise SystemExit('Use apt, apt-get or apt-cache')
 operation=next((v for v in args if not v.startswith('-')),None)
 if operation not in ACTIONS or tool=='apt-cache':os.execv('/usr/bin/'+tool,[tool,*args])
 try:
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
   conn.connect(SOCKET);conn.sendall((json.dumps({'tool':tool,'args':args})+'\n').encode())
   with conn.makefile('rb') as reader:
    for line in reader:
     frame=json.loads(line)
     if frame['type']=='output':sys.stdout.buffer.write(base64.b64decode(frame['data']));sys.stdout.buffer.flush()
     elif frame['type']=='exit':return exit_status(frame['exitCode'])
     else:print(frame.get('message','Package operation failed'),file=sys.stderr);return 1
  return 1
 except KeyboardInterrupt:return 130
 except OSError as error:print('VM package manager is unavailable: '+str(error),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(client(Path(sys.argv[0]).name,sys.argv[1:]))
