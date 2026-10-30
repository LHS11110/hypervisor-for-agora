import importlib.util,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('cleanup_manager',Path(__file__).resolve().parents[1]/'manager.py');manager=importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)

class CleanupTests(unittest.TestCase):
 def test_cleanup_is_complete_scoped_idempotent_and_blocks_recreation(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'command') as command,patch.object(manager,'running',return_value=False):
   root=Path(temporary)
   for name in ['canvases/351/guest.raw','canvases/352/guest.raw','settings/351.json','settings/351.next','settings/352.json','launch/351.env','launch/351.next','backup-test/351.raw','backup-test/352.raw','backup-test/base.raw']:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'keep unless target')
   manager.last_used[351]=1
   self.assertEqual(manager.execute({'canvasId':351,'action':'delete'}),{'deleted':True})
   for name in ['canvases/351','settings/351.json','settings/351.next','launch/351.env','launch/351.next','backup-test/351.raw']:self.assertFalse((root/name).exists(),name)
   for name in ['canvases/352/guest.raw','settings/352.json','backup-test/352.raw','backup-test/base.raw']:self.assertTrue((root/name).exists(),name)
   self.assertTrue((root/'deleted/351').exists());self.assertNotIn(351,manager.last_used);self.assertNotIn(351,manager.locks)
   command.assert_any_call('systemctl','stop','agora-runtime@351.service');command.assert_any_call('systemctl','stop','agora-runtime-network@351.service')
   self.assertEqual(manager.execute({'canvasId':351,'action':'delete'}),{'deleted':True})
   with self.assertRaises(ValueError):manager.execute({'canvasId':351,'language':'python','code':'print(1)'})

 def test_busy_execution_rejects_cleanup_before_mutating_files(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'command') as command:
   lock=threading.Lock();lock.acquire()
   with patch.dict(manager.locks,{353:lock}):
    with self.assertRaises(manager.Unavailable):manager.execute({'canvasId':353,'action':'delete'})
   command.assert_not_called();self.assertFalse((Path(temporary)/'deleted').exists())

 def test_stop_failure_preserves_disk_and_leaves_retryable_tombstone(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'command',side_effect=OSError('unavailable')):
   disk=manager.CANVASES/'354/guest.raw';disk.parent.mkdir(parents=True);disk.write_bytes(b'original')
   with self.assertRaises(OSError):manager.execute({'canvasId':354,'action':'delete'})
   self.assertEqual(disk.read_bytes(),b'original');self.assertTrue((Path(temporary)/'deleted/354').exists())

if __name__=='__main__':unittest.main()
