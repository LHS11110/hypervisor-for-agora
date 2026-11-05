"""Jupyter messaging client. Runs as the workspace user inside the canvas VM."""
import json,os,socket,sys,time,queue
from jupyter_client import KernelManager
control=socket.socket(fileno=int(sys.argv[1]));kernels={};access_times={}
LIMIT=262144

def send(value):control.sendall(json.dumps(value,ensure_ascii=True).encode()+b'\n')
def stop(key):
 access_times.pop(key,None)
 entry=kernels.pop(key,None)
 if entry:
  km,kc=entry
  try:kc.stop_channels();km.shutdown_kernel(now=True)
  except Exception:pass

def execute(request):
 key=request['notebook']
 if request['operation']=='reset':stop(key);return {'outputs':[],'executionCount':None,'exitCode':0,'reset':True}
 if key not in kernels:
  if len(kernels)>=4:stop(min(access_times,key=access_times.get))
  km=KernelManager(kernel_name='python3');km.kernel_spec.argv=[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}']
  try:
   km.start_kernel(cwd='/workspace',env={**os.environ,'MPLBACKEND':'module://matplotlib_inline.backend_inline'})
   kc=km.client();kc.start_channels();kc.wait_for_ready(timeout=20);kernels[key]=(km,kc)
  except BaseException:
   try:km.shutdown_kernel(now=True)
   except Exception:pass
   raise
 km,kc=kernels[key];access_times[key]=time.monotonic()
 message=kc.execute(request['code'],allow_stdin=False,stop_on_error=True);outputs=[];count=None;exit_code=0;used=0;deadline=time.monotonic()+15;truncated=False
 while True:
  remaining=deadline-time.monotonic()
  if remaining<=0:
   try:km.interrupt_kernel()
   finally:stop(key)
   return {'outputs':outputs,'executionCount':count,'exitCode':1,'timedOut':True,'error':'15초 실행 제한을 초과해 이 노트북 커널을 초기화했습니다.'}
  try:event=kc.get_iopub_msg(timeout=min(.25,remaining))
  except queue.Empty:continue
  if event.get('parent_header',{}).get('msg_id')!=message:continue
  kind=event['header']['msg_type'];content=event['content']
  if kind=='status' and content.get('execution_state')=='idle':break
  if kind=='execute_input':count=content.get('execution_count')
  if kind=='clear_output':outputs=[];used=0;continue
  output=None
  if kind=='stream':output={'output_type':'stream','name':content.get('name','stdout'),'text':content.get('text','')}
  elif kind in ('execute_result','display_data','update_display_data'):
   data={k:v for k,v in content.get('data',{}).items() if k in ('text/plain','text/html','image/png','image/jpeg','image/svg+xml') and isinstance(v,str)}
   output={'output_type':'display_data' if kind=='update_display_data' else kind,'data':data,'metadata':{}}
   if kind=='execute_result':output['execution_count']=content.get('execution_count')
   if kind=='update_display_data' and outputs:outputs.pop()
  elif kind=='error':exit_code=1;output={'output_type':'error','ename':content.get('ename','Error'),'evalue':content.get('evalue',''),'traceback':content.get('traceback',[])}
  if output:
   size=len(json.dumps(output,ensure_ascii=True).encode())
   if used+size<=LIMIT and len(outputs)<128:outputs.append(output);used+=size
   else:truncated=True
 return {'outputs':outputs,'executionCount':count,'exitCode':exit_code,'timedOut':False,'truncated':truncated}

with control.makefile('rb') as reader:
 for line in reader:
  try:send(execute(json.loads(line)))
  except Exception as error:send({'exitCode':1,'outputs':[],'error':str(error)[:512]})
for key in list(kernels):stop(key)
