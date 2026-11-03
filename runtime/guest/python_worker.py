"""Unprivileged persistent Python kernel. The supervisor owns resource limits."""
import importlib
import json
import os
import socket
import sys
import traceback

control = socket.socket(fileno=int(sys.argv[1]))
namespace = {'__name__': '__main__', '__file__': '/workspace/blueprint.py'}
reader = control.makefile('rb')
original_stdin = sys.stdin
for line in reader:
    status = 0
    try:
        request = json.loads(line)
        source = '/proc/self/fd/' + os.environ['AGORA_STDIN_FD'] if request.get('stdinProvided') and 'AGORA_STDIN_FD' in os.environ else '/dev/null'
        source_fd = os.open(source, os.O_RDONLY)
        os.dup2(source_fd, 0)
        os.close(source_fd)
        sys.stdin = open(0, 'r', encoding='utf-8', errors='replace', closefd=False)
        importlib.invalidate_caches()
        exec(compile(request['code'], '/workspace/blueprint.py', 'exec'), namespace, namespace)
    except BaseException:
        traceback.print_exc()
        status = 1
    sys.stdout.flush()
    sys.stderr.flush()
    control.sendall(json.dumps({'exitCode': status}).encode() + b'\n')
