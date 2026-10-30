"""Root-only real VM check: unlimited file size in every supported language."""
import json,shutil,socket,subprocess
from pathlib import Path
cid=2147483009;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists() and not (state/'settings'/f'{cid}.json').exists()
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
  connection.settimeout(65);connection.connect('/run/agora-runtime/manager.sock');connection.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(connection.makefile('rb').readline(500000))
cases=[
 ('python','import resource\nfrom pathlib import Path\nassert resource.getrlimit(resource.RLIMIT_FSIZE)==(resource.RLIM_INFINITY,resource.RLIM_INFINITY)\nPath("python-large.bin").write_bytes(b"a"*(8*1024*1024))\nprint(Path("python-large.bin").stat().st_size)','8388608\n'),
 ('javascript','var fs=require("fs");fs.writeFileSync("node-large.bin",Buffer.alloc(8*1024*1024,42));console.log(fs.statSync("node-large.bin").size);','8388608\n'),
 ('bash','test "$(ulimit -f)" = unlimited && dd if=/dev/zero of=shell-large.bin bs=1048576 count=8 status=none && stat -c %s shell-large.bin','8388608\n'),
 ('cpp','#include <fstream>\n#include <cstdio>\n#include <sys/resource.h>\nvoid makeLarge(){char b[65536]={};std::ofstream f("cpp-large.bin",std::ios::binary);for(int i=0;i<128;i++)f.write(b,sizeof(b));f.close();struct rlimit r;getrlimit(RLIMIT_FSIZE,&r);std::ifstream in("cpp-large.bin",std::ios::binary|std::ios::ate);printf("%d %lld\\n",r.rlim_cur==RLIM_INFINITY&&r.rlim_max==RLIM_INFINITY,(long long)in.tellg());}\nmakeLarge();','1 8388608\n'),
 ('c','#include <stdio.h>\n#include <sys/resource.h>\nvoid makeLarge(void){char b[65536]={0};FILE*f=fopen("c-large.bin","wb");for(int i=0;i<128;i++)fwrite(b,1,sizeof(b),f);long n=ftell(f);fclose(f);struct rlimit r;getrlimit(RLIMIT_FSIZE,&r);printf("%d %ld\\n",r.rlim_cur==RLIM_INFINITY&&r.rlim_max==RLIM_INFINITY,n);}\nmakeLarge();','1 8388608\n'),
 ('java','java.nio.file.Files.write(java.nio.file.Path.of("java-large.bin"),new byte[8*1024*1024]);System.out.println(java.nio.file.Files.size(java.nio.file.Path.of("java-large.bin")));','8388608\n'),
]
try:
 for language,code,expected in cases:
  result=request({'language':language,'code':code});assert result.get('exitCode')==0 and result['output']==expected,result
  assert request({'action':'reset'})=={'reset':True}
  print(f'PASS {language}: 8 MiB file successfully written; unlimited inherited file-size limit',flush=True)
finally:
 result=request({'action':'delete'})
 assert result=={'deleted':True},result
 marker.unlink(missing_ok=True)
 print('Removed only the dedicated file-size test VM and its files',flush=True)
