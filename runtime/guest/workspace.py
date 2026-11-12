"""Host-only workspace files, durable mutation receipts and Automerge text merging.

All filesystem work runs as UID 1000. Directory descriptors and O_NOFOLLOW keep
paths within /workspace, including when a shell replaces a path with a symlink.
The SQLite intent is fsynced before changing files and rolled forward on restart.
"""
import base64
import contextlib
import errno
import hashlib
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import uuid
from pathlib import Path

MAX_FILE = 131072
MAX_DOCUMENT = 262144
READS = {'stat', 'list', 'read'}
OPERATIONS = READS | {'write', 'mkdir', 'rename', 'delete'}


class WorkspaceError(Exception):
    pass


def parts(path):
    if not isinstance(path, str) or not path.startswith('/') or len(path.encode()) > 4096 or '\\' in path or '\x00' in path:
        raise WorkspaceError('PATH_INVALID')
    result = path[1:].split('/') if path != '/' else []
    if any(p in ('', '.', '..') or p.startswith('.agora-write-') or len(p.encode()) > 255 for p in result):
        raise WorkspaceError('PATH_INVALID')
    return result


def validate(request):
    if not isinstance(request, dict) or request.get('operation') not in OPERATIONS:
        raise WorkspaceError('WORKSPACE_REQUEST_INVALID')
    allowed = {'operation', 'path', 'requestId', 'document', 'content', 'fileId', 'etag', 'resourceId', 'create', 'overwrite', 'recursive', 'target', 'cursor'}
    if set(request) - allowed:
        raise WorkspaceError('WORKSPACE_REQUEST_INVALID')
    parts(request.get('path'))
    for key in ('create', 'overwrite', 'recursive'):
        if key in request and type(request[key]) is not bool:
            raise WorkspaceError('WORKSPACE_REQUEST_INVALID')
    for key, limit in (('document', MAX_DOCUMENT), ('content', 174764), ('fileId', 128), ('etag', 128), ('resourceId', 128)):
        if key in request and (not isinstance(request[key], str) or len(request[key]) > limit):
            raise WorkspaceError('WORKSPACE_REQUEST_INVALID')
    if request['operation'] == 'rename':
        parts(request.get('target'))
        if request['target'] == '/' or request['target'].startswith(request['path'] + '/'):
            raise WorkspaceError('PATH_INVALID')
    if request['operation'] not in READS:
        if request['path'] == '/' or not isinstance(request.get('requestId'), str) or not 1 <= len(request['requestId']) <= 192:
            raise WorkspaceError('WORKSPACE_REQUEST_INVALID')
    if 'cursor' in request and (type(request['cursor']) is not int or request['cursor'] < 0):
        raise WorkspaceError('WORKSPACE_REQUEST_INVALID')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def decode(value):
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise WorkspaceError('DOCUMENT_INVALID') from None
    if len(content) > MAX_FILE:
        raise WorkspaceError('FILE_TOO_LARGE')
    return content


