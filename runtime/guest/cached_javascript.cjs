// V8 bytecode cache, not a native AOT executable. Runtime JIT may still occur.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const [action,directory,strict]=process.argv.slice(2);
const filename=path.join(directory,'source.js');
const code=(strict==='true'?'"use strict";\n':'')+fs.readFileSync(filename,'utf8');
if(action==='build') {
  const script=new vm.Script(code,{filename});
  fs.writeFileSync(path.join(directory,'program.cache'),script.createCachedData());
} else {
  const script=new vm.Script(code,{filename,cachedData:fs.readFileSync(path.join(directory,'program.cache'))});
  if(script.cachedDataRejected)throw new Error('V8 캐시가 유효하지 않습니다. 다시 빌드하세요.');
  script.runInNewContext({require,console,process,Buffer,setTimeout,clearTimeout,setInterval,clearInterval,URL,URLSearchParams,__filename:filename,__dirname:'/workspace'});
}
