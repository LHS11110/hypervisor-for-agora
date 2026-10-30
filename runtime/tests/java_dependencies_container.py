"""Run as UID 1000 in the built guest image, with network and writable /workspace."""
import base64,importlib.util,json,os,socket,subprocess
from pathlib import Path

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
runner=module('runner','/usr/local/lib/agora-runner.py');dependencies=module('deps','/usr/local/lib/java_dependencies.py')
config={'engine':'jdk21','dependencies':['org.apache.commons:commons-lang3:3.17.0','com.fasterxml.jackson.core:jackson-databind:2.18.2']}
installed=dependencies.install(config);assert installed['installed'],installed;assert installed['jarCount']>=4,installed
assert dependencies.install(config)['cached']
parent,child=socket.socketpair();parent.settimeout(20)
command,env=runner.worker_command('java',config,str(child.fileno()))
process=subprocess.Popen(command,env=env,cwd='/workspace',stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,pass_fds=(child.fileno(),));child.close()
def cell(code):
 parent.sendall(base64.b64encode(code.encode())+b'\n');reply=b''
 while not reply.endswith(b'\n'):
  chunk=parent.recv(1024)
  if not chunk:raise RuntimeError(process.stdout.read().decode())
  reply+=chunk
 result=json.loads(reply);os.set_blocking(process.stdout.fileno(),False);output=b''
 while True:
  try:output+=os.read(process.stdout.fileno(),8192)
  except BlockingIOError:break
 assert result['exitCode']==0,(result,output)
 return output.decode()
try:
 assert cell('import org.apache.commons.lang3.StringUtils; import com.fasterxml.jackson.databind.ObjectMapper; int value=7; System.out.println(StringUtils.reverse("agora")); System.out.println(new ObjectMapper().readTree("{\\"answer\\":42}").get("answer").asInt());')=='aroga\n42\n'
 assert cell('System.out.println(value+1);')=='8\n'
 print('PASS Maven download + transitive dependencies, cached install, JShell import/execution and persistent namespace',flush=True)
finally:process.kill();process.wait();parent.close()
