import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,file):
 spec=importlib.util.spec_from_file_location(name,ROOT/file);obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
options=module('options','runtime_options.py');manager=module('options_manager','manager.py')

class RuntimeOptionsTests(unittest.TestCase):
 def test_shared_catalogue_matches_guest_and_rejects_argv_injection(self):
  self.assertEqual((ROOT/'runtime_options.py').read_bytes(),(ROOT/'guest/runtime_options.py').read_bytes())
  for language in options.CATALOG:self.assertEqual(options.normalize(language),options.CATALOG[language]['defaults'])
  invalid=[('cpp',{'engine':'/bin/bash'}),('cpp',{'standard':'c++17 -o /tmp/x'}),('cpp',{'flags':['-Xclang','-load','evil.so']}),('python',{'writeBytecode':1}),('java',{'engine':'jdk17','release':'25'}),('java',{'engine':'jdk25','release':'21','preview':True})]
  for language,config in invalid:
   with self.assertRaises((ValueError,TypeError)):options.normalize(language,config)
  self.assertEqual(options.normalize('java',{'engine':'jdk17'})['release'],'17')

 def test_persisted_settings_are_revision_checked_without_booting_vm(self):
  with tempfile.TemporaryDirectory() as temporary, patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'running',return_value=False),patch.object(manager,'prepare') as prepare:
   result=manager.execute({'canvasId':321,'action':'settings_get'})
   self.assertEqual(result['revision'],0)
   saved=manager.execute({'canvasId':321,'action':'settings_set','language':'cpp','config':{'engine':'clang21','standard':'c++20'},'revision':0})
   self.assertEqual(saved['revision'],1)
   self.assertEqual(manager.settings(321)['languages']['cpp']['standard'],'c++20')
   prepare.assert_not_called()
   with self.assertRaises(ValueError):manager.execute({'canvasId':321,'action':'settings_set','language':'cpp','config':{},'revision':0})

 def test_live_configuration_only_stops_target_language(self):
  with tempfile.TemporaryDirectory() as temporary, patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'running',return_value=True),patch.object(manager,'exchange',return_value={'configured':True}) as exchange:
   manager.execute({'canvasId':322,'action':'settings_set','language':'python','config':{'engine':'py313'},'revision':0})
   payload=exchange.call_args.args[1]
   self.assertEqual(payload,{'action':'configure','language':'python','runtime':options.normalize('python',{'engine':'py313'})})

class ResourceTemplateTests(unittest.TestCase):
 def test_fixed_templates_reject_unbounded_or_injected_values(self):
  self.assertEqual(options.resource_template('large')['memoryMiB'],4096)
  for key in ['custom','large --memory size=99G',{'memoryMiB':999999},None]:
   with self.assertRaises(ValueError):options.resource_template(key)

 def test_resource_change_stops_vm_persists_and_checks_revision(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'running',return_value=True),patch.object(manager,'command') as command,patch.object(manager,'resource_capacity') as capacity:
   result=manager.execute({'canvasId':333,'action':'resources_set','template':'medium','revision':0})
   self.assertEqual(result['resourceTemplate'],'medium');self.assertEqual(result['revision'],1)
   command.assert_called_once_with('systemctl','stop','agora-runtime@333.service')
   self.assertEqual(manager.settings(333)['resourceTemplate'],'medium')
   self.assertEqual(manager.settings(333)['languages'],options.defaults())
   command.reset_mock()
   manager.execute({'canvasId':333,'action':'resources_set','template':'medium','revision':1})
   command.assert_not_called()
   with self.assertRaises(ValueError):manager.execute({'canvasId':333,'action':'resources_set','template':'large','revision':0})

 def test_busy_canvas_and_capacity_failure_preserve_existing_vm_and_settings(self):
  import threading
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'running',return_value=True),patch.object(manager,'command') as command,patch.object(manager,'resource_capacity',side_effect=manager.Unavailable('full')):
   with self.assertRaises(manager.Unavailable):manager.execute({'canvasId':334,'action':'resources_set','template':'large','revision':0})
   command.assert_not_called();self.assertEqual(manager.settings(334)['revision'],0)
   lock=threading.Lock();lock.acquire()
   with patch.dict(manager.locks,{334:lock}):
    with self.assertRaises(manager.Unavailable):manager.execute({'canvasId':334,'action':'resources_set','template':'medium','revision':0})

 def test_aggregate_capacity_accounts_for_other_active_templates(self):
  with patch.object(manager,'active_units',return_value=['agora-runtime@1.service','agora-runtime@2.service']),patch.object(manager,'settings',return_value={'resourceTemplate':'large'}):
   with self.assertRaises(manager.Unavailable):manager.resource_capacity(3,options.resource_template('small'))

 def test_launch_arguments_and_host_limits_follow_allowlisted_template(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'command') as command:
   manager.configure_launch(335,'large')
   text=(Path(temporary)/'launch/335.env').read_text()
   self.assertIn('AGORA_VM_CPUS=boot=4',text);self.assertIn('AGORA_VM_MEMORY=size=4096M',text)
   command.assert_called_once_with('systemctl','set-property','--runtime','agora-runtime@335.service','MemoryMax=4608M','CPUQuota=400%')

