import importlib.util,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('agent_update',Path(__file__).resolve().parents[1]/'agent_update.py');agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)
class AgentUpdateTests(unittest.TestCase):
 def test_invalid_instance_and_running_guest_are_never_mounted(self):
  with patch.object(agent,'run') as run,patch.object(agent.subprocess,'run') as state:
   for value in ('../42','0','-1','2147483648'):
    with self.assertRaises(ValueError):agent.update(value)
   state.assert_not_called();state.return_value.returncode=0
   with self.assertRaises(RuntimeError):agent.update('42')
   run.assert_not_called()
 def test_stopped_disk_update_is_idempotent_and_keeps_workspace(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);source=root/'source';source.mkdir();(source/'runner.py').write_text('runner');(source/'terminal.py').write_text('terminal');(source/'notebook.py').write_text('notebook');(source/'notebook_worker.py').write_text('worker');(source/'package_manager.py').write_text('packages');directory=root/'state/42';directory.mkdir(parents=True);(directory/'guest.raw').touch();calls=[]
   def command(*args):
    calls.append(args)
    if args[0]=='mount':
     mount=Path(args[-1]);(mount/'usr/local/lib').mkdir(parents=True);(mount/'workspace').mkdir();(mount/'workspace/keep').write_text('user data')
    if args[0]=='sync':
     mount=Path(args[-1]);self.assertEqual((mount/'workspace/keep').read_text(),'user data');self.assertEqual((mount/'usr/local/lib/agora-runner.py').read_text(),'runner');self.assertEqual((mount/'usr/local/lib/terminal.py').read_text(),'terminal')
   with patch.object(agent,'SOURCE',source),patch.object(agent,'STATE',root/'state'),patch.object(agent,'run',side_effect=command),patch.object(agent.subprocess,'run') as state:
    state.return_value.returncode=3;agent.update('42');first=len(calls);agent.update('42');self.assertEqual(first,len(calls));self.assertTrue((directory/'agent-version').exists())
if __name__=='__main__':unittest.main()
