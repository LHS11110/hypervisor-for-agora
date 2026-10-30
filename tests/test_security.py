#!/usr/bin/env python3
"""Run as an ordinary KVM user: python3 -I tests/test_security.py"""
import importlib.util
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('security', ROOT / 'scripts/security.py')
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class StateSecurity(unittest.TestCase):
    def test_unsafe_state_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            security.check_state(state)
            state.chmod(0o755)
            with self.assertRaises(ValueError):
                security.check_state(state)
            state.chmod(0o700)
            (state / 'link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                security.check_state(state)
            (state / 'link').unlink()
            (state / 'disk').touch(mode=0o600)
            os.link(state / 'disk', state / 'alias')
            with self.assertRaises(ValueError):
                security.check_state(state)

    def test_no_new_privs_and_core_limit(self):
        if os.geteuid() == 0:
            self.skipTest('Confinement intentionally refuses root')
        code = 'import resource; from pathlib import Path; assert "NoNewPrivs:\\t1" in Path("/proc/self/status").read_text(); assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)'
        subprocess.run(['python3', '-I', str(ROOT / 'scripts/security.py'), 'exec',
                        '/usr/bin/python3', '-I', '-c', code], check=True)

    def test_hardened_firmware_boot(self):
        if os.geteuid() == 0 or not os.access('/dev/kvm', os.R_OK | os.W_OK):
            self.skipTest('Requires unprivileged KVM access')
        with tempfile.TemporaryDirectory(prefix='agora-security-') as d:
            state = Path(d)
            for name in ('bin', 'firmware'):
                shutil.copytree(ROOT / '.local' / name, state / name)
            for name in ('guest.raw', 'seed.img'):
                with (state / name).open('wb') as f:
                    f.truncate(16 * 1024 * 1024)
                (state / name).chmod(0o600)
            env = {**os.environ, 'AGORA_STATE_DIR': d, 'AGORA_MEMORY': '256M', 'AGORA_CPUS': '1'}
            master, slave = pty.openpty()
            proc = subprocess.Popen([str(ROOT / 'scripts/hypervisor'), 'run-dev'], env=env,
                                    stdin=slave, stdout=slave, stderr=slave)
            def api(method, endpoint, data=None, success=True):
                cmd = ['curl', '--disable', '-sS', '--unix-socket', d + '/api.sock', '-X', method,
                       '-w', '\n%{http_code}', 'http://localhost/api/v1/' + endpoint]
                if data is not None:
                    cmd += ['-H', 'Content-Type: application/json', '-d', json.dumps(data)]
                result = subprocess.check_output(cmd, timeout=5).decode()
                body, status = result.rsplit('\n', 1)
                if success:
                    self.assertTrue(status.startswith('2'), result)
                return body, status
            try:
                for _ in range(200):
                    if (state / 'api.sock').exists():
                        break
                    if proc.poll() is not None:
                        self.fail('VMM exited before API startup: ' + os.read(master, 65536).decode(errors='replace'))
                    time.sleep(.05)
                for _ in range(100):
                    info = json.loads(api('GET', 'vm.info')[0])
                    if info['state'] == 'Running':
                        break
                    time.sleep(.05)
                self.assertEqual(info['state'], 'Running')
                config = info['config']
                self.assertTrue(config['landlock_enable'])
                for disk in config['disks']:
                    self.assertEqual(disk['image_type'], 'Raw')
                    self.assertFalse(disk['backing_files'])
                pid = json.loads(api('GET', 'vmm.ping')[0])['pid']
                baseline = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
                for task in Path(f'/proc/{pid}/task').iterdir():
                    status = dict(line.split(':', 1) for line in (task / 'status').read_text().splitlines())
                    self.assertEqual(status['NoNewPrivs'].strip(), '1')
                    self.assertEqual(status['Seccomp'].strip(), '2')
                    self.assertGreater(int(status['Seccomp_filters']), int(baseline['Seccomp_filters']))
                    self.assertEqual(int(status['Uid'].split()[1]), os.geteuid())
                    self.assertEqual(int(status['CapEff'], 16), 0)
                self.assertEqual((state / 'api.sock').stat().st_mode & 0o077, 0)
                # Landlock must deny a file outside the configured resources.
                with tempfile.NamedTemporaryFile() as outside:
                    outside.truncate(1024 * 1024)
                    body, code = api('PUT', 'vm.add-disk', {'path': outside.name, 'readonly': True, 'image_type': 'Raw'}, success=False)
                    self.assertTrue(code.startswith('4') or code.startswith('5'), (body, code))
                    self.assertIn('Permission denied', body)
                # Parallel start is rejected before touching the live socket.
                second = subprocess.run([str(ROOT / 'scripts/hypervisor'), 'run-dev'], env=env, capture_output=True, timeout=5)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn(b'lock', second.stderr)
                api('PUT', 'vmm.shutdown')
                self.assertEqual(proc.wait(timeout=10), 0)
                self.assertFalse((state / 'api.sock').exists())
                # Comma-based device option injection and oversized allocations are rejected.
                for name, value in [('AGORA_CPUS', '2,max=64'), ('AGORA_MEMORY', '999999M'),
                                    ('AGORA_MEMORY', '128M'), ('AGORA_TAP', 'tap0,ip=1.2.3.4')]:
                    bad_env = {**env, name: value}
                    rejected = subprocess.run([str(ROOT / 'scripts/hypervisor'), 'run-dev'],
                                              env=bad_env, capture_output=True, timeout=5)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse((state / 'api.sock').exists())
                # Terminating the wrapper must stop the VMM before releasing its lock/socket.
                proc = subprocess.Popen([str(ROOT / 'scripts/hypervisor'), 'run-dev'], env=env,
                                        stdin=slave, stdout=slave, stderr=slave)
                for _ in range(200):
                    if (state / 'api.sock').exists():
                        break
                    time.sleep(.05)
                vmm_pid = json.loads(api('GET', 'vmm.ping')[0])['pid']
                proc.terminate()
                self.assertEqual(proc.wait(timeout=10), 143)
                self.assertFalse(Path(f'/proc/{vmm_pid}').exists())
                self.assertFalse((state / 'api.sock').exists())
                # A changed executable must never be executed.
                with (state / 'bin/cloud-hypervisor').open('ab') as f:
                    f.write(b'tampered')
                bad = subprocess.run([str(ROOT / 'scripts/hypervisor'), 'doctor'], env=env, capture_output=True, timeout=5)
                self.assertNotEqual(bad.returncode, 0)
                self.assertIn(b'checksum mismatch', bad.stderr)
            finally:
                if proc.poll() is None:
                    try:
                        api('PUT', 'vmm.shutdown')
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
                        proc.wait()
                os.close(master)
                os.close(slave)


if __name__ == '__main__':
    unittest.main(verbosity=2)
