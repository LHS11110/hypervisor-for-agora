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
  request=json.loads(line)
  code=request['code']
  digest=hashlib.sha256(code.encode()).hexdigest()
  binary=cache/digest
  if not binary.exists():
   source=cache/(digest+('.cpp' if language=='cpp' else '.c'))
   source.write_text(code)
   compiled=subprocess.run([compiler,*flags,str(source),'-o',str(binary)+'.next'],stdin=subprocess.DEVNULL)
   status=compiled.returncode
   if status==0:os.replace(str(binary)+'.next',binary)
  else:status=0
  if status==0:
   source='/proc/self/fd/'+os.environ['AGORA_STDIN_FD'] if request.get('stdinProvided') and 'AGORA_STDIN_FD' in os.environ else '/dev/null'
   with open(source,'rb') as input_stream:status=subprocess.run([str(binary)],stdin=input_stream).returncode
 except BaseException:
  traceback.print_exc();status=1
 sys.stdout.flush();sys.stderr.flush()
 control.sendall(json.dumps({'exitCode':status}).encode()+b'\n')
