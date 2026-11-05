"""Host-only VSOCK PTY service. Shells run as workspace UID, never guest root."""
import base64,ctypes,fcntl,json,os,pty,re,resource,sys,select,selectors,signal,socket,struct,subprocess,termios,threading,time
from pathlib import Path
MAX_BUFFER=262144
IDLE=120
CGROUP_ROOT=Path('/sys/fs/cgroup/agora-terminal')
WORKSPACE=Path('/workspace')
class PtySession:
 def __init__(self,cols,rows,python_engine='py312'):
  self.master,slave=pty.openpty();self.buffer=bytearray();self.input=bytearray();self.offset=0;self.lock=threading.Lock();self.last=time.monotonic()
  CGROUP_ROOT.mkdir(exist_ok=True);(CGROUP_ROOT/'cgroup.subtree_control').write_text('+cpu +memory +pids')
  self.group=CGROUP_ROOT/str(time.monotonic_ns());self.group.mkdir()
  (self.group/'pids.max').write_text('128');(self.group/'memory.max').write_text(str(512*1024**2));(self.group/'cpu.max').write_text('100000 100000')
  try:
   self.process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--child',str(self.group)],stdin=slave,stdout=slave,stderr=slave,cwd=WORKSPACE,env={'HOME':'/workspace','TERM':'xterm-256color','LANG':'C.UTF-8','PATH':'/opt/agora/python-tools:/workspace/.local/bin:/usr/local/bin:/usr/bin:/bin','AGORA_PYTHON_ENGINE':python_engine,'PS1':'\\u@canvas:\\w\\$ '},start_new_session=True)
  finally:os.close(slave)
  os.set_blocking(self.master,False);self.resize(cols,rows)
  self.thread=threading.Thread(target=self.read,daemon=True);self.thread.start()
 def resize(self,cols,rows):fcntl.ioctl(self.master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
 def read(self):
  selector=selectors.DefaultSelector();selector.register(self.master,selectors.EVENT_READ)
  try:
   while True:
    with self.lock:
     if self.input:
      try:
       count=os.write(self.master,self.input);del self.input[:count]
      except BlockingIOError:pass
      except OSError:self.input.clear()
    ready=selector.select(.02)
    if not ready and self.process.poll() is not None:break
    for _,_ in ready:
     try:data=os.read(self.master,8192)
     except OSError:return
     if not data:return
     with self.lock:
      self.buffer.extend(data);self.offset+=len(data)
      if len(self.buffer)>MAX_BUFFER:del self.buffer[:-MAX_BUFFER]
  finally:selector.close()
 def result(self,offset):
  with self.lock:
   start=self.offset-len(self.buffer);begin=max(start,min(offset,self.offset));data=bytes(self.buffer[begin-start:begin-start+32768])
   return {'data':base64.b64encode(data).decode(),'offset':begin+len(data),'truncated':offset<start,'exited':self.process.poll() is not None,'exitCode':self.process.poll()}
 def close(self):
  try:(self.group/'cgroup.kill').write_text('1')
  except OSError:
   try:os.killpg(self.process.pid,signal.SIGKILL)
   except ProcessLookupError:pass
  self.process.wait(timeout=3);self.thread.join(1);os.close(self.master)
  try:self.group.rmdir()
  except OSError:pass

class TerminalService:
 def __init__(self):self.sessions={};self.lock=threading.RLock()
 def handle(self,request):
  allowed={'session','operation','data','cols','rows','offset'}
  if not isinstance(request,dict) or set(request)-{'pythonEngine'}!=allowed:raise ValueError('Invalid terminal request')
  python_engine=request.get('pythonEngine','py312')
  if python_engine not in ('py311','py312','py313','py314','py315'):raise ValueError('Invalid Python engine')
  key=request['session'];op=request['operation'];cols=request['cols'];rows=request['rows'];offset=request['offset'];data=request['data']
  if not isinstance(key,str) or not re.fullmatch(r'[1-9][0-9]{0,18}:[a-zA-Z0-9_-]{1,128}',key):raise ValueError('Invalid session')
  if op not in ('open','poll','input','resize','close') or type(cols)is not int or not 2<=cols<=300 or type(rows)is not int or not 2<=rows<=150 or type(offset)is not int or offset<0 or not isinstance(data,str) or len(data.encode())>8192:raise ValueError('Invalid terminal operation')
  with self.lock:
   self.reap()
   session=self.sessions.get(key)
   if op=='close':
    if session:session.close();del self.sessions[key]
    return {'closed':True}
   if session is None or (op=='open' and session.process.poll() is not None):
    if op!='open':return {'expired':True}
    if session:session.close();del self.sessions[key]
    if len(self.sessions)>=8:raise ValueError('Terminal limit reached')
    session=PtySession(cols,rows,python_engine);self.sessions[key]=session
   session.last=time.monotonic()
   if op=='input' and data:
    with session.lock:
     if len(session.input)+len(data.encode())>65536:raise ValueError('Terminal input busy')
     encoded=data.encode();written=0
     if not session.input:
      try:written=os.write(session.master,encoded)
      except BlockingIOError:pass
     session.input.extend(encoded[written:])
   if op in ('open','resize'):session.resize(cols,rows)
   return session.result(offset)
 def attach(self,conn,request):
  base={**request,'operation':'open'};result=self.handle(base);cursor=result.get('offset',0)
  conn.sendall(json.dumps({'type':'ready',**result}).encode()+b'\n')
  pending=bytearray();heartbeat=time.monotonic()
  while True:
   readable,_,_=select.select([conn],[],[],.01)
   if readable:
    data=conn.recv(16384)
    if not data:return
    pending.extend(data)
    if len(pending)>60000:raise ValueError('Terminal input frame too large')
    while b'\n' in pending:
     wire,_,tail=pending.partition(b'\n');pending=bytearray(tail);command=json.loads(wire)
     if not isinstance(command,dict) or set(command)!={'operation','data','cols','rows'} or command['operation'] not in ('input','resize','close'):raise ValueError('Invalid stream operation')
     result=self.handle({**base,**command,'offset':cursor});cursor=result.get('offset',cursor)
     if result.get('closed'):
      conn.sendall(json.dumps({'type':'closed',**result}).encode()+b'\n');return
     if result.get('expired'):raise ValueError('Terminal expired')
     if result.get('data') or result.get('exited'):conn.sendall(json.dumps({'type':'output',**result}).encode()+b'\n')
   result=self.handle({**base,'operation':'poll','offset':cursor});cursor=result.get('offset',cursor)
   if result.get('expired'):raise ValueError('Terminal expired')
   if result.get('data') or result.get('exited') or time.monotonic()-heartbeat>=5:
    conn.sendall(json.dumps({'type':'output',**result}).encode()+b'\n');heartbeat=time.monotonic()
   if result.get('exited'):return
 def connection(self,conn,peer,slots):
  try:
   with conn:
    conn.settimeout(5)
    if peer[0]!=socket.VMADDR_CID_HOST:return
    try:
     with conn.makefile('rb') as reader:wire=reader.readline(60000)
     if not wire.endswith(b'\n'):raise ValueError('Invalid frame')
     request=json.loads(wire)
     if request.get('operation')=='attach':self.attach(conn,request);return
     result=self.handle(request)
    except (ValueError,OSError,subprocess.SubprocessError):result={'error':'Terminal unavailable or invalid request'}
    try:conn.sendall(json.dumps(result).encode()+b'\n')
    except OSError:pass
  finally:slots.release()
 def reap(self):
  for key,session in list(self.sessions.items()):
   if time.monotonic()-session.last>IDLE:session.close();del self.sessions[key]
 def serve(self):
  if not os.path.ismount('/dev/pts'):
   Path('/dev/pts').mkdir(exist_ok=True)
   subprocess.run(['mount','-t','devpts','-o','newinstance,nosuid,noexec,ptmxmode=0666,mode=0620,gid=5','devpts','/dev/pts'],check=True)
   Path('/dev/ptmx').unlink(missing_ok=True);Path('/dev/ptmx').symlink_to('pts/ptmx')
  def reaper():
   while True:
    time.sleep(10)
    with self.lock:self.reap()
  threading.Thread(target=reaper,daemon=True).start()
  listener=socket.socket(socket.AF_VSOCK,socket.SOCK_STREAM);listener.bind((socket.VMADDR_CID_ANY,5001));listener.listen(16)
  slots=threading.BoundedSemaphore(32)
  while True:
   conn,peer=listener.accept()
   if not slots.acquire(blocking=False):conn.close();continue
   threading.Thread(target=self.connection,args=(conn,peer,slots),daemon=True).start()

if __name__=='__main__':
 if len(sys.argv)!=3 or sys.argv[1]!='--child':raise SystemExit(2)
 group=Path(sys.argv[2])
 if not group.is_relative_to(CGROUP_ROOT):raise SystemExit(2)
 fcntl.ioctl(0,termios.TIOCSCTTY,0)
 (group/'cgroup.procs').write_text(str(os.getpid()))
 resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_NOFILE,(256,256))
 os.setgroups([]);os.setgid(1000);os.setuid(1000)
 if ctypes.CDLL(None).prctl(38,1,0,0,0)!=0:raise SystemExit(126)
 os.execv('/bin/bash',['bash','--noprofile','--norc','-i'])
