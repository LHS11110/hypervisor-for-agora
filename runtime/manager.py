#!/usr/bin/env python3
"""Root-owned local VM manager; only the backend may access its Unix socket."""
import json
import importlib.util
import os
import pwd
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path

_spec=importlib.util.spec_from_file_location('runtime_options',Path(__file__).with_name('runtime_options.py'))
options=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(options)

STATE = Path('/var/lib/agora-runtime')
CANVASES = STATE / 'canvases'
SOCKET = Path('/run/agora-runtime/manager.sock')
MAX_REQUEST = 800000
MAX_RESPONSE = 400000
MAX_ACTIVE = 4
MAX_VM_MEMORY_MIB = 8192
MAX_VM_CPUS = 8
MAX_DISKS = 32
DISK_RESERVE = 8 * 1024**3
IDLE_SECONDS = 900
guard = threading.Lock()
locks = {}
last_used = {}
slots = threading.BoundedSemaphore(16)


class Unavailable(Exception):
    pass


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, timeout=30)


def active_units():
    result = command('systemctl', 'list-units', '--type=service', '--state=active',
                     '--plain', '--no-legend', 'agora-runtime@*.service')
    return [line.split()[0] for line in result.stdout.decode().splitlines() if line.split()]


def running(unit):
    return subprocess.run(['systemctl', 'is-active', '--quiet', unit], timeout=5).returncode == 0


def resource_capacity(canvas_id, template):
    used_memory=used_cpus=0
    for unit in active_units():
        other=int(unit.split('@',1)[1].split('.',1)[0])
        if other==canvas_id: continue
        current=options.resource_template(settings(other)['resourceTemplate'])
        used_memory+=current['memoryMiB'];used_cpus+=current['vcpus']
    if used_memory+template['memoryMiB']>MAX_VM_MEMORY_MIB or used_cpus+template['vcpus']>MAX_VM_CPUS:
        raise Unavailable('Runtime resource template capacity reached')
    available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))//1024
    if available<template['memoryMiB']+512+2048:
        raise Unavailable('Insufficient available host memory')


def configure_launch(canvas_id, key):
    template=options.resource_template(key)
    directory=STATE/'launch';directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/f'{canvas_id}.env'
    temporary=directory/f'{canvas_id}.next'
    temporary.write_text(f"AGORA_VM_CPUS=boot={template['vcpus']}\nAGORA_VM_MEMORY=size={template['memoryMiB']}M\nAGORA_VM_TEMPLATE={key}\n")
    temporary.chmod(0o600);temporary.replace(path)
    command('systemctl','set-property','--runtime',f'agora-runtime@{canvas_id}.service',
            f"MemoryMax={template['memoryMiB']+512}M",f"CPUQuota={template['vcpus']*100}%")


def save_settings(canvas_id,current):
    directory=STATE/'settings';directory.mkdir(mode=0o700,exist_ok=True)
    temporary=directory/f'{canvas_id}.next'
    temporary.write_text(json.dumps({key:current[key] for key in ('revision','languages','resourceTemplate','diskGiB')}))
    temporary.chmod(0o600)
    with temporary.open('r+b') as stream: os.fsync(stream.fileno())
    temporary.replace(directory/f'{canvas_id}.json')
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def disk_capacity(size):
    # Growth builds a complete replacement; reserve the full target, not just its delta.
    if shutil.disk_usage(CANVASES).free < size + DISK_RESERVE:
        raise ValueError('디스크를 확장할 서버 여유 공간이 부족합니다. 기존 디스크는 유지됩니다.')


