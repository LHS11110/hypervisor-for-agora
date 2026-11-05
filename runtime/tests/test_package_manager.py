import importlib.util,unittest,struct
from pathlib import Path
from unittest.mock import Mock,patch
spec=importlib.util.spec_from_file_location('guest_packages',Path(__file__).resolve().parents[1]/'guest/package_manager.py');packages=importlib.util.module_from_spec(spec);spec.loader.exec_module(packages)
class PackageManagerTests(unittest.TestCase):
 def test_common_package_operations_use_real_apt_get(self):
  for tool,args,tail in [('apt',['update'],['update']),('apt-get',['install','-y','jq'],['install','jq']),('apt',['reinstall','hello'],['install','hello']),('apt',['full-upgrade'],['dist-upgrade'])]:
   result=packages.validated_command({'tool':tool,'args':args});self.assertEqual(result[0],'/usr/bin/apt-get');self.assertEqual(result[-len(tail):],tail)
 def test_package_names_support_architecture_and_exact_versions(self):
  self.assertEqual(packages.validated_command({'tool':'apt','args':['install','libc6:amd64=2.41-12+deb13u1']})[-1],'libc6:amd64=2.41-12+deb13u1')
 def test_arbitrary_commands_hooks_and_local_deb_files_are_rejected(self):
  for args in [['install','./untrusted.deb'],['install','/tmp/package.deb'],['-o','APT::Update::Pre-Invoke::=/bin/sh','update'],['--allow-unauthenticated','install','jq'],['-c','/workspace/apt.conf','update'],['install','jq;id'],['install','$(id)'],['install','--option=DPkg::Pre-Invoke::=/bin/sh'],['edit-sources']]:
   with self.assertRaises(ValueError,msg=str(args)):packages.validated_command({'tool':'apt','args':args})
 def test_update_cannot_receive_a_path_or_extra_package_arguments(self):
  with self.assertRaises(ValueError):packages.validated_command({'tool':'apt','args':['update','jq']})
 def test_wrong_peer_uid_is_refused_before_process_creation(self):
  service=packages.PackageService({'vcpus':1,'workerMemoryMiB':768});service.slots=Mock();conn=Mock();conn.getsockopt.return_value=struct.pack('3i',123,1001,1001)
  with patch.object(packages.subprocess,'Popen') as spawn:service.connection(conn);spawn.assert_not_called()
  self.assertIn(b'workspace user',conn.sendall.call_args.args[0]);conn.close.assert_called_once()
 def test_signalled_installers_are_not_reported_as_success(self):
  for status,expected in [(0,0),(100,100),(-2,130),(-9,137)]:self.assertEqual(packages.exit_status(status),expected)
 def test_read_only_tools_run_without_a_privileged_connection(self):
  with patch.object(packages.os,'execv',side_effect=RuntimeError('exec')) as execute,patch.object(packages.socket,'socket') as connect:
   with self.assertRaises(RuntimeError):packages.client('apt-cache',['policy','jq'])
   execute.assert_called_once_with('/usr/bin/apt-cache',['apt-cache','policy','jq']);connect.assert_not_called()
if __name__=='__main__':unittest.main()
