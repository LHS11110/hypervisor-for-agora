"""Run inside guest image as UID 1000; verifies real toolchains and artifact-only runs."""
import os,sys,json
from pathlib import Path
sys.path.insert(0,'/usr/local/lib')
import artifact_worker as worker
import runtime_options as options
cases={
'python':'print("artifact-ok")',
'javascript':'console.log("artifact-ok")',
'cpp':'#include <cstdio>\nint main(){puts("artifact-ok");}',
'c':'#include <stdio.h>\nint main(void){puts("artifact-ok");}',
'java':'public class Main {public static void main(String[] args){System.out.println("artifact-ok");}}',
'bash':'echo artifact-ok',
}
for language,code in cases.items():
 for engine in options.CATALOG[language]['engines']:
  config=options.normalize(language,{'engine':engine,'executionMode':'precompiled'})
  request={'code':code}
  assert worker.handle(language,config,request)==1
  assert worker.handle(language,config,{**request,'action':'build'})==0,(language,engine)
  directory=worker.artifact_path(language,config,code)
  before={str(p):p.stat().st_mtime_ns for p in directory.rglob('*') if p.is_file()}
  assert worker.handle(language,config,request)==0,(language,engine)
  assert worker.handle(language,config,request)==0
  assert before=={str(p):p.stat().st_mtime_ns for p in directory.rglob('*') if p.is_file()},(language,engine,'recompiled during run')
  assert worker.handle(language,config,{'code':code+'\n '})==1
  assert worker.handle(language,config,{**request,'action':'build'})==0
  print('PASS',language,engine,flush=True)
# A build must never execute program side effects.
config=options.normalize('python',{'executionMode':'precompiled'})
code='from pathlib import Path;Path("/workspace/side-effect").write_text("ran")'
assert worker.handle('python',config,{'action':'build','code':code})==0
assert not Path('/workspace/side-effect').exists()
assert worker.handle('python',config,{'code':code})==0
assert Path('/workspace/side-effect').read_text()=='ran'
assert worker.handle('python',config,{'action':'build','code':'def broken('})!=0
assert worker.handle('python',config,{'code':'def broken('})==1
print('PASS no execution at build; failed builds cannot run',flush=True)
