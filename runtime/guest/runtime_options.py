"""Shared, bounded runtime catalogue and validation for host and guest."""
import copy
import hashlib
import json
import re
from pathlib import Path


def select(label, values): return {'label': label, 'type': 'select', 'values': values}
def toggle(label): return {'label': label, 'type': 'boolean'}
def engine(label, mode='persistent'): return {'label': label, 'mode': mode}

NATIVE_FIELDS = {
 'optimization': select('최적화', ['0','1','2','3']),
 'warnings': toggle('추가 경고'), 'warningsAsErrors': toggle('경고를 오류로 처리'),
}
CATALOG = {
 'python': {'label':'Python', 'engines':{'py311':engine('CPython 3.11'),'py312':engine('CPython 3.12'),'py313':engine('CPython 3.13'),'py314':engine('CPython 3.14'),'py315':engine('CPython 3.15')},
  'defaults':{'engine':'py312','optimize':'0','writeBytecode':True,'warnings':'default'},
  'fields':{'optimize':select('최적화',['0','1','2']),'writeBytecode':toggle('바이트코드 파일 저장'),'warnings':select('경고 처리',['default','error','ignore'])}},
 'cpp': {'label':'C++', 'engines':{'clang19':engine('Clang 19 · JIT'),'clang21':engine('Clang 21 · JIT'),'gcc14':engine('GCC 14 · 프로그램 실행','program')},
  'defaults':{'engine':'clang19','standard':'c++17','optimization':'0','warnings':False,'warningsAsErrors':False,'exceptions':True,'rtti':True},
  'fields':{'standard':select('언어 표준',['c++11','c++14','c++17','c++20','c++23']),**NATIVE_FIELDS,'exceptions':toggle('예외 처리'),'rtti':toggle('런타임 타입 정보')}},
 'c': {'label':'C', 'engines':{'clang19':engine('Clang 19 · JIT'),'clang21':engine('Clang 21 · JIT'),'gcc14':engine('GCC 14 · 프로그램 실행','program')},
  'defaults':{'engine':'clang19','standard':'c17','optimization':'0','warnings':False,'warningsAsErrors':False},
  'fields':{'standard':select('언어 표준',['c11','c17','c23']),**NATIVE_FIELDS}},
 'java': {'label':'Java', 'engines':{'jdk17':engine('Temurin JDK 17'),'jdk21':engine('Temurin JDK 21'),'jdk25':engine('Temurin JDK 25')},
  'defaults':{'engine':'jdk21','release':'21','heapMiB':'256','assertions':False,'preview':False,'dependencies':[]},
  'fields':{'release':select('대상 Java 버전',['17','21','25']),'heapMiB':select('최대 힙 메모리 (MiB)',['128','256','512']),'assertions':toggle('assert 활성화'),'preview':toggle('미리보기 기능'),'dependencies':{'label':'Maven 라이브러리','type':'dependencies'}}},
 'javascript': {'label':'JavaScript', 'engines':{'node20':engine('Node.js 20'),'node22':engine('Node.js 22'),'node24':engine('Node.js 24')},
  'defaults':{'engine':'node20','strict':False,'warnings':True},'fields':{'strict':toggle('엄격 모드'),'warnings':toggle('경고 표시')}},
 'bash': {'label':'Shell', 'engines':{'bash52':engine('Bash 5.2')},
  'defaults':{'engine':'bash52','errexit':False,'nounset':False,'pipefail':False},
  'fields':{'errexit':toggle('명령 실패 시 종료 (-e)'),'nounset':toggle('미정의 변수 사용 시 종료 (-u)'),'pipefail':toggle('파이프라인 실패 감지')}},
}
# Explicit build/run separation; legacy settings retain persistent kernels.
for language, entry in CATALOG.items():
 entry['defaults']['executionMode']='interactive'
 entry['fields']['executionMode']={**select('실행 방식',['interactive','precompiled']), 'labels':{'interactive':'기존 실행 · 환경 유지','precompiled':'사전 빌드 후 실행'}}

PYTHON = {'py311':'/opt/python/3.11/bin/python3.11','py312':'/usr/local/bin/python3.12','py313':'/opt/python/3.13/bin/python3.13','py314':'/opt/python/3.14/bin/python3.14','py315':'/opt/python/3.15/bin/python3.15'}
NODE = {'node20':'/usr/bin/node','node22':'/opt/node/22/bin/node','node24':'/opt/node/24/bin/node'}
JDK = {f'jdk{version}':f'/opt/jdk/{version}' for version in (17,21,25)}