class DiskSettingsTests(unittest.TestCase):
 def test_only_allowed_integer_sizes(self):
  for size in [6,12,24]:self.assertEqual(options.disk_size(size),size)
  for size in [True,1,6.0,999,'12',None]:
   with self.assertRaises(ValueError):options.disk_size(size)

 def test_inactive_disk_choice_is_durable_without_allocating_or_booting(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'replace_disk') as replace,patch.object(manager,'command') as command:
   result=manager.execute({'canvasId':341,'action':'disk_set','sizeGiB':12,'revision':0})
   self.assertEqual(result['diskGiB'],12);self.assertEqual(result['revision'],1)
   self.assertEqual(manager.settings(341)['diskGiB'],12);replace.assert_not_called();command.assert_not_called()
   for size,revision in [(6,1),(24,0)]:
    with self.assertRaises(ValueError):manager.execute({'canvasId':341,'action':'disk_set','sizeGiB':size,'revision':revision})
   self.assertEqual(manager.execute({'canvasId':341,'action':'disk_set','sizeGiB':12,'revision':1})['revision'],1)

 def test_storage_failure_does_not_stop_vm_or_mutate_settings(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'disk_capacity',side_effect=ValueError('full')),patch.object(manager,'command') as command:
   directory=manager.CANVASES/'342';directory.mkdir(parents=True);(directory/'guest.raw').write_bytes(b'original')
   with self.assertRaises(ValueError):manager.execute({'canvasId':342,'action':'disk_set','sizeGiB':12,'revision':0})
   command.assert_not_called();self.assertEqual(manager.settings(342)['revision'],0)
   self.assertEqual((directory/'guest.raw').read_bytes(),b'original')

 def test_failed_replacement_preserves_original_and_removes_temporary_copy(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'disk_capacity'),patch.object(manager,'command',side_effect=OSError('copy failed')):
   target=Path(temporary)/'guest.raw';target.write_bytes(b'original')
   with self.assertRaises(OSError):manager.replace_disk(target,target,12*1024**3)
   self.assertEqual(target.read_bytes(),b'original');self.assertFalse(target.with_name('guest.growing').exists())

 def test_failed_filesystem_check_preserves_original(self):
  import shutil
  def copy(*args):shutil.copyfile(args[-2],args[-1])
  checked=type('Result',(),{'returncode':4})()
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'disk_capacity'),patch.object(manager,'command',side_effect=copy),patch.object(manager.os,'posix_fallocate'),patch.object(manager.subprocess,'run',return_value=checked):
   disk=Path(temporary)/'guest.raw';disk.write_bytes(b'original')
   with self.assertRaises(manager.Unavailable):manager.replace_disk(disk,disk,12*1024**3)
   self.assertEqual(disk.read_bytes(),b'original');self.assertFalse(disk.with_name('guest.growing').exists())

 def test_initial_creation_can_resume_after_an_interrupted_copy(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'disk_capacity'),patch.object(manager,'running',return_value=True),patch.object(manager.os,'chown'),patch.object(manager.pwd,'getpwnam',return_value=type('User',(),{'pw_uid':1000,'pw_gid':1000})()):
   directory=manager.CANVASES/'344';directory.mkdir(parents=True);(directory/'guest.growing').write_bytes(b'incomplete')
   def replace(source,target,size):
    self.assertFalse((directory/'guest.growing').exists());target.write_bytes(b'valid disk')
   with patch.object(manager,'replace_disk',side_effect=replace):manager.prepare(344)
   self.assertEqual((directory/'guest.raw').read_bytes(),b'valid disk')

 def test_actual_capacity_recovers_commit_before_settings_write(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'):
   directory=manager.CANVASES/'343';directory.mkdir(parents=True)
   with (directory/'guest.raw').open('wb') as stream:stream.truncate(12*1024**3)
   self.assertEqual(manager.settings(343)['diskGiB'],12)
   with self.assertRaises(ValueError):manager.execute({'canvasId':343,'action':'disk_set','sizeGiB':6,'revision':0})

class JavaDependenciesTests(unittest.TestCase):
 def test_coordinates_are_bounded_fixed_and_canonical(self):
  values=['org.apache.commons:commons-lang3:3.17.0','com.fasterxml.jackson.core:jackson-databind:2.18.2']
  self.assertEqual(options.normalize('java',{'dependencies':values})['dependencies'],sorted(values))
  for value in ['not-a-coordinate','https://evil/path:x:1','g:a:LATEST','g:a:1-SNAPSHOT','g:a:[1,2]','g:a:1</version>']:
   with self.assertRaises(ValueError):options.normalize('java',{'dependencies':[value]})
  for value in ['g:a:1',[values[0]]*21,['g:a:1','g:a:2']]:
   with self.assertRaises(ValueError):options.normalize('java',{'dependencies':value})
 def test_install_uses_authoritative_settings_and_separate_timeout(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'prepare',return_value='vsock'),patch.object(manager,'exchange',return_value={'installed':True}) as exchange:
   result=manager.execute({'canvasId':355,'action':'java_dependencies'})
   self.assertEqual(result,{'installed':True})
   exchange.assert_called_once_with('vsock',{'action':'java_dependencies','runtime':options.normalize('java')},response_timeout=185)

class Python314Tests(unittest.TestCase):
 def test_python314_engine_is_valid_and_has_its_own_interpreter(self):
  self.assertEqual(options.normalize('python',{'engine':'py314'})['engine'],'py314')
  self.assertEqual(options.PYTHON['py314'],'/opt/python/3.14/bin/python3.14')
  self.assertEqual(options.normalize('python')['engine'],'py312')

class Python315Tests(unittest.TestCase):
 def test_python315_engine_has_separate_interpreter(self):
  self.assertEqual(options.normalize('python',{'engine':'py315'})['engine'],'py315')
  self.assertEqual(options.PYTHON['py315'],'/opt/python/3.15/bin/python3.15')

class PythonToolAlignmentTests(unittest.TestCase):
 def test_selected_python_environment_replaces_managed_paths_and_preserves_custom_paths(self):
  before={'PATH':'/workspace/custom-bin:/opt/agora/python-tools:/workspace/.python/bin:/usr/bin',
          'PYTHONPATH':'/workspace/custom-lib:/workspace/.python', 'LD_LIBRARY_PATH':'/usr/local/lib:/workspace/native'}
  after=options.python_environment('py315',before)
  self.assertEqual(after['AGORA_PYTHON_ENGINE'],'py315')
  self.assertEqual(after['PIP_TARGET'],'/workspace/.python/3.15')
  self.assertEqual(after['PYTHONPATH'],'/workspace/.python/3.15:/workspace/custom-lib')
  self.assertEqual(after['PATH'],'/opt/agora/python-tools:/workspace/.python/3.15/bin:/workspace/custom-bin:/usr/bin')
  self.assertEqual(after['LD_LIBRARY_PATH'],'/opt/python/3.15/lib:/workspace/native')
  self.assertEqual(options.python_environment('py315',after),after)
 def test_bash_execution_receives_the_authoritative_python_profile(self):
  with tempfile.TemporaryDirectory() as temporary,patch.object(manager,'STATE',Path(temporary)),patch.object(manager,'CANVASES',Path(temporary)/'canvases'),patch.object(manager,'running',return_value=False),patch.object(manager,'prepare',return_value='vsock'),patch.object(manager,'exchange',return_value={'exitCode':0}) as exchange:
   manager.execute({'canvasId':356,'action':'settings_set','language':'python','config':{'engine':'py315'},'revision':0})
   manager.execute({'canvasId':356,'language':'bash','code':'pip install numpy'})
   self.assertEqual(exchange.call_args.args[1]['pythonRuntime']['engine'],'py315')

if __name__=='__main__':unittest.main()
