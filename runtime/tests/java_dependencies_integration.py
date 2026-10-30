"""Root-only real VM/Maven verification; uses only reserved canvas 2147483011."""
import json,socket,subprocess
from pathlib import Path
cid=2147483011;state=Path('/var/lib/agora-runtime');directory=state/'canvases'/str(cid);marker=state/'deleted'/str(cid)
assert not directory.exists() and not marker.exists()
deps=['org.apache.commons:commons-lang3:3.17.0','com.fasterxml.jackson.core:jackson-databind:2.18.2']
def request(data):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(240);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':cid,**data}).encode()+b'\n');return json.loads(s.makefile('rb').readline(500000))
def cell(language,code):
 result=request({'language':language,'code':code});assert result.get('exitCode')==0 and not result.get('environmentReset'),result;return result['output']
try:
 cell('python','retained=17')
 revision=0
 for engine in ['jdk21','jdk17','jdk25']:
  config=request({'action':'settings_set','language':'java','config':{'engine':engine,'dependencies':deps},'revision':revision});assert 'error' not in config,config;revision=config['revision']
  result=request({'language':'java','code':'System.out.println(1);'});assert result.get('exitCode')==1 and not result['environmentReset'],result
  result=request({'action':'java_dependencies'});assert result.get('installed') and result['jarCount']>=4,result
  output=cell('java','import org.apache.commons.lang3.StringUtils;import com.fasterxml.jackson.databind.ObjectMapper;int counter=7;System.out.println(StringUtils.reverse("agora"));System.out.println(new ObjectMapper().readTree("{\\"answer\\":42}").get("answer").asInt());')
  assert output=='aroga\n42\n',output
  assert cell('java','System.out.println(++counter);')=='8\n'
  assert cell('python','print(retained)')=='17\n'
  print(f'PASS {engine}: Maven libraries + transitive JARs imported/executed in JShell; Java state and independent Python state retained',flush=True)
 result=request({'action':'java_dependencies'});assert result.get('installed') and result.get('cached'),result
 subprocess.run(['systemctl','stop',f'agora-runtime@{cid}.service'],check=True)
 assert cell('java','import org.apache.commons.lang3.StringUtils;System.out.println(StringUtils.upperCase("cached"));')=='CACHED\n'
 print('PASS cached reinstall and Java imports after VM restart without another download',flush=True)
finally:
 assert request({'action':'delete'})=={'deleted':True}
 marker.unlink(missing_ok=True)
 print('Removed only the Java dependency test VM, Maven cache and settings',flush=True)
