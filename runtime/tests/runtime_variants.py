"""Run as UID 1000 inside the configured guest image; exercise every installed engine."""
import base64,importlib.util,json,os,socket,subprocess,unittest
spec=importlib.util.spec_from_file_location('runner','/usr/local/lib/agora-runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class Variants(unittest.TestCase):
 def start(self,language,config):
  self.language=language;self.control,child=socket.socketpair();self.control.settimeout(25)
  command,env=runner.worker_command(language,config,str(child.fileno()))
  self.process=subprocess.Popen(command,env=env,cwd='/workspace',stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,pass_fds=(child.fileno(),));child.close()
  process,control=self.process,self.control
  def cleanup():
   process.kill();process.wait();control.close();process.stdout.close()
  self.addCleanup(cleanup)
 def close(self):
  self.process.kill();self.process.wait();self.control.close();self.process.stdout.close()
 def cell(self,code):
  payload=base64.b64encode(code.encode()) if self.language=='java' else json.dumps({'code':code}).encode()
  self.control.sendall(payload+b'\n');reply=b''
  while not reply.endswith(b'\n'):
   try:chunk=self.control.recv(1024)
   except ConnectionResetError:raise RuntimeError(self.process.stdout.read().decode())
   if not chunk:raise RuntimeError('Worker terminated: '+str(self.process.stdout.read()))
   reply+=chunk
  self.response=json.loads(reply);output=b'';os.set_blocking(self.process.stdout.fileno(),False)
  while True:
   try:
    data=os.read(self.process.stdout.fileno(),8192)
    if not data:break
    output+=data
   except BlockingIOError:break
  return self.response['exitCode'],output.decode()
 def test_python_versions_and_optimization(self):
  for engine,version in [('py311','3.11'),('py312','3.12'),('py313','3.13'),('py314','3.14'),('py315','3.15')]:
   with self.subTest(engine=engine):
    self.start('python',{'engine':engine,'optimize':'2','writeBytecode':False})
    self.assertEqual(self.cell('import sys,ssl,sqlite3\nprint(str(sys.version_info.major)+"."+str(sys.version_info.minor));print(sys.flags.optimize,sys.dont_write_bytecode);value=7'),(0,version+'\n2 True\n'))
    self.assertEqual(self.cell('print(value+1)'),(0,'8\n'))
    self.close();self._cleanups.pop()
 def test_node_versions_and_strict_mode(self):
  for engine,version in [('node20','v20.'),('node22','v22.'),('node24','v24.')]:
   with self.subTest(engine=engine):
    self.start('javascript',{'engine':engine,'strict':True})
    status,output=self.cell('console.log(process.version);var count=3;')
    self.assertEqual(status,0);self.assertTrue(output.startswith(version),output)
    self.assertEqual(self.cell('accidentalGlobal=3;')[0],1)
    self.assertEqual(self.cell('console.log(++count)'),(0,'4\n'))
    self.close();self._cleanups.pop()
 def test_clang_versions_standards_optimization_and_cache(self):
  for engine in ['clang19','clang21']:
   with self.subTest(engine=engine):
    self.start('cpp',{'engine':engine,'standard':'c++20','optimization':'2'})
    code='#include <cstdio>\nint count=0;\nprintf("%ld %d\\n",__cplusplus,++count);'
    self.assertEqual(self.cell(code),(0,'202002 1\n'))
    self.assertEqual(self.cell(code),(0,'202002 2\n'));self.assertEqual(self.response['compileCount'],1)
    self.close();self._cleanups.pop()
 def test_gcc_program_mode_and_standard(self):
  self.start('cpp',{'engine':'gcc14','standard':'c++20','optimization':'2'})
  code='#include <cstdio>\nint count=0;int main(){printf("%d %ld %d\\n",__GNUC__,__cplusplus,++count);}'
  self.assertEqual(self.cell(code),(0,'14 202002 1\n'));self.assertEqual(self.cell(code),(0,'14 202002 1\n'))
 def test_c_standards_on_all_compilers(self):
  for engine in ['clang19','clang21','gcc14']:
   for standard in ['c11','c17','c23']:
    with self.subTest(engine=engine,standard=standard):
     self.start('c',{'engine':engine,'standard':standard})
     status,output=self.cell('#include <stdio.h>\nint main(){printf("%ld\\n",__STDC_VERSION__);return 0;}')
     self.assertEqual(status,0,output);self.assertGreaterEqual(int(output.strip()),201112)
     self.close();self._cleanups.pop()
 def test_native_option_flags_change_compiler_behavior(self):
  for engine in ['clang19','clang21','gcc14']:
   with self.subTest(engine=engine):
    self.start('cpp',{'engine':engine,'warnings':True,'warningsAsErrors':True,'exceptions':False})
    self.assertEqual(self.cell('int main(){int unused=1;return 0;}')[0],1)
    self.assertEqual(self.cell('int main(){throw 1;}')[0],1)
    self.close();self._cleanups.pop()
 def test_java_versions_shared_context_and_main(self):
  for engine,version in [('jdk17','17'),('jdk21','21'),('jdk25','25')]:
   with self.subTest(engine=engine):
    self.start('java',{'engine':engine})
    self.assertEqual(self.cell('int count=3; int total(int n){return count+n;} System.out.println(total(4));'),(0,'7\n'))
    self.assertEqual(self.cell('count+=2; System.out.println(total(4));'),(0,'9\n'))
    self.assertEqual(self.cell('public class Main { public static void main(String[] args){System.out.println(System.getProperty("java.specification.version"));}}'),(0,version+'\n'))
    self.assertEqual(self.cell('System.out.println(unknownName);')[0],1)
    self.assertEqual(self.cell('System.out.println(count);'),(0,'5\n'))
    self.close();self._cleanups.pop()

if __name__=='__main__':
 if os.getuid()==0:raise SystemExit('Run as unprivileged UID 1000')
 unittest.main()
