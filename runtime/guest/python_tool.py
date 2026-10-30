#!/usr/local/bin/python3.12 -I
"""Python/pip launcher: use the selected interpreter and its own package directory."""
import importlib.util,os,shlex,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('runtime_options',Path(__file__).resolve().with_name('runtime_options.py'));options=importlib.util.module_from_spec(spec);spec.loader.exec_module(options)
name=Path(sys.argv[0]).name
if name=='python_tool.py' and len(sys.argv)==3 and sys.argv[1]=='--environment':
 env=options.python_environment(sys.argv[2],os.environ)
 for key in ('PATH','PYTHONPATH','PIP_TARGET','LD_LIBRARY_PATH','AGORA_PYTHON_ENGINE'):print('export '+key+'='+shlex.quote(env[key]))
else:
 is_pip=name.startswith('pip');version=name[3:] if is_pip else name[6:]
 engine=os.environ.get('AGORA_PYTHON_ENGINE','py312') if version in ('','3') else 'py'+version.replace('.','')
 env=options.python_environment(engine,os.environ);binary=options.PYTHON[engine]
 os.execve(binary,[binary,*(['-m','pip'] if is_pip else []),*sys.argv[1:]],env)
