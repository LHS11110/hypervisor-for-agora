"""Unprivileged GCC program worker; binaries are cached, process memory is per run."""
import hashlib,json,os,socket,subprocess,sys,traceback
from pathlib import Path
control=socket.socket(fileno=int(sys.argv[1]))
language=sys.argv[2]
flags=sys.argv[3:]
compiler='/usr/bin/g++-14' if language=='cpp' else '/usr/bin/gcc-14'
cache=Path('/workspace/.program-cache')/hashlib.sha256(json.dumps([language,flags]).encode()).hexdigest()
cache.mkdir(parents=True,exist_ok=True)
for line in control.makefile('rb'):
 status=1
 try:
  code=json.loads(line)['code']
  digest=hashlib.sha256(code.encode()).hexdigest()
  binary=cache/digest
  if not binary.exists():
   source=cache/(digest+('.cpp' if language=='cpp' else '.c'))
   source.write_text(code)
   compiled=subprocess.run([compiler,*flags,str(source),'-o',str(binary)+'.next'],stdin=subprocess.DEVNULL)
   status=compiled.returncode
   if status==0:os.replace(str(binary)+'.next',binary)
  else:status=0
  if status==0:status=subprocess.run([str(binary)],stdin=subprocess.DEVNULL).returncode
 except BaseException:
  traceback.print_exc();status=1
 sys.stdout.flush();sys.stderr.flush()
 control.sendall(json.dumps({'exitCode':status}).encode()+b'\n')
