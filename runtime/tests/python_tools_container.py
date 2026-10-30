"""Run in the guest image as UID 1000; check actual Python/pip command routing."""
import importlib.util,subprocess
p='/usr/local/lib/agora-runner.py';s=importlib.util.spec_from_file_location('runner',p);runner=importlib.util.module_from_spec(s);s.loader.exec_module(runner)
for engine,version in [('py311','3.11'),('py312','3.12'),('py313','3.13'),('py314','3.14'),('py315','3.15')]:
 command,env=runner.worker_command('bash',{},'0',{'engine':engine})
 for name in ['python','python3']:
  r=subprocess.run([name,'--version'],env=env,capture_output=True,text=True,check=True);assert r.stdout.startswith('Python '+version+'.'),r.stdout
 for name in ['pip','pip3']:
  r=subprocess.run([name,'--version'],env=env,capture_output=True,text=True,check=True);assert '(python '+version+')' in r.stdout,r.stdout
 print('PASS '+version+': python/python3/pip/pip3 select matching interpreter',flush=True)
_,env=runner.worker_command('python',{'engine':'py315'},'0')
r=subprocess.run(['python3','-c','import subprocess;print(subprocess.check_output(["pip","--version"],text=True));print(subprocess.check_output(["python3.12","--version"],text=True))'],env=env,capture_output=True,text=True,check=True)
assert '(python 3.15)' in r.stdout and 'Python 3.12.' in r.stdout,r.stdout
print('PASS nested Python subprocess pip matches 3.15; explicit version command selects its own interpreter',flush=True)
