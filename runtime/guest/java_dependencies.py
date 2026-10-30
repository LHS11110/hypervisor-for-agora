"""Unprivileged Maven resolver. Invoked only inside the canvas VM job cgroup."""
import importlib.util,json,os,shutil,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
spec=importlib.util.spec_from_file_location('options',Path(__file__).with_name('runtime_options.py'));options=importlib.util.module_from_spec(spec);spec.loader.exec_module(options)

def install(config):
 config=options.normalize('java',config)
 target=Path(options.java_dependency_path(config));directory=target.parent
 if target.exists():
  paths=target.read_text().strip().split(os.pathsep)
  if all(Path(path).is_file() for path in paths if path):return {'installed':True,'cached':True,'jarCount':len([p for p in paths if p]),'output':'Java libraries are already installed.'}
 directory.mkdir(parents=True,exist_ok=True)
 repository=Path('/workspace/.m2/repository');repository.mkdir(parents=True,exist_ok=True)
 seed=Path('/opt/agora/maven/repository')
 if seed.exists():
  for source in seed.rglob('*'):
   dest=repository/source.relative_to(seed)
   if source.is_file() and not dest.exists():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
 project=ET.Element('project');ET.SubElement(project,'modelVersion').text='4.0.0'
 for key,value in [('groupId','agora.canvas'),('artifactId','dependencies'),('version','1')]:ET.SubElement(project,key).text=value
 dependencies=ET.SubElement(project,'dependencies')
 for coordinate in config['dependencies']:
  dependency=ET.SubElement(dependencies,'dependency')
  for key,value in zip(('groupId','artifactId','version'),coordinate.split(':')):ET.SubElement(dependency,key).text=value
 ET.ElementTree(project).write(directory/'pom.xml',encoding='utf-8',xml_declaration=True)
 settings=directory/'settings.xml'
 settings.write_text('<settings><mirrors><mirror><id>central</id><mirrorOf>*</mirrorOf><url>https://repo.maven.apache.org/maven2</url></mirror></mirrors></settings>')
 temporary=directory/'classpath.next';temporary.unlink(missing_ok=True)
 env=dict(os.environ);env['JAVA_HOME']=options.JDK[config['engine']];env['MAVEN_OPTS']='-Xmx192m -XX:+UseSerialGC -XX:ActiveProcessorCount=2 -Djava.io.tmpdir=/workspace'
 command=['/usr/bin/mvn','--batch-mode','--no-transfer-progress','--settings',str(settings),'-f',str(directory/'pom.xml'),'-Dmaven.repo.local='+str(repository),'-Dmaven.artifact.threads=2','org.apache.maven.plugins:maven-dependency-plugin:3.11.0:build-classpath','-DincludeScope=runtime','-Dmdep.outputFile='+str(temporary)]
 log=directory/'install.log'
 with log.open('wb') as output:
  try:result=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,timeout=160)
  except subprocess.TimeoutExpired:return {'installed':False,'output':'Java dependency download timed out. Retry the installation.'}
 with log.open('rb') as output:output.seek(max(0,log.stat().st_size-12000));message=output.read().decode('utf-8','replace')
 if result.returncode!=0:return {'installed':False,'output':message}
 if not temporary.exists():temporary.write_text('')
 paths=[path for path in temporary.read_text().strip().split(os.pathsep) if path]
 if any(not Path(path).is_file() or not Path(path).resolve().is_relative_to(repository.resolve()) for path in paths):raise ValueError('Invalid resolved Java classpath')
 temporary.replace(target)
 return {'installed':True,'cached':False,'jarCount':len(paths),'output':message}

if __name__=='__main__':
 try:result=install(json.loads(sys.argv[1]))
 except Exception as error:result={'installed':False,'output':str(error)[:1000]}
 print(json.dumps(result),flush=True)
