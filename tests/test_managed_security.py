#!/usr/bin/env python3
"""Root-only real service integration test. Creates and removes an isolated smoke unit."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAME = 'agora-hypervisor-smoke'
STATE = Path('/var/lib') / NAME
SOCKET = Path('/run') / (NAME + '.sock')
SOCKET_UNIT = Path('/run/systemd/system') / (NAME + '.socket')
UNIT = Path('/run/systemd/system') / (NAME + '.service')


def command(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=20).decode().strip()


@unittest.skipUnless(os.geteuid() == 0, 'Requires root and systemd with KVM')
class ManagedSecurity(unittest.TestCase):
    def test_real_service_isolation(self):
        self.assertFalse(UNIT.exists(), 'Smoke unit already exists; inspect before retrying')
        self.assertFalse(STATE.exists(), 'Smoke state already exists; inspect before retrying')
        STATE.mkdir(mode=0o700)
        for name in ('guest.raw', 'seed.img'):
            with (STATE / name).open('wb') as f:
                f.truncate(16 * 1024 * 1024)
                os.posix_fallocate(f.fileno(), 0, f.tell() or 16 * 1024 * 1024)
        text = (ROOT / 'systemd/agora-hypervisor.service').read_text()
        text = text.replace('/var/lib/agora-hypervisor', str(STATE))
        text = text.replace('StateDirectory=agora-hypervisor', 'StateDirectory=' + NAME)
        text = text.replace('agora-hypervisor.socket', NAME + '.socket')
        socket_text = (ROOT / 'systemd/agora-hypervisor.socket').read_text()
        socket_text = socket_text.replace('agora-hypervisor.service', NAME + '.service').replace('/run/agora-hypervisor.sock', str(SOCKET))
        SOCKET_UNIT.write_text(socket_text)
        UNIT.write_text(text)
        def api(method, endpoint, data=None):
            args = ['curl', '--disable', '--fail-with-body', '-sS', '--unix-socket', str(SOCKET),
                    '-X', method, 'http://localhost/api/v1/' + endpoint]
            if data is not None:
                args += ['-H', 'Content-Type: application/json', '-d', json.dumps(data)]
            return command(*args)
        try:
            command('systemctl', 'daemon-reload')
            command('systemctl', 'start', NAME)
            for _ in range(100):
                if SOCKET.exists():
                    break
                if command('systemctl', 'is-active', NAME) != 'active':
                    self.fail(command('journalctl', '-u', NAME, '-n', '30', '--no-pager'))
                time.sleep(.05)
            for _ in range(100):
                info = json.loads(api('GET', 'vm.info'))
                if info['state'] == 'Running':
                    break
                time.sleep(.05)
            self.assertEqual(info['state'], 'Running')
            self.assertTrue(info['config']['landlock_enable'])
            pid = int(command('systemctl', 'show', NAME, '-p', 'MainPID', '--value'))
            status_path = Path('/proc') / str(pid)
            for task in (status_path / 'task').iterdir():
                status = dict(line.split(':', 1) for line in (task / 'status').read_text().splitlines())
                self.assertNotEqual(int(status['Uid'].split()[1]), 0)
                self.assertNotEqual(int(status['Uid'].split()[1]), 1001)
                self.assertEqual(status['NoNewPrivs'].strip(), '1')
                self.assertEqual(status['Seccomp'].strip(), '2')
                self.assertGreater(int(status['Seccomp_filters']), 1)
                for cap in ('CapEff', 'CapPrm', 'CapBnd', 'CapAmb'):
                    self.assertEqual(int(status[cap], 16), 0)
            for ns in ('net', 'mnt', 'pid', 'ipc', 'uts'):
                self.assertNotEqual(os.readlink(status_path / 'ns' / ns), os.readlink('/proc/self/ns/' + ns))
            jail = status_path / 'root'
            for name in ('home/steam', 'run/dbus/system_bus_socket', 'run/user/1001', 'etc/shadow'):
                self.assertFalse((jail / name).exists(), name)
            mounts = [line.split() for line in (status_path / 'mountinfo').read_text().splitlines()]
            for path in ('/', '/usr/local/lib/agora-hypervisor/cloud-hypervisor'):
                self.assertTrue(any(row[4] == path and 'ro' in row[5].split(',') for row in mounts), path)
            limits = (status_path / 'limits').read_text()
            self.assertRegex(limits, r'Max core file size\s+0\s+0')
            self.assertRegex(limits, r'Max file size\s+10737418240\s+10737418240')
            net = (status_path / 'net/dev').read_text()
            self.assertEqual(len(net.strip().splitlines()), 3)  # headers plus loopback only
            cgroup = Path('/sys/fs/cgroup') / command('systemctl', 'show', NAME, '-p', 'ControlGroup', '--value').lstrip('/')
            self.assertEqual((cgroup / 'memory.max').read_text().strip(), str(12 * 1024**3))
            self.assertEqual((cgroup / 'memory.swap.max').read_text().strip(), '0')
            self.assertEqual((cgroup / 'pids.max').read_text().strip(), '64')
            quota, period = (cgroup / 'cpu.max').read_text().split()
            self.assertEqual(int(quota), 2 * int(period))
            io_limits = (cgroup / 'io.max').read_text()
            for limit in ('rbps=52428800', 'wbps=52428800', 'riops=1000', 'wiops=1000'):
                self.assertIn(limit, io_limits)
            self.assertEqual(SOCKET.stat().st_mode & 0o077, 0)
            denied = subprocess.run(['runuser', '-u', 'steam', '--', 'curl', '--disable', '-sS', '--unix-socket',
                                     str(SOCKET), 'http://localhost/api/v1/vmm.ping'], capture_output=True)
            self.assertNotEqual(denied.returncode, 0)
            command('systemctl', 'stop', NAME)
            self.assertFalse(status_path.exists())
            self.assertFalse(SOCKET.exists())
        except Exception:
            print(subprocess.run(['journalctl', '-u', NAME, '-n', '30', '--no-pager'], capture_output=True, text=True).stdout)
            raise
        finally:
            subprocess.run(['systemctl', 'stop', NAME], capture_output=True)
            subprocess.run(['systemctl', 'stop', NAME + '.socket'], capture_output=True)
            UNIT.unlink(missing_ok=True)
            SOCKET_UNIT.unlink(missing_ok=True)
            subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
            subprocess.run(['systemctl', 'reset-failed', NAME], capture_output=True)
            # DynamicUser StateDirectory may be an administrative symlink into /var/lib/private.
            if STATE.is_symlink():
                target = STATE.resolve()
                STATE.unlink()
                if target.parent == Path('/var/lib/private') and target.name == NAME:
                    shutil.rmtree(target)
            elif STATE.exists():
                shutil.rmtree(STATE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
