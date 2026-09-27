"""Detached worker. Receipts survive the service process."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import psutil
from .process_scope import OwnedProcess

def atomic_json(path, value):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value),encoding='utf-8')
    os.replace(tmp,path)

def stop_tree(pid):
    try:
        proc=psutil.Process(pid)
        children=proc.children(recursive=True)
        for child in reversed(children):
            try: child.kill()
            except psutil.NoSuchProcess: pass
        proc.kill()
        psutil.wait_procs(children+[proc],timeout=3)
    except psutil.NoSuchProcess: pass

def run(folder):
    spec=json.loads((folder/'request.json').read_text(encoding='utf-8'))
    identity={'pid':os.getpid(),'created_at':psutil.Process().create_time()}
    atomic_json(folder/'identity.json',identity)
    if (folder/'cancel').exists():
        atomic_json(folder/'exit.json',{**identity,'exit_code':None,'status':'cancelled','truncated':False});return
    lock=threading.Lock()
    logged=0
    truncated=False
    def drain(pipe, stream):
        nonlocal logged,truncated
        with pipe:
            while data:=pipe.read1(4096):
                with lock:
                    remaining=max(0,spec['max_log_bytes']-logged)
                    if len(data)>remaining:truncated=True
                    data=data[:remaining]
                    if data:
                        with (folder/'output.bin').open('ab') as output:
                            output.write(struct.pack('!BI',stream,len(data))+data)
                        logged+=len(data)
    child=None
    try:
        owned=OwnedProcess(spec['argv'],cwd=spec['cwd'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        child=owned.process
        atomic_json(folder/'child.json',{'pid':child.pid,'created_at':psutil.Process(child.pid).create_time()})
        threads=[threading.Thread(target=drain,args=(child.stdout,1)),threading.Thread(target=drain,args=(child.stderr,2))]
        for thread in threads: thread.start()
        def feed_stdin():
            while child.poll() is None:
                for item in sorted((folder/'stdin').glob('*.json')):
                    text=json.loads(item.read_text(encoding='utf-8'))
                    try: child.stdin.write(text.encode());child.stdin.flush()
                    except (BrokenPipeError,OSError):return
                    item.unlink(missing_ok=True)
                time.sleep(.03)
        writer=threading.Thread(target=feed_stdin,daemon=True);writer.start()
        start=time.monotonic(); status=None
        while child.poll() is None:
            if (folder/'cancel').exists(): status='cancelled';owned.terminate_owned();break
            if time.monotonic()-start>spec['timeout']: status='failed';owned.terminate_owned();break
            time.sleep(.03)
        code=child.wait()
        owned.terminate_owned()
        writer.join(timeout=3)
        for thread in threads: thread.join(timeout=3)
        if any(thread.is_alive() for thread in threads):raise RuntimeError('owned streams did not close')
        atomic_json(folder/'exit.json',{**identity,'exit_code':code,
            'status':status or ('completed' if code==0 else 'failed'),'truncated':truncated})
    except BaseException as exc:
        if child is not None:owned.terminate_owned();child.wait()
        atomic_json(folder/'exit.json',{**identity,'exit_code':None,'status':'failed','error':str(exc)})

if __name__=='__main__': run(Path(sys.argv[1]))
