#!/usr/bin/env python3
"""Root-only deployment test: uses and removes one dedicated temporary VM.

Checks real DNS/TLS/pip, persistent packages, blocked host/private destinations,
and blocked unsolicited connections to a listening guest from host/another netns.
"""
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

CANVAS = 2147483004
DIRECTORY = Path('/var/lib/agora-runtime/canvases') / str(CANVAS)
ATTACKER = 'agora-egress-test-attacker'
ATTACK_LINK = 'agatk-test'


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def request(code):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(65)
        connection.connect('/run/agora-runtime/manager.sock')
        connection.sendall(json.dumps({'canvasId': CANVAS, 'language': 'python', 'code': code}).encode()+b'\n')
        with connection.makefile('rb') as reader:
            result = json.loads(reader.readline(400001))
    assert result.get('exitCode') == 0 and not result.get('environmentReset'), result
    return result['output']


def main():
    if os.geteuid() != 0: raise SystemExit('Run as root')
    assert not DIRECTORY.exists(), 'Only use an unused temporary VM'
    assert not Path('/run/netns', ATTACKER).exists(), 'Test namespace already exists'
    assert not json.loads(command('ip', '-j', 'route', 'show', 'exact', '10.205.0.2/32').stdout), 'Test route already exists'
    route_added = False
    try:
        output = request('import urllib.request, socket\nprint(socket.gethostbyname("pypi.org"))\nprint(urllib.request.urlopen("https://pypi.org/simple/six/",timeout=8).status)')
        assert output.strip().endswith('200'), output
        print('PASS: guest DNS and certificate-verified public HTTPS', flush=True)
        output = request('import subprocess,sys\nsubprocess.run([sys.executable,"-m","pip","install","--no-deps","--only-binary=:all:","--timeout=6","--retries=0","six==1.17.0"],check=True)')
        assert 'Successfully installed six-1.17.0' in output, output
        assert request('import six\nprint(six.__version__)\nprint(six.__file__)').strip().endswith('/workspace/.python/six.py')
        print('PASS: pip installs to writable persistent workspace and Python imports package', flush=True)
        output = request('''import socket
for host,port in [("10.205.0.1",443),("172.30.1.71",443),("169.254.169.254",80),("10.204.0.2",8765)]:
 try:
  connection=socket.create_connection((host,port),timeout=1.5)
  connection.close()
  raise AssertionError("private destination reachable: "+host)
 except OSError:
  print("BLOCKED",host)
''')
        assert output.count('BLOCKED') == 4, output
        print('PASS: gateway, host LAN, metadata and other VM networks blocked', flush=True)
        assert request('listener=socket.socket();listener.bind(("0.0.0.0",8765));listener.listen()\nlocal=socket.create_connection(("127.0.0.1",8765),timeout=2);local.close()\nprint("LISTENING")') == 'LISTENING\n'
        slot = json.loads(Path('/run/agora-runtime-network/slots.json').read_text())[str(CANVAS)]
        wan_ip = '10.204.0.' + str(slot*4+2)
        command('ip', 'route', 'add', '10.205.0.2/32', 'via', wan_ip, 'dev', 'agv'+str(CANVAS))
        route_added = True
        try:
            connection = socket.create_connection(('10.205.0.2',8765),timeout=2)
        except OSError: pass
        else:
            connection.close(); raise AssertionError('Host initiated connection reached guest')
        command('ip','netns','add',ATTACKER)
        command('ip','link','add',ATTACK_LINK,'type','veth','peer','name','probe0','netns',ATTACKER)
        command('ip','addr','add','198.19.1.1/30','dev',ATTACK_LINK)
        command('ip','link','set',ATTACK_LINK,'up')
        command('ip','netns','exec',ATTACKER,'ip','addr','add','198.19.1.2/30','dev','probe0')
        command('ip','netns','exec',ATTACKER,'ip','link','set','probe0','up')
        command('ip','netns','exec',ATTACKER,'ip','route','add','default','via','198.19.1.1')
        before=int(command('iptables','-w','5','-nvx','-L','AGORA-RUNTIME').stdout.strip().splitlines()[-1].split()[0])
        probe = command('ip','netns','exec',ATTACKER,'python3','-c',
                        'import socket; socket.create_connection(("10.205.0.2",8765),timeout=2)',check=False)
        assert probe.returncode != 0 and 'TimeoutError' in probe.stderr, probe.stderr
        after=int(command('iptables','-w','5','-nvx','-L','AGORA-RUNTIME').stdout.strip().splitlines()[-1].split()[0])
        assert after > before, 'Probe did not reach the inbound DROP rule'
        print('PASS: unsolicited host and external-namespace connections to real guest listener blocked', flush=True)
        command('systemctl','stop',f'agora-runtime@{CANVAS}.service')
        output=request('import six\nprint(six.__version__)\nprint(six.__file__)')
        assert output == '1.17.0\n/workspace/.python/six.py\n',output
        print('PASS: installed package remains usable after VM restart', flush=True)
    finally:
        if route_added: command('ip','route','del','10.205.0.2/32',check=False)
        command('ip','link','del',ATTACK_LINK,check=False)
        command('ip','netns','del',ATTACKER,check=False)
        command('systemctl','stop',f'agora-runtime@{CANVAS}.service',check=False)
        command('systemctl','stop',f'agora-runtime-network@{CANVAS}.service',check=False)
        if DIRECTORY.exists(): shutil.rmtree(DIRECTORY)
        (Path('/var/lib/agora-runtime/launch')/f'{CANVAS}.env').unlink(missing_ok=True)
        command('systemctl','revert',f'agora-runtime@{CANVAS}.service',check=False)
        print('Removed dedicated test VM, namespace, interfaces and route',flush=True)


if __name__=='__main__':main()
