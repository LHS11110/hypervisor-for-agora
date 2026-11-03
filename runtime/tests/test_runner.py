import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('runner', Path(__file__).resolve().parents[1] / 'guest/runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RequestValidationTests(unittest.TestCase):
    def test_supported_languages(self):
        for language in ('python', 'javascript', 'bash', 'c', 'cpp', 'java'):
            self.assertEqual(runner.validate({'language': language, 'code': 'example'}), (language, 'example'))

    def test_stdin_is_optional_bounded_utf8_text(self):
        self.assertEqual(runner.validate({'language':'python','code':'input()','stdin':'한글\n'}),('python','input()'))
        for value in [None, 123, '한'*22000]:
            with self.assertRaises(ValueError):runner.validate({'language':'python','code':'','stdin':value})

    def test_rejects_unknown_language_extra_fields_and_large_utf8(self):
        for request in ({'language': 'ruby', 'code': ''},
                        {'language': 'bash', 'code': '', 'command': 'unexpected'},
                        {'language': 'python', 'code': '한' * 22000}):
            with self.assertRaises(ValueError):
                runner.validate(request)


class Python314CommandTests(unittest.TestCase):
    def test_python314_uses_isolated_packages_and_matching_shared_library(self):
        command,env=runner.worker_command('python',{'engine':'py314'},'3')
        self.assertEqual(command[0],'/opt/python/3.14/bin/python3.14')
        self.assertEqual(env['PYTHONPATH'],'/workspace/.python/3.14')
        self.assertEqual(env['PIP_TARGET'],'/workspace/.python/3.14')
        self.assertEqual(env['LD_LIBRARY_PATH'],'/opt/python/3.14/lib')

class Python315CommandTests(unittest.TestCase):
    def test_python315_uses_separate_packages_and_matching_shared_library(self):
        command,env=runner.worker_command('python',{'engine':'py315'},'3')
        self.assertEqual(command[0],'/opt/python/3.15/bin/python3.15')
        self.assertEqual(env['PYTHONPATH'],'/workspace/.python/3.15')
        self.assertEqual(env['PIP_TARGET'],'/workspace/.python/3.15')
        self.assertEqual(env['LD_LIBRARY_PATH'],'/opt/python/3.15/lib')

if __name__ == '__main__':
    unittest.main()