def replace_disk(source, target, size):
    temporary=target.with_name('guest.growing')
    temporary.unlink(missing_ok=True)
    disk_capacity(size)
    try:
        command('cp','--reflink=auto','--sparse=always',str(source),str(temporary))
        with temporary.open('r+b') as stream:
            os.posix_fallocate(stream.fileno(),0,size)
        checked=subprocess.run(['e2fsck','-pf',str(temporary)],capture_output=True,timeout=30)
        if checked.returncode not in (0,1): raise Unavailable('Guest filesystem check failed')
        command('resize2fs',str(temporary))
        account=pwd.getpwnam('agora-vmm')
        os.chown(temporary,account.pw_uid,account.pw_gid);temporary.chmod(0o600)
        with temporary.open('r+b') as stream: os.fsync(stream.fileno())
        temporary.replace(target)
        descriptor=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(canvas_id):
    directory = CANVASES / str(canvas_id)
    unit = f'agora-runtime@{canvas_id}.service'
    with guard:
        if not (directory/'guest.raw').exists():
            if not directory.exists() and len(list(CANVASES.iterdir())) >= MAX_DISKS:
                raise Unavailable('Runtime storage limit reached')
            (directory/'guest.growing').unlink(missing_ok=True)
            size=settings(canvas_id)['diskGiB']*1024**3
            disk_capacity(size)
            directory.mkdir(mode=0o700,exist_ok=True)
            try:
                replace_disk(STATE/'base.raw',directory/'guest.raw',size)
                account=pwd.getpwnam('agora-vmm')
                os.chown(directory,account.pw_uid,account.pw_gid)
            except Exception:
                (directory/'guest.raw').unlink(missing_ok=True)
                try:directory.rmdir()
                except OSError:pass
                raise
        if not running(unit):
            (directory/'guest.growing').unlink(missing_ok=True)
            if len(active_units()) >= MAX_ACTIVE:
                raise Unavailable('Runtime capacity reached')
            key=settings(canvas_id)['resourceTemplate']
            resource_capacity(canvas_id,options.resource_template(key))
            configure_launch(canvas_id,key)
            for name in ('vsock', 'api.sock', 'api.sock.lock'):
                (directory / name).unlink(missing_ok=True)
            command('systemctl', 'start', unit)
        last_used[canvas_id] = time.monotonic()
    return directory / 'vsock'


def exchange(path, request, response_timeout=23):
    deadline = time.monotonic() + 25
    while True:
        sent = False
        try:
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.settimeout(response_timeout)
            conn.connect(str(path))
            conn.sendall(b'CONNECT 5000\n')
            with conn.makefile('rb') as reader:
                reply = reader.readline(1024)
                if not reply.startswith(b'OK '):
                    raise OSError('Guest not ready')
                sent = True
                conn.sendall(json.dumps(request, ensure_ascii=True).encode() + b'\n')
                response = reader.readline(MAX_RESPONSE + 1)
                if not response.endswith(b'\n') or len(response) > MAX_RESPONSE:
                    raise Unavailable('Invalid guest response')
                return json.loads(response)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as error:
            if sent:
                raise Unavailable('Guest connection lost during execution') from error
            if time.monotonic() >= deadline:
                raise Unavailable('Guest did not become ready') from error
            time.sleep(.2)
        finally:
            conn.close()


