import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch,Mock
spec=importlib.util.spec_from_file_location('shared_memory_runner',Path(__file__).resolve().parents[1]/'guest/runner.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
class SharedMemoryTests(unittest.TestCase):
 def test_boot_mounts_bounded_private_shared_memory_for_unprivileged_workers(self):
  target=Mock()
  with patch.object(runner,'Path',return_value=target),patch.object(runner.os.path,'ismount',return_value=False),patch.object(runner.subprocess,'run') as run,patch.object(runner.os,'chmod') as chmod:
   runner.ensure_shared_memory()
   args=run.call_args.args[0]
   self.assertIn('rw,nosuid,nodev,noexec,mode=1777,size=64m',args)
   self.assertTrue(run.call_args.kwargs['check']);chmod.assert_called_once_with(target,0o1777)
 def test_existing_mount_is_retained(self):
  with patch.object(runner,'Path'),patch.object(runner.os.path,'ismount',return_value=True),patch.object(runner.subprocess,'run') as run,patch.object(runner.os,'chmod'):
   runner.ensure_shared_memory();run.assert_not_called()
