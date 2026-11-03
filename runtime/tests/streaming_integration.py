"""Root-only real VM streaming test; owns only reserved canvas 2147483017."""
import json,socket,time
from pathlib import Path
cid=2147483017;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
def request(data,stream=False):
 chunks=[];times=[];start=time.monotonic()
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(75);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data,**({'stream':True} if stream else {})}).encode()+b'\n')
  reader=s.makefile('rb')
  while True:
   line=reader.readline(500000)
   assert line, 'manager disconnected'
   result=json.loads(line)
   if result.get('type')=='output':chunks.append(result['output']);times.append(time.monotonic()-start)
   else:return result,chunks,times,time.monotonic()-start
cases={
 'python':'import time\nstream_state=41\nprint("한글-first",flush=True)\ntime.sleep(.6)\nprint("last",flush=True)',
 'cpp':'#include <iostream>\n#include <thread>\n#include <chrono>\nint main(){std::cout<<"first"<<std::endl;std::this_thread::sleep_for(std::chrono::milliseconds(600));std::cout<<"last"<<std::endl;}',
 'c':'#include <stdio.h>\n#include <unistd.h>\nint main(){printf("first\\n");fflush(stdout);sleep(1);printf("last\\n");fflush(stdout);return 0;}',
 'javascript':'(async()=>{console.log("first");await new Promise(resolve=>setTimeout(resolve,600));console.log("last");})()',
 'java':'System.out.println("first");Thread.sleep(600);System.out.println("last");',
 'bash':'printf "first\\n"; sleep .6; printf "last\\n"',
}
try:
 for language,code in cases.items():
  result,chunks,times,elapsed=request({'language':language,'code':code},True)
  assert result.get('exitCode')==0 and not result.get('environmentReset'),result
  assert ''.join(chunks)==result['output'],(language,chunks,result)
  assert len(chunks)>=2 and elapsed-times[0]>=.45,(language,times,elapsed)
  print('PASS',language,'first output at',round(times[0],3),'s, complete at',round(elapsed,3),'s; streamed Unicode/output preserved',flush=True)
 result,_,_,_=request({'language':'python','code':'print(stream_state+1)'})
 assert result['output']=='42\n' and result['exitCode']==0,result
 result,chunks,times,elapsed=request({'language':'python','code':'print("before-error",flush=True)\nraise ValueError("test-error")'},True)
 assert result['exitCode']==1 and ''.join(chunks)==result['output'] and 'before-error' in result['output'] and 'test-error' in result['output'],result
 print('PASS existing non-streaming API, persistent environment and failed execution retain their outputs',flush=True)
finally:
 result,_,_,_=request({'action':'delete'});assert result=={'deleted':True},result
 marker.unlink(missing_ok=True);assert not directory.exists()
 print('Removed dedicated streaming test VM, workspace and marker',flush=True)
