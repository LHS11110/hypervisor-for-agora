"""Root-only live KVM regression; reserves and removes only canvas 2147483005."""
import socket,json,subprocess,shutil
from pathlib import Path
cid=2147483005;root=Path('/var/lib/agora-runtime');directory=root/'canvases'/str(cid);settings=root/'settings'/f'{cid}.json'
launch=root/'launch'/f'{cid}.env'
assert not directory.exists() and not settings.exists() and not launch.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(65);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n')
  return json.loads(s.makefile('rb').readline(500000))
revision=0
def configure(lang,config):
 global revision
 r=request({'action':'settings_set','language':lang,'config':config,'revision':revision});assert 'error' not in r,r;revision=r['revision'];return r
def cell(lang,code,expected=None):
 r=request({'language':lang,'code':code});assert r.get('exitCode')==0 and not r.get('environmentReset'),r
 if expected is not None:assert r['output']==expected,r
 return r
try:
 assert request({'action':'settings_get'})['revision']==0
 configure('python',{'engine':'py311','optimize':'2'});assert not directory.exists()
 cell('python','import sys; value=17; print(sys.version_info.minor,sys.flags.optimize)','11 2\n')
 for engine in ['clang19','clang21']:
  configure('cpp',{'engine':engine,'standard':'c++20','optimization':'2','warnings':True,'warningsAsErrors':True})
  cell('cpp','#include <cstdio>\nint count=0;\nprintf("%ld %d\\n",__cplusplus,++count);','202002 1\n')
  cell('cpp','#include <cstdio>\nint count=0;\nprintf("%ld %d\\n",__cplusplus,++count);','202002 2\n')
 cell('python','print(value)','17\n')
 configure('cpp',{'engine':'gcc14','standard':'c++23'});cell('cpp','#include <cstdio>\nint main(){printf("%d\\n",__GNUC__);}','14\n')
 for engine,version in [('jdk17','17'),('jdk21','21'),('jdk25','25')]:
  configure('java',{'engine':engine})
  cell('java','int value=10; System.out.println(System.getProperty("java.specification.version"));',version+'\n')
  cell('java','System.out.println(++value);','11\n')
 for engine,version in [('py312',12),('py313',13),('py314',14),('py315',15)]:
  configure('python',{'engine':engine});cell('python','import sys;print(sys.version_info.minor)',str(version)+'\n')
 for engine,version in [('node20','20'),('node22','22'),('node24','24')]:
  configure('javascript',{'engine':engine,'strict':True});cell('javascript','console.log(process.versions.node.split(".")[0])',version+'\n')
 stale=request({'action':'settings_set','language':'java','config':{'engine':'jdk17'},'revision':0});assert stale.get('error')=='RUNTIME_CONFIG_INVALID',stale
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 assert request({'action':'settings_get'})['revision']==revision
 cell('python','import sys;print(sys.version_info.minor)','15\n')
 print('PASS actual KVM: all Python/Clang/GCC/JDK/Node versions, options, persistent state, isolated language reset, stale revision rejection, VM restart persistence',flush=True)
finally:
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 if directory.exists():shutil.rmtree(directory)
 settings.unlink(missing_ok=True);launch.unlink(missing_ok=True)
 subprocess.run(['systemctl','revert',f'agora-runtime@{cid}.service'],check=True,stdout=subprocess.DEVNULL)
