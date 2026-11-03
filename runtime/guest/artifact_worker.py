"""Unprivileged explicit build/run worker. Never compiles on an execute request."""
import hashlib,json,os,re,socket,subprocess,sys,traceback
from pathlib import Path
import runtime_options as options

VERSION=1

def artifact_path(language,config,code):
    key=hashlib.sha256(json.dumps([VERSION,language,config,code],sort_keys=True).encode()).hexdigest()
    return Path('/workspace/.compiled-cache')/key

def commands(language,config,directory,code):
    d=str(directory)
    if language in ('c','cpp'):
        compiler=('/usr/bin/g++-14' if language=='cpp' else '/usr/bin/gcc-14') if config['engine']=='gcc14' else '/usr/bin/clang'+('++' if language=='cpp' else '')+'-'+config['engine'][5:]
        source=directory/('source.cpp' if language=='cpp' else 'source.c')
        return source,[compiler,*options.native_flags(language,config),str(source),'-o',d+'/program'],[d+'/program']
    if language=='python':
        source=directory/'source.py';binary=options.PYTHON[config['engine']]
        flags=(['-O'] if config['optimize']=='1' else ['-OO'] if config['optimize']=='2' else [])+(['-W',config['warnings']] if config['warnings']!='default' else [])
        compile_code='import py_compile,sys;py_compile.compile(sys.argv[1],cfile=sys.argv[2],doraise=True)'
        return source,[binary,*flags,'-c',compile_code,str(source),d+'/program.pyc'],[binary,'-u',*flags,d+'/program.pyc']
    if language=='javascript':
        source=directory/'source.js';node=options.NODE[config['engine']]
        base=[node,*([] if config['warnings'] else ['--no-warnings']),'/usr/local/lib/cached_javascript.cjs']
        return source,[*base,'build',d,str(config['strict']).lower()],[*base,'run',d,str(config['strict']).lower()]
    if language=='java':
        # Match top-level main class using javac's source-file convention.
        match=re.search(r'\bpublic\s+(?:(?:final|abstract)\s+)?(?:class|record|enum)\s+(\w+)',code) or re.search(r'\bclass\s+(\w+)',code)
        if not match:raise ValueError('사전 빌드 Java 코드는 main 메서드가 있는 클래스가 필요합니다.')
        name=match.group(1);package=re.search(r'^\s*package\s+([\w.]+)\s*;',code,re.M)
        main=(package.group(1)+'.' if package else '')+name
        cp=d
        if config['dependencies']:cp+=':'+Path(options.java_dependency_path(config)).read_text().strip()
        jdk=options.JDK[config['engine']]+'/bin/'
        preview=['--enable-preview'] if config['preview'] else []
        return directory/(name+'.java'),[jdk+'javac','-J-Xmx256m',*preview,'--release',config['release'],'-cp',cp,'-d',d,d+'/'+name+'.java'],[jdk+'java','-Xmx'+config['heapMiB']+'m','-XX:+UseSerialGC',*preview,*(['-ea'] if config['assertions'] else []),'-cp',cp,main]
    source=directory/'source.sh'
    flags=(['-e'] if config['errexit'] else [])+(['-u'] if config['nounset'] else [])+(['-o','pipefail'] if config['pipefail'] else [])
    return source,['/bin/bash','--noprofile','--norc','-n',str(source)],['/bin/bash','--noprofile','--norc',*flags,str(source)]

def handle(language,config,request):
    code=request['code'];directory=artifact_path(language,config,code)
    source,build,run=commands(language,config,directory,code)
    marker=directory/'built.json'
    if request.get('action')=='build':
        if marker.exists():print('빌드 결과물을 재사용합니다.',flush=True);return 0
        directory.mkdir(parents=True,exist_ok=True)
        source.write_text(code)
        result=subprocess.run(build,stdin=subprocess.DEVNULL)
        if result.returncode:return result.returncode
        # Commit only successful builds. Failed builds never become runnable.
        marker.write_text(json.dumps({'version':VERSION}))
        print('사전 빌드 완료' if language!='bash' else 'Shell 구문 검사 완료',flush=True)
        return 0
    if not marker.is_file():
        print('이 코드와 실행 옵션으로 먼저 사전 빌드를 수행하세요.',flush=True);return 1
    input_path='/proc/self/fd/'+os.environ['AGORA_STDIN_FD'] if request.get('stdinProvided') else '/dev/null'
    with open(input_path,'rb') as stream:return subprocess.run(run,stdin=stream).returncode

if __name__=='__main__':
    control=socket.socket(fileno=int(sys.argv[1]));language=sys.argv[2];config=options.normalize(language,json.loads(sys.argv[3]))
    for line in control.makefile('rb'):
        try:status=handle(language,config,json.loads(line))
        except Exception:traceback.print_exc();status=1
        sys.stdout.flush();sys.stderr.flush();control.sendall(json.dumps({'exitCode':status}).encode()+b'\n')
