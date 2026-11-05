"""Exercise cold-boot tmpfiles creation without touching the host /run."""
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest

@unittest.skipUnless(os.geteuid() == 0 and shutil.which('systemd-tmpfiles'), 'root and systemd-tmpfiles required')
class RuntimeBootDirectoryTests(unittest.TestCase):
    def test_cold_boot_creates_bind_source_and_preserves_existing_socket_inode(self):
        rule = (Path(__file__).resolve().parents[1] / 'agora-runtime.tmpfiles.conf').read_text()
        with tempfile.TemporaryDirectory(prefix='agora-boot-') as temporary:
            root = Path(temporary)
            (root / 'etc').mkdir()
            (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\n')
            (root / 'etc/group').write_text('root:x:0:\n')
            def apply():
                subprocess.run(['systemd-tmpfiles', '--root', temporary, '--create', '-'], input=rule,
                               text=True, capture_output=True, check=True)
            directory = root / 'run/agora-runtime'
            self.assertFalse(directory.exists())
            apply()
            first = directory.stat()
            self.assertEqual((stat.S_IMODE(first.st_mode), first.st_uid, first.st_gid), (0o710, 0, 10001))
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                path = directory / 'manager.sock'
                listener.bind(str(path)); socket_inode = path.stat().st_ino
                apply()
                self.assertEqual(directory.stat().st_ino, first.st_ino)
                self.assertEqual(path.stat().st_ino, socket_inode)
                self.assertTrue(stat.S_ISSOCK(path.stat().st_mode))
