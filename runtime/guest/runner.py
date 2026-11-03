#!/usr/bin/env python3
"""Offline guest execution agent. The listener is reachable only via host VSOCK.

Each request is a bounded JSON line; responses are bounded JSON lines. Code runs
as persistent unprivileged language kernels in a bounded, idle-frozen cgroup.
"""
import codecs
import base64
import ctypes
import functools
import importlib.util
import json
import os
import resource
import selectors
import signal
import socket
import subprocess
import time
import traceback
from pathlib import Path

_spec = importlib.util.spec_from_file_location('runtime_options', Path(__file__).with_name('runtime_options.py'))
options = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(options)

MAX_CODE = 65536
MAX_OUTPUT = 65536
TIMEOUT = 15
UID = 1000
WORKSPACE = Path('/workspace')
CGROUP = Path('/sys/fs/cgroup/agora-job')
LANGUAGES = {'python': ('python3',), 'javascript': ('node',),
             'bash': ('bash',), 'java': ('java',), 'c': ('gcc', '-x', 'c'), 'cpp': ('g++', '-x', 'c++')}


def validate(request):
    if not isinstance(request, dict) or not {'language', 'code'} <= set(request) or set(request) - {'language', 'code', 'runtime', 'pythonRuntime', 'stdin', 'action'}:
        raise ValueError('Expected language and code')
    if request.get('action','execute') not in ('execute','build'): raise ValueError('Invalid action')
    language, code = request['language'], request['code']
    if language not in LANGUAGES or not isinstance(code, str) or len(code.encode('utf-8')) > MAX_CODE:
        raise ValueError('Unsupported language or code too large')
    if 'stdin' in request and (not isinstance(request['stdin'], str) or len(request['stdin'].encode()) > 65536):
        raise ValueError('Invalid stdin')
    options.normalize(language, request.get('runtime'))
    if 'pythonRuntime' in request:
        if language!='bash':raise ValueError('Python tool configuration is only allowed for Shell')
        options.normalize('python',request['pythonRuntime'])
    return language, code


