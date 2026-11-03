"""Root-only KVM stdin test; owns only reserved canvas 2147483013."""
import json,socket
from pathlib import Path
cid=2147483013;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(75);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
def cell(language,code,stdin=None):
 data={'language':language,'code':code}
 if stdin is not None:data['stdin']=stdin
 result=request(data);assert result.get('exitCode')==0 and not result.get('environmentReset'),result
 return result['output']
try:
 output=cell('python','retained=41\nprint(10)\nprint(20)')
 assert output=='10\n20\n',output
 cpp='#include <iostream>\nint main(){int a,b;if(!(std::cin>>a>>b)){std::cout<<"EOF\\n";return 2;}std::cout<<a+b<<"\\n";return 0;}'
 assert cell('cpp',cpp,output)=='30\n'
 assert cell('cpp',cpp,'7\n8\nunused\n')=='15\n'
 result=request({'language':'cpp','code':cpp});assert result.get('exitCode')==2 and result['output']=='EOF\n' and not result['environmentReset'],result
 assert cell('cpp',cpp,'1\n2\n')=='3\n'
 print('PASS actual VM Python output -> C++ cin, cached re-execution, EOF and fresh input',flush=True)
 assert cell('python','print(input());print(input());print(retained)','first\nsecond\n')=='first\nsecond\n41\n'
 result=request({'language':'python','code':'input()'});assert result.get('exitCode')==1 and 'EOFError' in result['output'] and not result['environmentReset'],result
 assert cell('python','print(input());print(retained)','다음 입력\n')=='다음 입력\n41\n'
 print('PASS actual VM Python ordered lines, no-input EOFError, Unicode, persistent namespace after EOF',flush=True)
 assert cell('c','#include <stdio.h>\nint main(){int value;if(scanf("%d",&value)!=1)return 2;printf("%d\\n",value*2);return 0;}','6\n')=='12\n'
 assert cell('java','class Main{public static void main(String[] args){java.util.Scanner s=new java.util.Scanner(System.in);System.out.println(s.nextInt()+s.nextInt());}}','3\n4\n')=='7\n'
 assert cell('javascript','console.log(require("fs").readFileSync(0,"utf8").trim())','js input\n')=='js input\n'
 assert cell('bash','read -r line && printf "%s\\n" "$line"','shell input\n')=='shell input\n'
 print('PASS actual VM C scanf, Java Scanner, JavaScript fd 0 and Shell read through the manager/guest boundary',flush=True)
finally:
 result=request({'action':'delete'});assert result=={'deleted':True},result
 marker.unlink(missing_ok=True)
 assert not directory.exists()
 print('Removed only the dedicated stdin test VM, workspace and marker',flush=True)
