"""Run as UID 1000 inside the guest image. Real persistent workers, no mocks."""
import base64,importlib.util,json,os,socket,subprocess,unittest
spec=importlib.util.spec_from_file_location('runner','/usr/local/lib/agora-runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
class InputTests(unittest.TestCase):
 def start(self,language,config=None):
  self.language=language;self.control,child=socket.socketpair();self.control.settimeout(30);self.input_fd=os.memfd_create('test-stdin')
  command,env=runner.worker_command(language,config,str(child.fileno()));env['AGORA_STDIN_FD']=str(self.input_fd)
  self.process=subprocess.Popen(command,env=env,cwd='/workspace',stdin=child if language=='bash' else self.input_fd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,pass_fds=(child.fileno(),self.input_fd));child.close()
  process,control,input_fd=self.process,self.control,self.input_fd
  def cleanup():
   process.kill();process.wait();process.stdout.close();control.close();os.close(input_fd)
  self.addCleanup(cleanup)
 def cell(self,code,stdin=None):
  os.ftruncate(self.input_fd,0);os.lseek(self.input_fd,0,0)
  if stdin is not None:os.write(self.input_fd,stdin.encode())
  os.lseek(self.input_fd,0,0)
  if self.language=='java':payload=base64.b64encode(code.encode())+(b' 1' if stdin is not None else b' 0')+b'\n'
  elif self.language=='bash':
   encoded=base64.b64encode(code.encode()).decode();source=f'/proc/self/fd/{self.input_fd}' if stdin is not None else '/dev/null'
   fd=next(fd for fd in os.listdir(f'/proc/{self.process.pid}/fd') if int(fd)>2 and os.readlink(f'/proc/{self.process.pid}/fd/{fd}').startswith('socket:'))
   payload=f"eval -- \"$(printf '%s' '{encoded}' | base64 -d)\" <{source}\nbuiltin printf '{{\"exitCode\":%d}}\\n' \"$?\" >&{fd}\n".encode()
  else:payload=(json.dumps({'code':code,'stdinProvided':stdin is not None})+'\n').encode()
  self.control.sendall(payload);reply=b''
  while not reply.endswith(b'\n'):
   chunk=self.control.recv(1024)
   if not chunk:raise RuntimeError(self.process.stdout.read().decode())
   reply+=chunk
  response=json.loads(reply);self.response=response;output=b'';os.set_blocking(self.process.stdout.fileno(),False)
  while True:
   try:
    data=os.read(self.process.stdout.fileno(),8192)
    if not data:break
    output+=data
   except BlockingIOError:break
  return response['exitCode'],output.decode()
 def test_python_input_eof_and_state(self):
  self.start('python');code='value=input(); print(value); count=globals().get("count",0)+1; print(count)'
  self.assertEqual(self.cell(code,'한글\nunused\n'),(0,'한글\n1\n'))
  status,text=self.cell('input()');self.assertEqual(status,1);self.assertIn('EOFError',text)
  self.assertEqual(self.cell(code,'next\n'),(0,'next\n2\n'))
 def test_cpp_cached_main_fresh_input_and_eof(self):
  for engine in ['clang19','clang21','gcc14']:
   with self.subTest(engine=engine):
    self.start('cpp',{'engine':engine});code='#include <iostream>\nint main(){int a,b;if(!(std::cin>>a>>b)){std::cout<<"EOF\\n";return 2;}std::cout<<a+b<<"\\n";return 0;}'
    self.assertEqual(self.cell(code,'10\n20\n99\n'),(0,'30\n'));compiled=self.response.get('compileCount')
    self.assertEqual(self.cell(code),(2,'EOF\n'))
    self.assertEqual(self.cell(code,'7\n8\n'),(0,'15\n'))
    self.assertEqual(self.response.get('compileCount'),compiled)
 def test_c_scanf(self):
  self.start('c');code='#include <stdio.h>\nint main(){int a,b;if(scanf("%d%d",&a,&b)!=2)return 2;printf("%d\\n",a+b);return 0;}'
  self.assertEqual(self.cell(code,'4\n5\n'),(0,'9\n'));self.assertEqual(self.cell(code)[0],2);self.assertEqual(self.cell(code,'8\n9\n'),(0,'17\n'))
 def test_java_scanner(self):
  self.start('java');code='class Main {public static void main(String[] args){java.util.Scanner scanner=new java.util.Scanner(System.in);System.out.println(scanner.nextLine());}}'
  self.assertEqual(self.cell(code,'first\n'),(0,'first\n'));self.assertEqual(self.cell(code)[0],1);self.assertEqual(self.cell(code,'second\n'),(0,'second\n'))
 def test_javascript_read_fd_zero(self):
  self.start('javascript');code='console.log(require("fs").readFileSync(0,"utf8").trim())'
  self.assertEqual(self.cell(code,'one\ntwo\n'),(0,'one\ntwo\n'));self.assertEqual(self.cell(code),(0,'\n'));self.assertEqual(self.cell(code,'three\n'),(0,'three\n'))
 def test_shell_read_is_separate_from_control(self):
  self.start('bash');code='read -r value && printf "%s\\n" "$value"'
  self.assertEqual(self.cell(code,'hello\nunused\n'),(0,'hello\n'));self.assertEqual(self.cell(code)[0],1);self.assertEqual(self.cell(code,'again\n'),(0,'again\n'))
if __name__=='__main__':unittest.main()
