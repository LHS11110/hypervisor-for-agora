"""Run inside the guest image as UID 1000 (no network/capabilities)."""
import json
import os
import selectors
import socket
import subprocess
import unittest

class Kernels(unittest.TestCase):
    def cell(self, code):
        self.control.sendall(json.dumps({'code': code}).encode() + b'\n')
        reply = b''
        while not reply.endswith(b'\n'):
            chunk = self.control.recv(1024)
            if not chunk:
                raise RuntimeError('Kernel terminated: ' + self.process.stdout.read().decode())
            reply += chunk
        out = b''
        os.set_blocking(self.process.stdout.fileno(), False)
        while True:
            try:
                data = os.read(self.process.stdout.fileno(), 8192)
                if not data: break
                out += data
            except BlockingIOError: break
        self.last_response = json.loads(reply)
        return self.last_response['exitCode'], out.decode()

    def start(self, language):
        self.control, child = socket.socketpair()
        self.control.settimeout(15)
        fd = str(child.fileno())
        command = {'python': ['python3', '-u', '/usr/local/lib/python_worker.py', fd],
                   'javascript': ['node', '/usr/local/lib/javascript_worker.cjs', fd]}.get(language,
                   ['/usr/local/bin/agora-native-worker', fd, language])
        self.process = subprocess.Popen(command, pass_fds=(child.fileno(),), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, cwd='/workspace')
        child.close()
        self.addCleanup(self.close)

    def close(self):
        self.process.kill()
        self.process.wait()
        self.control.close()
        self.process.stdout.close()

    def test_python_closure_and_mutation_without_replay(self):
        self.start('python')
        self.assertEqual(self.cell('values=[]\nvalues.append(3)\ndef total():\n return sum(values)')[0], 0)
        self.assertEqual(self.cell('values.append(4)\nprint(total())'), (0, '7\n'))
        self.assertEqual(self.cell('print(total())'), (0, '7\n'))
        self.assertEqual(self.cell('raise ValueError("cell error")')[0], 1)
        self.assertEqual(self.cell('print(total())'), (0, '7\n'))

    def test_javascript_lexical_scope_and_async(self):
        self.start('javascript')
        self.assertEqual(self.cell('const values=[3]; function total(){return values.reduce((a,b)=>a+b,0)}')[0], 0)
        self.assertEqual(self.cell('values.push(4); console.log(total())'), (0, '7\n'))
        self.assertEqual(self.cell('Promise.resolve().then(()=>console.log(total()))'), (0, '7\n'))
        self.assertEqual(self.cell('throw Error("cell error")')[0], 1)
        self.assertEqual(self.cell('console.log(total())'), (0, '7\n'))

    def test_cpp_state_and_multiline_function(self):
        self.start('cpp')
        self.assertEqual(self.cell('#include <cstdio>\nint count=3;\nint total(int n) {\nreturn count+n;\n}')[0], 0)
        self.assertEqual(self.cell('count += 4; printf("%d\\n",total(2));'), (0, '9\n'))
        self.assertEqual(self.cell('printf("%d\\n",count);'), (0, '7\n'))
        self.assertEqual(self.cell('invalid syntax;')[0], 1)
        self.assertEqual(self.cell('printf("%d\\n",count);'), (0, '7\n'))

    def test_cpp_inline_assembly_across_cells(self):
        self.start('cpp')
        self.assertEqual(self.cell('#include <cstdio>\nint counter=3;\nvoid advance(){ asm volatile ("nop"); ++counter; }')[0], 0)
        self.assertEqual(self.cell('advance(); printf("%d\\n",counter);'), (0, '4\n'))
        self.assertEqual(self.cell('advance(); printf("%d\\n",counter);'), (0, '5\n'))

    def test_cpp_rerun_preserves_declarations_and_executes_statements(self):
        self.start('cpp')
        declarations = '#include <cstdio>\n#include <vector>\nstd::vector<int> values;\nint total(){int sum=0; for(int x:values)sum+=x; return sum;}'
        action = 'values.push_back(3); printf("%d\\n",total());'
        for expected in ('3\n', '6\n', '9\n'):
            self.assertEqual(self.cell(declarations), (0, ''))
            self.assertEqual(self.cell(action), (0, expected))
            self.assertEqual(self.last_response['compileCount'], 2)

    def test_cpp_full_program_runs_main_again(self):
        self.start('cpp')
        code = '#include <cstdio>\nint runs=0;\nint main(){printf("%d\\n",++runs);return 0;}'
        for expected in ('1\n', '2\n', '3\n'):
            self.assertEqual(self.cell(code), (0, expected))
            self.assertEqual(self.last_response['compileCount'], 1)

    def test_cpp_mixed_declarations_and_statements_rerun(self):
        self.start('cpp')
        code = '#include <cstdio>\nint counter=0;\nstruct Item{int x;};\nvoid advance(){asm volatile("nop");++counter;}\nadvance(); printf("%d\\n",counter);'
        for expected in ('1\n', '2\n', '3\n'):
            self.assertEqual(self.cell(code), (0, expected))
            self.assertEqual(self.last_response['compileCount'], 1)

    def test_cpp_macro_statement_rerun(self):
        self.start('cpp')
        code = '#include <cstdio>\n#define ADVANCE(x) (++(x))\nint counter=0;\nADVANCE(counter); printf("%d\\n",counter);'
        for expected in ('1\n', '2\n'):
            self.assertEqual(self.cell(code), (0, expected))
            self.assertEqual(self.last_response['compileCount'], 1)

    def test_cpp_cached_machine_code_keeps_static_and_initializes_globals_once(self):
        self.start('cpp')
        code = '#include <cstdio>\nint initCount=0;\nint initialize(){return ++initCount;}\nint value=initialize();\n{ static int calls=0; printf("%d %d\\n", initCount, ++calls); }'
        for i in range(1, 31):
            self.assertEqual(self.cell(code), (0, f'1 {i}\n'))
            self.assertEqual(self.last_response['compileCount'], 1)

    def test_cpp_edited_main_uses_new_body_and_keeps_library_state(self):
        self.start('cpp')
        self.assertEqual(self.cell('#include <cstdio>\nint counter=0;\nint add(int a,int b){return a+b;}'), (0, ''))
        first = 'int main(){printf("%d\\n",++counter);}'
        second = 'int main(int argc,char**argv){printf("%d %d\\n",++counter,add(3,4));return 0;}'
        self.assertEqual(self.cell(first), (0, '1\n'))
        self.assertEqual(self.cell(second), (0, '2 7\n'))
        self.assertEqual(self.cell(second), (0, '3 7\n'))
        count = self.last_response['compileCount']
        self.assertEqual(self.cell(first), (0, '4\n'))
        self.assertEqual(self.last_response['compileCount'], count)

    def test_cpp_failed_main_edit_preserves_previous_code_and_allows_retry(self):
        self.start('cpp')
        self.assertEqual(self.cell('#include <cstdio>\nint counter=0;'), (0, ''))
        original = 'int main(){printf("%d\\n",++counter);return 0;}'
        edited = 'int main(){printf("%d %d\\n",++counter,add(3,4));return 0;}'
        self.assertEqual(self.cell(original), (0, '1\n'))
        for _ in range(3):
            status, output = self.cell(edited)
            self.assertEqual(status, 1)
            self.assertIn("undeclared identifier 'add'", output)
            self.assertNotIn("redefinition of 'main'", output)
        self.assertEqual(self.cell(original), (0, '2\n'))
        self.assertEqual(self.cell('int add(int a,int b){return a+b;}'), (0, ''))
        self.assertEqual(self.cell(edited), (0, '3 7\n'))

    def test_cpp_main_body_edit_keeps_globals_in_the_same_cell(self):
        self.start('cpp')
        prefix = '#include <cstdio>\nint counter=0;\nint add(int a,int b){return a+b;}\n'
        first = prefix + 'int main(){printf("%d\\n",++counter);}'
        second = prefix + 'int main(){printf("%d %d\\n",++counter,add(3,4));}'
        self.assertEqual(self.cell(first), (0, '1\n'))
        self.assertEqual(self.cell(second), (0, '2 7\n'))
        self.assertEqual(self.cell(second), (0, '3 7\n'))
        count = self.last_response['compileCount']
        self.assertEqual(self.cell(first), (0, '4\n'))
        self.assertEqual(self.last_response['compileCount'], count)

    def test_cpp_main_prototype_and_edit_keep_normal_main_semantics(self):
        self.start('cpp')
        self.assertEqual(self.cell('#include <cstdio>\nint main();\nint main(){puts("first");}'), (0, 'first\n'))
        self.assertEqual(self.cell('int main(){puts("edited");}'), (0, 'edited\n'))
        self.assertEqual(self.cell('int main(){return missing_name;}')[0], 1)
        self.assertEqual(self.cell('int main(){puts("recovered");}'), (0, 'recovered\n'))

    def test_c_state_and_conventional_main(self):
        self.start('c')
        self.assertEqual(self.cell('#include <stdio.h>\nint count=3;\nint total(int n){ return count+n; }')[0], 0)
        self.assertEqual(self.cell('count += 4; printf("%d\\n",total(2));'), (0, '9\n'))
        self.assertEqual(self.cell('int main(void){printf("%d\\n",count); return 0;}'), (0, '7\n'))

if __name__ == '__main__':
    if os.getuid() == 0: raise SystemExit('Run as the unprivileged guest user')
    unittest.main()
