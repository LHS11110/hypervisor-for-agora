#!/usr/bin/env python3
"""Local state validation and privilege confinement for the VMM launcher."""
import ctypes
import os
from pathlib import Path
import resource
import stat
import sys


def check_state(path, initialize=False):
    if not path.is_absolute() or any(c in str(path) for c in ',\n\r'):
        raise ValueError('AGORA_STATE_DIR must be absolute and contain no comma/newline')
    # Do not silently follow a symlink in any component.
    for component in [*reversed(path.parents), path]:
        if component.is_symlink():
            raise ValueError(f'Symlink in state path: {component}')
    if initialize:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid():
        raise ValueError('State directory must be owned by the current user')
    if initialize:
        path.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError('State directory must have mode 0700; run setup as its owner')
    for p in path.rglob('*'):
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != os.geteuid():
            raise ValueError(f'Unsafe state entry (symlink/owner): {p}')
        if not (stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode) or stat.S_ISSOCK(s.st_mode)):
            raise ValueError(f'Unexpected state entry type: {p}')
        if stat.S_ISREG(s.st_mode) and (s.st_nlink != 1 or s.st_mode & 0o6022):
            raise ValueError(f'Unsafe file permissions/hardlink: {p}')
        if stat.S_ISDIR(s.st_mode) and s.st_mode & 0o022:
            raise ValueError(f'Writable state subdirectory: {p}')


def confine():
    if os.geteuid() == 0:
        raise ValueError('Run the VM as an unprivileged user with /dev/kvm access, never root')
    # A capability-bearing launcher is also outside our supported security model.
    status = Path('/proc/self/status').read_text()
    for line in status.splitlines():
        if line.startswith(('CapEff:', 'CapPrm:', 'CapInh:', 'CapAmb:')) and int(line.split()[1], 16):
            raise ValueError('Run without effective/permitted/inheritable/ambient capabilities')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'PR_SET_NO_NEW_PRIVS failed')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'state':
            check_state(Path(sys.argv[2]), len(sys.argv) > 3 and sys.argv[3] == 'init')
        elif sys.argv[1] == 'exec':
            confine()
            # No host credentials, loader variables or agent sockets in the VMM environment.
            env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
            os.execve(sys.argv[2], sys.argv[2:], env)
        else:
            raise ValueError('Unknown security operation')
    except (OSError, ValueError) as e:
        sys.exit(f'error: {e}')