def settings(canvas_id):
    path=STATE/'settings'/f'{canvas_id}.json'
    stored=json.loads(path.read_text()) if path.exists() else {'revision':0,'languages':{}}
    languages={language:options.normalize(language,stored['languages'].get(language)) for language in options.CATALOG}
    key=stored.get('resourceTemplate',options.DEFAULT_RESOURCE_TEMPLATE)
    options.resource_template(key)
    return {'revision':stored['revision'],'languages':languages,'catalog':options.catalogue(),
            'resourceTemplate':key,'resourceTemplates':options.RESOURCE_TEMPLATES,
            'diskGiB':max(options.disk_size(stored.get('diskGiB',options.DEFAULT_DISK_GIB)),
                          ((CANVASES/str(canvas_id)/'guest.raw').stat().st_size//1024**3 if (CANVASES/str(canvas_id)/'guest.raw').exists() else 0)),
            'diskSizesGiB':options.DISK_SIZES_GIB}


def fsync_directory(path):
    if not path.exists(): return
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def delete_runtime(canvas_id):
    # The backend calls this only for a committed canvas deletion. A durable marker
    # prevents an already-authorized, delayed code request from recreating its VM.
    deleted=STATE/'deleted';deleted.mkdir(mode=0o700,exist_ok=True)
    marker=deleted/str(canvas_id)
    with marker.open('a') as stream:os.fsync(stream.fileno())
    fsync_directory(deleted)
    unit=f'agora-runtime@{canvas_id}.service'
    command('systemctl','stop',unit)
    command('systemctl','stop',f'agora-runtime-network@{canvas_id}.service')
    if running(unit): raise Unavailable('Runtime did not stop')
    directory=CANVASES/str(canvas_id)
    if directory.exists():shutil.rmtree(directory)
    for folder,suffixes in [('settings',['.json','.next']),('launch',['.env','.next'])]:
        for suffix in suffixes:(STATE/folder/f'{canvas_id}{suffix}').unlink(missing_ok=True)
        fsync_directory(STATE/folder)
    for backup in STATE.glob('backup-*'):
        if backup.is_dir() and not backup.is_symlink():
            (backup/f'{canvas_id}.raw').unlink(missing_ok=True)
            fsync_directory(backup)
    command('systemctl','revert',unit)
    fsync_directory(CANVASES)
    last_used.pop(canvas_id,None)
    # Tombstone fences delayed holders of the old lock before it is reclaimed.
    locks.pop(canvas_id,None)
    return {'deleted':True}


def execute(request):
    if not isinstance(request,dict): raise ValueError('Invalid request')
    canvas_id=request.get('canvasId')
    if type(canvas_id) is not int or not 0<canvas_id<=2147483647: raise ValueError('Invalid canvas')
    action=request.get('action','execute')
    shapes={'execute':{'canvasId','language','code'},'reset':{'canvasId','action'},'delete':{'canvasId','action'},'java_dependencies':{'canvasId','action'},
            'resources_set':{'canvasId','action','template','revision'},
            'disk_set':{'canvasId','action','sizeGiB','revision'},
            'settings_get':{'canvasId','action'},'settings_set':{'canvasId','action','language','config','revision'}}
    if action not in shapes or set(request) - ({'stdin'} if action=='execute' else set()) != shapes[action]: raise ValueError('Invalid request')
    if action=='execute':
        if 'stdin' in request and (not isinstance(request['stdin'],str) or len(request['stdin'].encode())>65536): raise ValueError('Invalid stdin')
        language,code=request['language'],request['code']
        if language not in options.CATALOG or not isinstance(code,str) or len(code.encode())>65536: raise ValueError('Invalid code')
    elif action=='settings_set':
        language=request['language']
        config=options.normalize(language,request['config'])
        if type(request['revision']) is not int or request['revision']<0: raise ValueError('Invalid revision')
    if action=='resources_set':
        options.resource_template(request['template'])
        if type(request['revision']) is not int or request['revision']<0: raise ValueError('Invalid revision')
    if action=='disk_set':
        options.disk_size(request['sizeGiB'])
        if type(request['revision']) is not int or request['revision']<0: raise ValueError('Invalid revision')
    if action!='delete' and (STATE/'deleted'/str(canvas_id)).exists(): raise ValueError('삭제된 캔버스의 실행 환경입니다.')
    if action=='settings_get': return settings(canvas_id)
    with guard: lock=locks.setdefault(canvas_id,threading.Lock())
    if not lock.acquire(blocking=False): raise Unavailable('A program is already running in this canvas')
    try:
        if action=='delete':
            with guard:return delete_runtime(canvas_id)
        if (STATE/'deleted'/str(canvas_id)).exists(): raise ValueError('삭제된 캔버스의 실행 환경입니다.')
        if action=='settings_get': return settings(canvas_id)
        if action=='disk_set':
            with guard:
                current=settings(canvas_id)
                if current['revision']!=request['revision']: raise ValueError('다른 사용자가 실행 환경을 변경했습니다. 다시 불러온 뒤 적용하세요.')
                if request['sizeGiB']<current['diskGiB']: raise ValueError('기존 파일을 보호하기 위해 디스크 축소는 지원하지 않습니다.')
                if request['sizeGiB']==current['diskGiB']: return current
                disk=CANVASES/str(canvas_id)/'guest.raw'
                if disk.exists():
                    disk.with_name('guest.growing').unlink(missing_ok=True)
                    disk_capacity(request['sizeGiB']*1024**3)
                    command('systemctl','stop',f'agora-runtime@{canvas_id}.service')
                    # Resize a private copy and atomically swap only after a successful fs check.
                    replace_disk(disk,disk,request['sizeGiB']*1024**3)
                current['diskGiB']=request['sizeGiB'];current['revision']+=1
                save_settings(canvas_id,current);last_used.pop(canvas_id,None)
                return current
        if action=='resources_set':
            with guard:
                current=settings(canvas_id)
                if current['revision']!=request['revision']: raise ValueError('다른 사용자가 실행 환경을 변경했습니다. 다시 불러온 뒤 적용하세요.')
                if current['resourceTemplate']==request['template']: return current
                # Stop before changing the durable boot template. Files have been synced by the guest.
                if running(f'agora-runtime@{canvas_id}.service'):
                    resource_capacity(canvas_id,options.resource_template(request['template']))
                    command('systemctl','stop',f'agora-runtime@{canvas_id}.service')
                current['resourceTemplate']=request['template'];current['revision']+=1
                save_settings(canvas_id,current)
                last_used.pop(canvas_id,None)
                return current
        if action=='settings_set':
            current=settings(canvas_id)
            if current['revision']!=request['revision']: raise ValueError('다른 사용자가 실행 환경을 변경했습니다. 다시 불러온 뒤 적용하세요.')
            if current['languages'][language]==config: return current
            if running(f'agora-runtime@{canvas_id}.service'):
                result=exchange(CANVASES/str(canvas_id)/'vsock',{'action':'configure','language':language,'runtime':config})
                if not result.get('configured'): raise Unavailable('Guest configuration failed')
            current['languages'][language]=config
            current['revision']+=1
            save_settings(canvas_id,current)
            return current
        if action=='java_dependencies':
            result=exchange(prepare(canvas_id),{'action':'java_dependencies','runtime':settings(canvas_id)['languages']['java']},response_timeout=185)
            with guard:last_used[canvas_id]=time.monotonic()
            return result
        if action=='reset' and not running(f'agora-runtime@{canvas_id}.service'): return {'reset':True}
        payload={'action':'reset'} if action=='reset' else {'language':language,'code':code,'runtime':settings(canvas_id)['languages'][language]}
        if action=='execute' and 'stdin' in request:payload['stdin']=request['stdin']
        if action=='execute' and language=='bash':payload['pythonRuntime']=settings(canvas_id)['languages']['python']
        result=exchange(prepare(canvas_id),payload)
        with guard: last_used[canvas_id]=time.monotonic()
        return result
    finally: lock.release()


def reap():
    while True:
        time.sleep(30)
        with guard:
            expired = [canvas for canvas, last in last_used.items()
                       if time.monotonic() - last >= IDLE_SECONDS]
            for canvas in expired:
                lock = locks[canvas]
                if not lock.acquire(blocking=False):
                    continue
                try:
                    command('systemctl', 'stop', f'agora-runtime@{canvas}.service')
                    del last_used[canvas]
                except (OSError, subprocess.SubprocessError):
                    pass
                finally:
                    lock.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            data = self.rfile.readline(MAX_REQUEST + 1)
            if len(data) > MAX_REQUEST or not data.endswith(b'\n'):
                raise ValueError('Invalid request')
            result = execute(json.loads(data))
        except (ValueError, TypeError) as error:
            result = {'error':'RUNTIME_CONFIG_INVALID','message':str(error)[:240]}
        except (OSError, subprocess.SubprocessError, Unavailable):
            result = {'error':'RUNTIME_UNAVAILABLE'}
        try:
            self.wfile.write(json.dumps(result, ensure_ascii=True).encode() + b'\n')
        except OSError:
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def process_request(self, request, address):
        if not slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            slots.release()


def main():
    os.umask(0o077)
    CANVASES.mkdir(mode=0o700, parents=True, exist_ok=True)
    SOCKET.parent.mkdir(mode=0o710, parents=True, exist_ok=True)
    os.chown(SOCKET.parent, 0, 10001)
    os.chmod(SOCKET.parent, 0o710)
    SOCKET.unlink(missing_ok=True)
    for unit in active_units():
        instance = unit.removeprefix('agora-runtime@').removesuffix('.service')
        if instance.isdecimal():
            canvas_id = int(instance)
            locks[canvas_id] = threading.Lock()
            last_used[canvas_id] = time.monotonic()
    with Server(str(SOCKET), Handler) as server:
        os.chown(SOCKET, 0, 10001)
        os.chmod(SOCKET, 0o660)
        threading.Thread(target=reap, daemon=True).start()
        server.serve_forever()


if __name__ == '__main__':
    main()
