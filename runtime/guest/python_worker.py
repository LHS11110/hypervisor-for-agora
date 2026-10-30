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
for line in reader:
    status = 0
    try:
        importlib.invalidate_caches()
        exec(compile(json.loads(line)['code'], '/workspace/blueprint.py', 'exec'), namespace, namespace)
    except BaseException:
        traceback.print_exc()
        status = 1
    sys.stdout.flush()
    sys.stderr.flush()
    control.sendall(json.dumps({'exitCode': status}).encode() + b'\n')