def _restrict(group):
    os.setsid()
    # Move into the job cgroup before allowing any untrusted instructions.
    (group / 'cgroup.procs').write_text(str(os.getpid()))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    # Individual files may use the available VM disk space.
    resource.setrlimit(resource.RLIMIT_FSIZE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.setgroups([])
    os.setgid(UID)
    os.setuid(UID)
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
        os._exit(126)


# One kernel per language, shared by every participant in this canvas VM.
kernels = {}
kernel_configs = {}
kernel_inputs = {}


def stop_kernel(language):
    input_fd = kernel_inputs.pop(language, None)
    if input_fd is not None: os.close(input_fd)
    entry = kernels.pop(language, None)
    kernel_configs.pop(language, None)
    group = CGROUP / language
    if group.exists():
        (group / 'cgroup.freeze').write_text('0')
        (group / 'cgroup.kill').write_text('1')
    if entry:
        process, control = entry
        process.wait(timeout=5)
        process.stdout.close()
        control.close()
    if group.exists():
        for _ in range(100):
            try:
                group.rmdir(); break
            except OSError: time.sleep(.01)


def reset():
    for language in list(kernels): stop_kernel(language)
    if CGROUP.exists():
        (CGROUP / 'cgroup.kill').write_text('1')
        for _ in range(100):
            try:
                for group in CGROUP.iterdir():
                    if group.is_dir(): group.rmdir()
                CGROUP.rmdir(); break
            except OSError: time.sleep(.01)
    return {'reset': True}


def worker_command(language, config, fd, python_runtime=None):
    config = options.normalize(language, config)
    python = options.PYTHON.get(config['engine'], '/usr/local/bin/python3.12')
    package_version={'py311':'3.11','py313':'3.13','py314':'3.14','py315':'3.15'}.get(config['engine'])
    package_dir='/workspace/.python'+('/'+package_version if package_version else '')
    env = {'PATH': package_dir+'/bin:/usr/local/bin:/usr/bin:/bin', 'HOME': '/workspace',
           'LANG': 'C.UTF-8', 'TMPDIR': '/workspace', 'PYTHONPATH': package_dir,
           'PIP_TARGET': package_dir, 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    if language in ('python','bash'):
        selected=config if language=='python' else options.normalize('python',python_runtime)
        env=options.python_environment(selected['engine'],env)
    if config['executionMode']=='precompiled':
        command=['/usr/local/bin/python3.12','-u','/usr/local/lib/artifact_worker.py',fd,language,json.dumps(config)]
    elif language == 'python':
        command = [python, '-u']
        if config['optimize'] != '0': command += ['-O' if config['optimize']=='1' else '-OO']
        if not config['writeBytecode']: command += ['-B']
        if config['warnings'] != 'default': command += ['-W',config['warnings']]
        command += ['/usr/local/lib/python_worker.py',fd]
    elif language == 'javascript':
        command = [options.NODE[config['engine']]]
        if not config['warnings']: command += ['--no-warnings']
        command += ['/usr/local/lib/javascript_worker.cjs',fd,str(config['strict']).lower()]
    elif language == 'bash':
        command = ['bash','--noprofile','--norc']
        if config['errexit']: command += ['-e']
        if config['nounset']: command += ['-u']
        if config['pipefail']: command += ['-o','pipefail']
    elif language == 'java':
        command = [options.JDK[config['engine']]+'/bin/java', '-Xmx'+config['heapMiB']+'m',
                   '-XX:+UseSerialGC',f"-XX:ActiveProcessorCount={resource_limits()['vcpus']}",'-XX:MaxMetaspaceSize=160m',
                   '-XX:ReservedCodeCacheSize=64m','-Duser.home=/workspace','-Djava.io.tmpdir=/workspace',
                   '--add-modules','jdk.jshell','--add-opens=java.base/java.io=ALL-UNNAMED']
        if config['assertions']: command += ['-ea']
        if config['preview']: command += ['--enable-preview']
        command += ['-cp','/opt/agora/java','JavaWorker',fd,config['release'],str(config['preview']).lower()]
        if config['dependencies']:command += [options.java_dependency_path(config)]
    elif config['engine'] == 'gcc14':
        command = ['/usr/local/bin/python3.12','-u','/usr/local/lib/program_worker.py',fd,language,*options.native_flags(language,config)]
    else:
        binary = '/usr/local/bin/agora-native-worker' + ('-21' if config['engine']=='clang21' else '')
        command = [binary,fd,language,*options.native_flags(language,config)]
    return command, env


def resource_limits():
    # Only the host can set the boot command line; code objects cannot raise limits.
    profile=options.DEFAULT_RESOURCE_TEMPLATE
    for argument in Path('/proc/cmdline').read_text().split():
        if argument.startswith('agora.resources='): profile=argument.split('=',1)[1]
    return options.resource_template(profile)


def ensure_job_cgroup():
    if not CGROUP.exists():
        CGROUP.mkdir()
        limits=resource_limits()
        (CGROUP / 'memory.max').write_text(str(limits['workerMemoryMiB'] * 1024 * 1024))
        (CGROUP / 'pids.max').write_text(str(limits['pids']))
        (CGROUP / 'cpu.max').write_text(f"{limits['vcpus'] * 100000} 100000")


def kernel(language, config=None, python_runtime=None):
    config = options.normalize(language, config)
    if language in kernels and kernel_configs.get(language) == config:
        return kernels[language]
    if language in kernels: stop_kernel(language)
    ensure_job_cgroup()
    group = CGROUP / language
    group.mkdir()
    parent, child = socket.socketpair()
    fd = str(child.fileno())
    command, env = worker_command(language, config, fd, python_runtime)
    input_fd = os.memfd_create("agora-stdin", os.MFD_CLOEXEC)
    env["AGORA_STDIN_FD"] = str(input_fd)
    try:
        process = subprocess.Popen(command, cwd=WORKSPACE, stdin=child if language == 'bash' else input_fd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, pass_fds=(child.fileno(),input_fd),
            env=env, preexec_fn=functools.partial(_restrict, group))
    except BaseException:
        parent.close()
        os.close(input_fd)
        raise
    finally:
        child.close()
    kernel_inputs[language] = input_fd
    kernel_configs[language] = config
    kernels[language] = (process, parent)
    return process, parent


def install_java_dependencies(config):
    config=options.normalize('java',config)
    stop_kernel('java')
    ensure_job_cgroup()
    group=CGROUP/'java-install';group.mkdir()
    result={'installed':False,'output':'Java library installation failed.'}
    try:
        process=subprocess.Popen(['/usr/local/bin/python3','-I','/usr/local/lib/java_dependencies.py',json.dumps(config)],
            cwd=WORKSPACE,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env=worker_command('java',config,'0')[1],preexec_fn=functools.partial(_restrict,group))
        try:
            output,error=process.communicate(timeout=175)
            if len(output)<=MAX_OUTPUT and process.returncode==0:result=json.loads(output)
            else:result={'installed':False,'output':error[-12000:].decode('utf-8','replace') or 'Java library installation failed.'}
        except subprocess.TimeoutExpired:
            (group/'cgroup.kill').write_text('1');process.communicate()
            result={'installed':False,'output':'Java library installation timed out. Retry the installation.'}
    finally:
        (group/'cgroup.kill').write_text('1')
        for _ in range(100):
            try:group.rmdir();break
            except OSError:time.sleep(.01)
        os.sync()
    return result


def execute(request, on_output=None):
    if isinstance(request,dict) and set(request)=={'action','runtime'} and request['action']=='java_dependencies':
        return install_java_dependencies(request['runtime'])
    if request == {'action': 'reset'}:
        return reset()
    if isinstance(request,dict) and set(request)=={'action','language','runtime'} and request['action']=='configure':
        options.normalize(request['language'],request['runtime'])
        stop_kernel(request['language'])
        return {'configured': True}
    language, code = validate(request)
    config=options.normalize(language,request.get('runtime'))
    if language=='java' and config['dependencies'] and not Path(options.java_dependency_path(config)).is_file():
        return {'exitCode':1,'output':'실행 환경에서 Java 라이브러리를 먼저 설치하세요.','timedOut':False,'truncated':False,'environmentReset':False}
    if request.get('action')=='build' and config['executionMode']!='precompiled':
        return {'exitCode':0,'output':'기존 실행 모드: 사전 빌드 대상이 아닙니다.','timedOut':False,'truncated':False,'environmentReset':False}
    output, reply = bytearray(), bytearray()
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    def collect(data):
        available = MAX_OUTPUT - len(output)
        accepted = data[:available]
        output.extend(accepted)
        if on_output:
            text = decoder.decode(accepted)
            if text: on_output(text)
        return len(data) > available
    timed_out = truncated = False
    environment_reset = False
    status = 1
    selector = selectors.DefaultSelector()
    try:
        process, control = kernel(language, request.get('runtime'), request.get('pythonRuntime'))
        group = CGROUP / language
        input_fd = kernel_inputs[language]
        os.ftruncate(input_fd, 0)
        os.lseek(input_fd, 0, os.SEEK_SET)
        data = request.get('stdin', '').encode()
        if data: os.write(input_fd, data)
        os.lseek(input_fd, 0, os.SEEK_SET)
        (group / 'cgroup.freeze').write_text('0')
        os.set_blocking(process.stdout.fileno(), False)
        if config['executionMode']=='precompiled':
            payload=json.dumps({'code':code,'stdinProvided':'stdin' in request,'action':request.get('action','execute')},ensure_ascii=True).encode()+b'\n'
        elif language == 'bash':
            encoded = base64.b64encode(code.encode()).decode()
            child_fd = next(fd for fd in os.listdir(f'/proc/{process.pid}/fd')
                            if int(fd) > 2 and os.readlink(f'/proc/{process.pid}/fd/{fd}').startswith('socket:'))
            engine=options.normalize('python',request.get('pythonRuntime'))['engine']
            prelude=f'eval "$(/usr/local/bin/python3.12 -I /usr/local/lib/python_tool.py --environment {engine})"\n'
            input_source = f'/proc/self/fd/{input_fd}' if 'stdin' in request else '/dev/null'
            payload = (prelude+f"eval -- \"$(printf '%s' '{encoded}' | base64 -d)\" <{input_source}\nbuiltin printf '{{\"exitCode\":%d}}\\n' \"$?\" >&{child_fd}\n").encode()
        elif language == 'java':
            payload = base64.b64encode(code.encode()) + (b' 1' if 'stdin' in request else b' 0') + b'\n'
        else:
            payload = json.dumps({'code': code, 'stdinProvided': 'stdin' in request}, ensure_ascii=True).encode() + b'\n'
        control.sendall(payload)
        selector.register(process.stdout, selectors.EVENT_READ, 'output')
        selector.register(control, selectors.EVENT_READ, 'control')
        deadline = time.monotonic() + (60 if request.get('action')=='build' else TIMEOUT)
        while not reply.endswith(b'\n'):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(remaining, .2)):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    raise OSError('Kernel terminated')
                if key.data == 'control':
                    reply.extend(data)
                    if len(reply) > 1024:
                        raise OSError('Invalid kernel response')
                else:
                    if collect(data):
                        truncated = True
                        break
            if truncated:
                break
        if reply.endswith(b'\n') and not truncated and not timed_out:
            status = int(json.loads(reply)['exitCode'])
            # Freeze this kernel before collecting the last bytes: no asynchronous
            # output from this cell may leak into the next cell's result.
            (group / 'cgroup.freeze').write_text('1')
            while 'frozen 1' not in (group / 'cgroup.events').read_text():
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(.001)
            # Retain the interpreter, but terminate detached subprocesses before
            # another cell can run. Language threads remain frozen between cells.
            for pid in (group / 'cgroup.procs').read_text().split():
                if int(pid) != process.pid:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            while True:
                try:
                    data = os.read(process.stdout.fileno(), 8192)
                except BlockingIOError:
                    break
                if not data:
                    break
                if collect(data):
                    truncated = True
                    break
        else:
            environment_reset = True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        environment_reset = True
    finally:
        selector.close()
        if timed_out or truncated or environment_reset:
            reset()
            environment_reset = True
    if on_output:
        tail = decoder.decode(b'', final=True)
        if tail: on_output(tail)
    # The hypervisor may stop on idle or an operator restart. Commit guest file
    # writes (including pip installations) before acknowledging the completed cell.
    os.sync()
    return {'exitCode': status if not environment_reset else 1,
            'output': output.decode('utf-8', errors='replace'),
            'timedOut': timed_out, 'truncated': truncated, 'environmentReset': environment_reset}


def serve():
    listener = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    listener.bind((socket.VMADDR_CID_ANY, 5000))
    listener.listen(8)
    while True:
        conn, peer = listener.accept()
        with conn:
            conn.settimeout(20)
            try:
                if peer[0] != socket.VMADDR_CID_HOST:
                    continue
                data = bytearray()
                while not data.endswith(b'\n') and len(data) <= MAX_CODE * 12 + 2048:
                    part = conn.recv(4096)
                    if not part:
                        raise ValueError('Incomplete request')
                    data.extend(part)
                if len(data) > MAX_CODE * 12 + 2048:
                    raise ValueError('Request too large')
                request = json.loads(data)
                stream = request.pop('stream', False) if isinstance(request, dict) else False
                if type(stream) is not bool: raise ValueError('Invalid stream flag')
                disconnected = False
                def send_output(text):
                    nonlocal disconnected
                    if disconnected: return
                    try: conn.sendall(json.dumps({'type':'output','output':text}, ensure_ascii=True).encode()+b'\n')
                    except OSError: disconnected = True
                result = execute(request, on_output=send_output) if stream else execute(request)
            except (ValueError, TypeError, OSError, json.JSONDecodeError):
                traceback.print_exc()
                result = {'error': 'Execution unavailable or invalid request'}
            try:
                conn.sendall(json.dumps(result, ensure_ascii=True).encode() + b'\n')
            except OSError:
                pass


if __name__ == '__main__':
    serve()