def normalize(language, config=None):
 if language not in CATALOG: raise ValueError('지원하지 않는 실행 언어입니다.')
 if config is None: config={}
 if not isinstance(config,dict): raise ValueError('실행 환경 설정은 객체여야 합니다.')
 catalog=CATALOG[language]
 if set(config)-set(catalog['defaults']): raise ValueError('지원하지 않는 실행 옵션입니다.')
 result={**catalog['defaults'],**config}
 if result['engine'] not in catalog['engines']: raise ValueError('설치되지 않은 실행 환경입니다.')
 if language=='java' and 'engine' in config and 'release' not in config: result['release']=result['engine'][3:]
 for key,field in catalog['fields'].items():
  value=result[key]
  if field['type']=='dependencies':
   result[key]=java_dependencies(value)
  elif field['type']=='boolean':
   if type(value) is not bool: raise ValueError('실행 옵션의 참/거짓 값이 올바르지 않습니다.')
  elif not isinstance(value,str) or value not in field['values']: raise ValueError('지원하지 않는 실행 옵션 값입니다.')
 if language=='java':
  version=int(result['engine'][3:]);release=int(result['release'])
  if release>version: raise ValueError('대상 Java 버전은 선택한 JDK보다 높을 수 없습니다.')
  if result['preview'] and release!=version: raise ValueError('미리보기 기능은 JDK와 대상 Java 버전이 같아야 합니다.')
 return result


def defaults(): return {language:normalize(language) for language in CATALOG}
def catalogue(): return copy.deepcopy(CATALOG)


def native_flags(language, config):
 flags=['-std='+config['standard'],'-O'+config['optimization']]
 if config['warnings']: flags+=['-Wall','-Wextra']
 if config['warningsAsErrors']: flags+=['-Werror']
 if language=='cpp':
  if not config['exceptions']:flags+=['-fno-exceptions']
  if not config['rtti']:flags+=['-fno-rtti']
 return flags


# Fixed templates: users cannot supply VMM arguments or unbounded limits.
RESOURCE_TEMPLATES = {
 'small': {'label':'소형', 'vcpus':1, 'memoryMiB':1024, 'workerMemoryMiB':768, 'pids':64},
 'medium': {'label':'표준', 'vcpus':2, 'memoryMiB':2048, 'workerMemoryMiB':1536, 'pids':128},
 'large': {'label':'고성능', 'vcpus':4, 'memoryMiB':4096, 'workerMemoryMiB':3072, 'pids':256},
}
DEFAULT_RESOURCE_TEMPLATE = 'small'

def resource_template(key):
 if not isinstance(key,str) or key not in RESOURCE_TEMPLATES: raise ValueError('지원하지 않는 VM 리소스 템플릿입니다.')
 return copy.deepcopy(RESOURCE_TEMPLATES[key])


DISK_SIZES_GIB = [6,12,24]
DEFAULT_DISK_GIB = 6

def disk_size(value):
 if type(value) is not int or value not in DISK_SIZES_GIB: raise ValueError('지원하지 않는 VM 디스크 용량입니다.')
 return value


def java_dependencies(value):
 if not isinstance(value,list) or len(value)>20: raise ValueError('Java 라이브러리는 최대 20개까지 지정할 수 있습니다.')
 result=[];groups=set()
 for coordinate in value:
  if not isinstance(coordinate,str) or len(coordinate)>240 or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*:[A-Za-z0-9_][A-Za-z0-9_.-]*:[A-Za-z0-9_][A-Za-z0-9_.+-]*',coordinate):
   raise ValueError('Java 라이브러리는 group:artifact:version 형식으로 입력하세요.')
  group,artifact,version=coordinate.split(':')
  if any(part in ('.','..') for part in (group,artifact,version)) or version.upper() in ('LATEST','RELEASE') or 'SNAPSHOT' in version.upper(): raise ValueError('Java 라이브러리는 고정된 릴리스 버전을 지정하세요.')
  key=(group,artifact)
  if key in groups:raise ValueError('같은 Java 라이브러리를 중복 지정할 수 없습니다.')
  groups.add(key);result.append(coordinate)
 return sorted(result)


def java_dependency_path(config):
 data=json.dumps([1,config['engine'],config['dependencies']],separators=(',',':'))
 return '/workspace/.java-dependencies/'+hashlib.sha256(data.encode()).hexdigest()+'/classpath.txt'


def python_package_directory(engine):
 if engine not in PYTHON:raise ValueError('지원하지 않는 Python 실행 환경입니다.')
 return '/workspace/.python'+('' if engine=='py312' else '/3.'+engine[3:])


def python_environment(engine, environment):
 """Align command-line tools and packages, keeping user-added search paths."""
 package=python_package_directory(engine);binary=PYTHON[engine]
 result=dict(environment)
 packages={python_package_directory(key) for key in PYTHON}
 libraries={str(Path(value).parent.parent/'lib') for value in PYTHON.values()}
 def paths(value,removed):return [entry for entry in value.split(':') if entry and entry not in removed]
 result['PATH']=':'.join(['/opt/agora/python-tools',package+'/bin',*paths(result.get('PATH','/usr/local/bin:/usr/bin:/bin'),{'/opt/agora/python-tools',*(p+'/bin' for p in packages)})])
 result['PYTHONPATH']=':'.join([package,*paths(result.get('PYTHONPATH',''),packages)])
 result['LD_LIBRARY_PATH']=':'.join([str(Path(binary).parent.parent/'lib'),*paths(result.get('LD_LIBRARY_PATH',''),libraries)])
 result['PIP_TARGET']=package;result['AGORA_PYTHON_ENGINE']=engine
 return result
