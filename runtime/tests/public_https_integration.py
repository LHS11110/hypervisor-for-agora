"""Root-only real VM regression: public proxy HTTPS, pip, and private isolation."""
import json,shutil,socket,subprocess,time
from pathlib import Path
CID=2147483016
root=Path('/var/lib/agora-runtime');directory=root/'canvases'/str(CID)
assert not directory.exists() and not (root/'settings'/f'{CID}.json').exists()
def request(language,code):
 start=time.monotonic()
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(100);s.connect('/run/agora-runtime/manager.sock');s.sendall(json.dumps({'canvasId':CID,'language':language,'code':code}).encode()+b'\n')
  result=json.loads(s.makefile('rb').readline(500000))
 print(language,round(time.monotonic()-start,3),'s',json.dumps(result,ensure_ascii=False),flush=True)
 assert result.get('exitCode')==0 and not result.get('timedOut') and not result.get('environmentReset'),result
 return result['output']
try:
 request('python','import urllib.request,socket\nprint(socket.gethostbyname("frelog.p-e.kr"))\nwith urllib.request.urlopen("https://frelog.p-e.kr/downloads/agora_canvas-0.1.2-py3-none-any.whl",timeout=5) as response: print(response.status,len(response.read()))')
 request('bash','python -m pip install https://frelog.p-e.kr/downloads/agora_canvas-0.1.2-py3-none-any.whl')
 request('python','from agora_canvas import AgoraClient\nimport httpx,websockets\nwith AgoraClient() as client: print(client.request("GET","/api/auth/health")["status"])')
 print('PASS actual guest DNS/TLS/wheel download, Bash pip install, Python import and SDK HTTPS health',flush=True)
 # Check six blocked destinations concurrently to stay within the execution limit.
 output=request('python', 'import socket,concurrent.futures\ntargets=[("172.30.1.71",443),("172.30.1.71",22),("10.205.0.1",443),("118.34.144.201",80),("118.34.144.201",22),("169.254.169.254",80)]\ndef blocked(target):\n try:\n  s=socket.create_connection(target,timeout=1);s.close();return False\n except OSError:return True\nwith concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool: print(list(pool.map(blocked,targets)))')
 assert output.strip()=='[True, True, True, True, True, True]',output
 print('PASS host private addresses, SSH, HTTP and metadata endpoints remain blocked',flush=True)
finally:
 for unit in (f'agora-runtime@{CID}.service',f'agora-runtime-network@{CID}.service'):subprocess.run(['systemctl','stop',unit],check=True)
 if directory.exists():shutil.rmtree(directory)
 for folder,suffix in [('settings','.json'),('launch','.env')]: (root/folder/f'{CID}{suffix}').unlink(missing_ok=True)
 subprocess.run(['systemctl','revert',f'agora-runtime@{CID}.service'],stdout=subprocess.DEVNULL,check=True)
