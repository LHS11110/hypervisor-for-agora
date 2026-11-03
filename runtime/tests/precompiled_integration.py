"""Root-only KVM regression; reserves and removes only canvas 2147483014."""
import socket,json,subprocess,shutil
from pathlib import Path
cid=2147483014;root=Path('/var/lib/agora-runtime');directory=root/'canvases'/str(cid);settings=root/'settings'/f'{cid}.json';launch=root/'launch'/f'{cid}.env'
assert not directory.exists() and not settings.exists() and not launch.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(100);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n')
  return json.loads(s.makefile('rb').readline(500000))
revision=0
def configure(language,config):
 global revision
 r=request({'action':'settings_set','language':language,'config':config,'revision':revision});assert 'error' not in r,r;revision=r['revision']
cases={'python':'print(input())','javascript':'console.log(require("fs").readFileSync(0,"utf8").trim())','cpp':'#include <iostream>\nint main(){std::string s;std::cin>>s;std::cout<<s<<"\\n";}','c':'#include <stdio.h>\nint main(){char s[80];scanf("%79s",s);puts(s);}','java':'public class Main {public static void main(String[] a){System.out.println(new java.util.Scanner(System.in).nextLine());}}','bash':'read -r value; echo "$value"'}
try:
 for language,code in cases.items():
  configure(language,{'executionMode':'precompiled'})
  payload={'language':language,'code':code}
  r=request(payload);assert r.get('exitCode')==1 and '빌드' in r['output'],r
  r=request({**payload,'action':'build'});assert r.get('exitCode')==0,r
  for i in range(2):
   r=request({**payload,'stdin':'artifact-ok\n'});assert r.get('exitCode')==0 and r['output']=='artifact-ok\n',r
  r=request({'language':language,'code':code+'\n '});assert r.get('exitCode')==1,r
  print('PASS KVM build/run/stdin/stale',language,flush=True)
 assert request({'action':'reset'})['reset']
 r=request({'language':'python','code':cases['python'],'stdin':'after-reset\n'});assert r.get('exitCode')==0 and r['output']=='after-reset\n',r
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 r=request({'language':'cpp','code':cases['cpp'],'stdin':'after-restart\n'});assert r.get('exitCode')==0 and r['output']=='after-restart\n',r
 configure('python',{'executionMode':'interactive'})
 assert request({'language':'python','code':'value=41'})['exitCode']==0
 assert request({'language':'python','code':'print(value+1)'})['output']=='42\n'
 print('PASS artifacts survive reset/restart; default persistent execution retained',flush=True)
finally:
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 subprocess.run(['systemctl','stop',f'agora-runtime-network@{cid}.service'],check=True)
 if directory.exists():shutil.rmtree(directory)
 settings.unlink(missing_ok=True);launch.unlink(missing_ok=True)
 subprocess.run(['systemctl','revert',f'agora-runtime@{cid}.service'],check=True,stdout=subprocess.DEVNULL)
