"""Root-only comparative benchmark in an isolated disposable canvas VM.
Measures manager round trips (includes VSOCK/kernel/output, excludes HTTP/browser).
"""
import argparse,json,math,platform,shutil,socket,statistics,subprocess,time
from pathlib import Path
CID=2147483015
ROOT=Path('/var/lib/agora-runtime')

def request(data):
    start=time.perf_counter_ns()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
        stream.settimeout(100);stream.connect('/run/agora-runtime/manager.sock')
        stream.sendall(json.dumps({'canvasId':CID,**data}).encode()+b'\n')
        result=json.loads(stream.makefile('rb').readline(500000))
    return result,(time.perf_counter_ns()-start)/1e6

def source(language,heavy,round_number):
    n=300 if heavy else 0
    if language=='python':
        return f'# round {round_number}\n'+''.join(f'def f{i}(x): return x+{i}\n' for i in range(n))+'total=0\nfor i in range(100000): total+=i\nprint(total)'
    if language=='javascript':
        return f'// round {round_number}\n'+''.join(f'function f{i}(x){{return x+{i};}}\n' for i in range(n))+'var total=0;for(var i=0;i<100000;i++)total+=i;console.log(total);'
    if language in ('c','cpp'):
        return f'// round {round_number}\n#include <stdio.h>\n'+''.join(f'int f{i}(int x){{return x+{i};}}\n' for i in range(n))+'int main(){long long total=0;for(int i=0;i<100000;i++)total+=i;printf("%lld\\n",total);return 0;}'
    if language=='java':
        return f'// round {round_number}\npublic class Main {{\n'+''.join(f'static int f{i}(int x){{return x+{i};}}\n' for i in range(n))+'public static void main(String[] args){long total=0;for(int i=0;i<100000;i++)total+=i;System.out.println(total);}}'
    return f'# round {round_number}\n'+''.join(f'f{i}() {{ echo $(( $1 + {i} )); }}\n' for i in range(n))+'total=0\nfor ((i=0;i<100000;i++)); do ((total+=i)); done\nprintf "%s\\n" "$total"'

def successful(result,expect_output=False):
    assert result.get('exitCode')==0 and not result.get('timedOut') and not result.get('environmentReset') and not result.get('truncated'),result
    if expect_output:assert result['output']=='4999950000\n',result

def stats(values):
    values=sorted(values)
    return {'median_ms':statistics.median(values),'mean_ms':statistics.mean(values),'p95_ms':values[math.ceil(.95*len(values))-1],'samples_ms':values}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--repeats',type=int,default=8)
    args=parser.parse_args()
    directory=ROOT/'canvases'/str(CID);settings=ROOT/'settings'/f'{CID}.json';launch=ROOT/'launch'/f'{CID}.env'
    assert not directory.exists() and not settings.exists() and not launch.exists(),'Reserved canvas already exists'
    result={'metric':'host manager request round-trip milliseconds; HTTP/browser excluded','rounds':args.rounds,'repeats_per_round':args.repeats,'host':platform.platform(),'load_before':Path('/proc/loadavg').read_text().strip(),'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'cases':[]}
    revision=0
    def configure(language,config):
        nonlocal revision
        response,_=request({'action':'settings_set','language':language,'config':config,'revision':revision});assert 'error' not in response,response;revision=response['revision']
    try:
        # Start the VM once; exclude VM provisioning/boot from language comparisons.
        response,boot=request({'language':'python','code':'print(4999950000)'});successful(response,True);result['vm_provision_and_python_first_ms']=boot
        snapshot,_=request({'action':'settings_get'});result['resourceTemplate']=snapshot['resourceTemplate'];result['resources']=snapshot['resourceTemplates'][snapshot['resourceTemplate']]
        for language,engine in [('python','py312'),('javascript','node20'),('c','clang19'),('cpp','clang19'),('cpp','gcc14'),('java','jdk21'),('bash','bash52')]:
            for heavy in (False,True):
                values={mode:{'first':[],'warm':[],'build':[],'prepare_and_first':[]} for mode in ('interactive','precompiled')}
                for round_number in range(args.rounds):
                    code=source(language,heavy,round_number)
                    for mode in (('interactive','precompiled') if round_number%2==0 else ('precompiled','interactive')):
                        configure(language,{'engine':engine,'executionMode':mode})
                        # reset before each mode/round; existing artifacts remain but
                        # unique source per round/workload makes each build a cache miss.
                        response,_=request({'action':'reset'});assert response.get('reset'),response
                        build_ms=0
                        if mode=='precompiled':
                            response,build_ms=request({'action':'build','language':language,'code':code});successful(response);assert '재사용' not in response['output'],response
                            values[mode]['build'].append(build_ms)
                        response,first=request({'language':language,'code':code});successful(response,True)
                        values[mode]['first'].append(first);values[mode]['prepare_and_first'].append(first+build_ms)
                        for _ in range(args.repeats):
                            response,elapsed=request({'language':language,'code':code});successful(response,True);values[mode]['warm'].append(elapsed)
                case={'language':language,'engine':engine,'workload':'300 definitions + 100k sum' if heavy else '100k sum','source_bytes':len(code.encode()),'modes':{mode:{key:stats(samples) for key,samples in metrics.items() if samples} for mode,metrics in values.items()}}
                before=case['modes']['interactive']['warm']['median_ms'];after=case['modes']['precompiled']['warm']['median_ms'];case['warm_speedup']=before/after
                result['cases'].append(case);Path(args.output).write_text(json.dumps(result,indent=2));print(language,engine,case['workload'],f'warm {before:.2f} -> {after:.2f} ms ({before/after:.2f}x)',flush=True)
        result['load_after']=Path('/proc/loadavg').read_text().strip();result['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());Path(args.output).write_text(json.dumps(result,indent=2))
    finally:
        subprocess.run(['systemctl','stop',f'agora-runtime@{CID}.service'],check=True)
        subprocess.run(['systemctl','stop',f'agora-runtime-network@{CID}.service'],check=True)
        if directory.exists():shutil.rmtree(directory)
        settings.unlink(missing_ok=True);launch.unlink(missing_ok=True)
        subprocess.run(['systemctl','revert',f'agora-runtime@{CID}.service'],check=True,stdout=subprocess.DEVNULL)

if __name__=='__main__':main()