class Workspace:
    def __init__(self, root='/workspace', state='/var/lib/agora-workspace', merger=None):
        self.root = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.state = Path(state)
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.merger = merger or Path(__file__).with_name('workspace-crdt.cjs')
        self.db = sqlite3.connect(self.state / 'files.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, id TEXT, document TEXT, hash TEXT, inode TEXT);
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, digest TEXT, result TEXT, seq INTEGER);
            CREATE TABLE IF NOT EXISTS intent(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT);
        ''')

    def close(self):
        self.db.close()
        os.close(self.root)

    @contextlib.contextmanager
    def parent(self, path):
        segments = parts(path)
        descriptor = os.dup(self.root)
        try:
            for segment in segments[:-1]:
                child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor, segments[-1] if segments else '.'
        finally:
            os.close(descriptor)

    def info(self, path):
        with self.parent(path) as (parent, name):
            value = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode)):
            raise WorkspaceError('FILE_TYPE_UNSUPPORTED')
        return {'type': 'directory' if stat.S_ISDIR(value.st_mode) else 'file', 'size': value.st_size,
                'mtime': value.st_mtime_ns // 1000000, 'ctime': value.st_ctime_ns // 1000000,
                'resourceId': f'{value.st_dev}:{value.st_ino}:{value.st_ctime_ns}'}

    def content(self, path):
        with self.parent(path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise WorkspaceError('FILE_TYPE_UNSUPPORTED')
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise WorkspaceError('FILE_TOO_LARGE')
        return data

    def merge(self, file_id, content, current=None, incoming=None):
        try:
            text = content.decode('utf-8')
            if '\x00' in text:
                return None, content
        except UnicodeDecodeError:
            return None, content
        request = {'fileId': file_id, 'text': text, 'current': current, 'incoming': incoming}
        process = subprocess.run(['/opt/node/22/bin/node' if Path('/opt/node/22/bin/node').exists() else 'node',
                                  '--max-old-space-size=96', str(self.merger)],
                                 input=json.dumps(request), capture_output=True, text=True, timeout=8, check=True)
        result = json.loads(process.stdout)
        if 'error' in result:
            raise WorkspaceError(result['error'])
        return result['document'], decode(result['content'])

    def read(self, path):
        before = self.info(path)
        content = self.content(path)
        info = self.info(path)
        if before['resourceId'] != info['resourceId']:
            raise WorkspaceError('FILE_CONFLICT')
        inode = info['resourceId'].rsplit(':', 1)[0]
        row = self.db.execute('SELECT * FROM files WHERE path=?', (path,)).fetchone()
        if row and row['inode'] != inode:
            # A deleted/recreated file is a new document, even if the path was reused.
            row = None
        file_id = row['id'] if row else str(uuid.uuid4())
        document = row['document'] if row else None
        if not row or row['hash'] != digest(content):
            document, _ = self.merge(file_id, content, document)
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)', (path, file_id, document, digest(content), inode))
        return {**info, 'path': path, 'fileId': file_id, 'document': document,
                'content': base64.b64encode(content).decode(), 'etag': digest(content)}

    def recover(self):
        row = self.db.execute('SELECT payload FROM intent WHERE id=1').fetchone()
        if row:
            self.finish(json.loads(row['payload']))

    def finish(self, intent):
        request, operation = intent['request'], intent['request']['operation']
        path = request['path']
        if operation == 'write':
            content = decode(intent['content'])
            try:
                actual = digest(self.content(path))
            except FileNotFoundError:
                actual = None
            if actual != intent['result']['etag'] and actual != intent.get('expected'):
                return self.abort(intent, 'FILE_CONFLICT')
            with self.parent(path) as (parent, name):
                temporary = intent['temporary']
                try:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                except FileExistsError:
                    os.unlink(temporary, dir_fd=parent)
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fchmod(stream.fileno(), intent['mode'])
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
        elif operation == 'mkdir':
            with self.parent(path) as (parent, name):
                try:
                    os.mkdir(name, 0o755, dir_fd=parent)
                except FileExistsError:
                    if self.info(path)['type'] != 'directory':
                        raise WorkspaceError('FILE_EXISTS')
                os.fsync(parent)
        elif operation == 'rename':
            with self.parent(path) as (source, name), self.parent(request['target']) as (target, destination):
                try:
                    os.stat(name, dir_fd=source, follow_symlinks=False)
                except FileNotFoundError:
                    if self.info(request['target'])['resourceId'].rsplit(':', 1)[0] != intent['sourceInode']:
                        return self.abort(intent, 'FILE_CONFLICT')
                else:
                    if self.info(path)['resourceId'] != request['resourceId']:
                        return self.abort(intent, 'FILE_CONFLICT')
                    # Unlike rename(), RENAME_NOREPLACE cannot replace a concurrently created target.
                    import ctypes
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.renameat2(source, os.fsencode(name), target, os.fsencode(destination), 1) != 0:
                        code = ctypes.get_errno()
                        if code == errno.EEXIST:
                            return self.abort(intent, 'FILE_EXISTS')
                        raise OSError(code, os.strerror(code))
                os.fsync(source)
                os.fsync(target)
        elif operation == 'delete':
            trash = self.state / ('trash-' + digest(request['requestId'].encode()))
            with self.parent(path) as (parent, name):
                try:
                    if self.info(path)['resourceId'] != request['resourceId']:
                        return self.abort(intent, 'FILE_CONFLICT')
                    os.rename(name, trash, src_dir_fd=parent)
                except FileNotFoundError:
                    pass
                os.fsync(parent)
        result = intent['result']
        with self.db:
            if operation == 'write':
                self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)',
                                (path, result['fileId'], intent['document'], result['etag'], self.info(path)['resourceId'].rsplit(':', 1)[0]))
            if operation in ('rename', 'delete'):
                rows = self.db.execute('SELECT * FROM files').fetchall()
                for row in rows:
                    if row['path'] == path or row['path'].startswith(path + '/'):
                        self.db.execute('DELETE FROM files WHERE path=?', (row['path'],))
                        if operation == 'rename':
                            destination = request['target'] + row['path'][len(path):]
                            self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)',
                                            (destination, row['id'], row['document'], row['hash'], row['inode']))
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM receipts').fetchone()[0]
            self.db.execute('INSERT INTO receipts VALUES(?,?,?,?)', (request['requestId'], intent['digest'], json.dumps(result), seq))
            self.db.execute('DELETE FROM receipts WHERE seq<=?', (seq - 2048,))
            self.db.execute('DELETE FROM intent WHERE id=1')
        if operation == 'delete':
            import shutil
            if trash.is_dir() and not trash.is_symlink():
                shutil.rmtree(trash)
            else:
                trash.unlink(missing_ok=True)
        return result

    def abort(self, intent, code):
        result = {'error': code}
        with self.db:
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM receipts').fetchone()[0]
            self.db.execute('INSERT INTO receipts VALUES(?,?,?,?)',
                            (intent['request']['requestId'], intent['digest'], json.dumps(result), seq))
            self.db.execute('DELETE FROM receipts WHERE seq<=?', (seq - 2048,))
            self.db.execute('DELETE FROM intent WHERE id=1')
        return result

    def handle(self, request):
        validate(request)
        self.recover()
        operation, path = request['operation'], request['path']
        if operation == 'stat':
            return self.info(path)
        if operation == 'list':
            with self.parent(path) as (parent, name):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    # Bound traversal too, rather than allocating an unbounded directory listing.
                    with os.scandir(fd) as scan:
                        entries = []
                        scanned = 0
                        for entry in scan:
                            scanned += 1
                            if scanned > 10000:
                                raise WorkspaceError('DIRECTORY_TOO_LARGE')
                            if entry.name.startswith('.agora-write-'):
                                continue
                            if entry.is_file(follow_symlinks=False) or entry.is_dir(follow_symlinks=False):
                                entries.append([entry.name, 'directory' if entry.is_dir(follow_symlinks=False) else 'file'])
                finally:
                    os.close(fd)
            entries.sort()
            cursor = request.get('cursor', 0)
            page = entries[cursor:cursor + 256]
            return {'entries': page, 'cursor': cursor + 256 if cursor + 256 < len(entries) else None}
        if operation == 'read':
            return self.read(path)
        request_hash = digest(json.dumps(request, sort_keys=True).encode())
        receipt = self.db.execute('SELECT * FROM receipts WHERE id=?', (request['requestId'],)).fetchone()
        if receipt:
            if receipt['digest'] != request_hash:
                raise WorkspaceError('REQUEST_MISMATCH')
            return {**json.loads(receipt['result']), 'duplicate': True}
        result = {'path': path, 'operation': operation}
        intent = {'request': request, 'digest': request_hash, 'result': result}
        if operation == 'write':
            try:
                current = self.read(path)
            except FileNotFoundError:
                if not request.get('create'):
                    raise
                current = None
            if current and not request.get('overwrite', True):
                raise WorkspaceError('FILE_EXISTS')
            if current and request.get('fileId') != current['fileId']:
                raise WorkspaceError('FILE_REPLACED')
            file_id = current['fileId'] if current else str(uuid.uuid4())
            if request.get('document'):
                if not current or not current['document']:
                    raise WorkspaceError('FILE_REPLACED')
                document, content = self.merge(file_id, decode(current['content']), current['document'], request['document'])
                if document is None:
                    raise WorkspaceError('DOCUMENT_INVALID')
            else:
                if current and request.get('etag') != current['etag']:
                    raise WorkspaceError('FILE_CONFLICT')
                content = decode(request.get('content', ''))
                document, content = self.merge(file_id, content)
            result.update({'fileId': file_id, 'etag': digest(content), 'created': current is None})
            intent.update({'document': document, 'content': base64.b64encode(content).decode(), 'mode': 0o644,
                           'expected': current['etag'] if current else None, 'temporary': '.agora-write-' + uuid.uuid4().hex})
            if current:
                with self.parent(path) as (parent, name):
                    intent['mode'] = stat.S_IMODE(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode) & 0o777
        elif operation == 'mkdir':
            try:
                self.info(path)
            except FileNotFoundError:
                pass
            else:
                raise WorkspaceError('FILE_EXISTS')
        else:
            source = self.info(path)
            if source['resourceId'] != request.get('resourceId'):
                raise WorkspaceError('FILE_CONFLICT')
            intent['sourceInode'] = source['resourceId'].rsplit(':', 1)[0]
            if operation == 'rename':
                try:
                    self.info(request['target'])
                except FileNotFoundError:
                    pass
                else:
                    raise WorkspaceError('FILE_EXISTS')
                result['target'] = request['target']
            elif not request.get('recursive') and self.info(path)['type'] == 'directory':
                with self.parent(path) as (parent, name):
                    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        if os.listdir(fd):
                            raise WorkspaceError('DIRECTORY_NOT_EMPTY')
                    finally:
                        os.close(fd)
        with self.db:
            self.db.execute('INSERT INTO intent VALUES(1,?)', (json.dumps(intent),))
        return self.finish(intent)


def child():
    request = json.loads(sys.stdin.buffer.read(800001))
    workspace = Workspace()
    try:
        try:
            result = workspace.handle(request)
        except WorkspaceError as error:
            result = {'error': str(error)}
        except FileNotFoundError:
            result = {'error': 'FILE_NOT_FOUND'}
        except FileExistsError:
            result = {'error': 'FILE_EXISTS'}
        except (NotADirectoryError, IsADirectoryError):
            result = {'error': 'FILE_TYPE_UNSUPPORTED'}
        except OSError as error:
            result = {'error': 'PATH_INVALID' if error.errno in (errno.ELOOP, errno.ENOTDIR) else 'WORKSPACE_IO_ERROR'}
        encoded = json.dumps(result, ensure_ascii=True)
        print(encoded if len(encoded) < 400000 else '{"error":"WORKSPACE_RESPONSE_TOO_LARGE"}')
    finally:
        workspace.close()


def serve():
    state = Path('/var/lib/agora-workspace')
    state.mkdir(mode=0o700, exist_ok=True)
    os.chown(state, 1000, 1000)
    listener = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    listener.bind((socket.VMADDR_CID_ANY, 5003))
    listener.listen(8)
    while True:
        connection, peer = listener.accept()
        with connection:
            if peer[0] != socket.VMADDR_CID_HOST:
                continue
            connection.settimeout(15)
            try:
                with connection.makefile('rb') as stream:
                    data = stream.readline(800001)
                if not data.endswith(b'\n') or len(data) > 800000:
                    raise ValueError('Invalid frame')
                process = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--child'], input=data,
                                         capture_output=True, timeout=12, check=True, user=1000, group=1000, extra_groups=[])
                response = process.stdout
                if not response.endswith(b'\n') or len(response) > 400000:
                    raise ValueError('Invalid response')
            except (OSError, ValueError, subprocess.SubprocessError):
                response = b'{"error":"WORKSPACE_UNAVAILABLE"}\n'
            try:
                connection.sendall(response)
            except OSError:
                # The durable receipt lets a disconnected caller retry on a new socket.
                pass


if __name__ == '__main__':
    child()
