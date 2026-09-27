#!/usr/bin/env python3
"""Install this standalone stdlib worker on an authorized Linux SSH host."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

def kill_owned(child):
    if os.name!='nt':
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    elif child.poll() is None:child.kill()

def bounded_short(argv,root,timeout):
    child=subprocess.Popen(argv,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=os.name!='nt')
    buffers=[bytearray(),bytearray()];truncated=[False,False]
    def drain(pipe,index):
        with pipe:
            while data:=pipe.read1(4096):
                room=8000-len(buffers[index])
                if len(data)>room:truncated[index]=True
                buffers[index].extend(data[:max(0,room)])
    threads=[threading.Thread(target=drain,args=(child.stdout,0),daemon=True),threading.Thread(target=drain,args=(child.stderr,1),daemon=True)]
    for thread in threads:thread.start()
    try:code=child.wait(timeout=timeout)
    finally:
        kill_owned(child);child.wait()
        for thread in threads:thread.join(timeout=3)
    if any(t.is_alive() for t in threads):raise RuntimeError('owned output streams did not close')
    return {'stdout':buffers[0].decode(errors='replace'),'stderr':buffers[1].decode(errors='replace'),'exit_code':code,'truncated':any(truncated)}

def atomic(path,value):
    fd,name=tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(value,f)
    os.replace(name,path)

def safe(root,relative):
    p=Path(relative)
    if p.is_absolute() or '..' in p.parts or not relative: raise PermissionError('relative path required')
    current=root
    for part in p.parts:
        current=current/part
        if current.is_symlink(): raise PermissionError('symlinks forbidden')
    resolved=current.resolve()
    if resolved!=root and root not in resolved.parents: raise PermissionError('path outside remote root')
    return resolved

def worker(folder):
    req=json.loads((folder/'request.json').read_text())
    if (folder/'cancel').exists():
        atomic(folder/'exit.json',{'status':'cancelled','exit_code':None,'truncated':False});return
    lock=threading.Lock(); total=0; truncated=False
    def drain(pipe,stream):
        nonlocal total,truncated
        with pipe:
            while data:=pipe.read1(4096):
                with lock:
                    room=max(0,req['max_log_bytes']-total)
                    if len(data)>room: truncated=True
                    data=data[:room]
                    if data:
                        with (folder/'output.bin').open('ab') as f: f.write(struct.pack('!BI',stream,len(data))+data)
                        total+=len(data)
    try:
        child=subprocess.Popen(req['argv'],cwd=req['root'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=os.name!='nt')
        threads=[threading.Thread(target=drain,args=(child.stdout,1),daemon=True),threading.Thread(target=drain,args=(child.stderr,2),daemon=True)]
        for t in threads:t.start()
        end=time.monotonic()+req['timeout']; status=None
        while child.poll() is None:
            if (folder/'cancel').exists() or time.monotonic()>end:
                status='cancelled' if (folder/'cancel').exists() else 'failed'
                kill_owned(child)
                break
            time.sleep(.05)
        code=child.wait()
        kill_owned(child)
        for t in threads:t.join(timeout=3)
        if any(t.is_alive() for t in threads):raise RuntimeError('owned output streams did not close')
        atomic(folder/'exit.json',{'status':status or ('completed' if code==0 else 'failed'),'exit_code':code,'truncated':truncated})
    except BaseException as e:
        if 'child' in locals():kill_owned(child);child.wait()
        atomic(folder/'exit.json',{'status':'failed','exit_code':None,'error':str(e)})

def execute(operation,p):
    root=Path(p['root']).resolve(strict=True)
    if operation in {'read','write'}:
        path=safe(root,p['path'])
        if not path.parent.is_dir(): raise ValueError('parent directory missing')
        data=path.read_bytes() if path.exists() else None
        if data is not None and (len(data)>2097152 or b'\x00' in data): raise ValueError('binary or oversized file')
        digest=hashlib.sha256(data).hexdigest() if data is not None else ''
        if operation=='read':
            if data is None: raise FileNotFoundError('file missing')
            if p['start_line']<1 or not 1<=p['max_lines']<=1000: raise ValueError('invalid bounds')
            text=''.join(data.decode().splitlines(keepends=True)[p['start_line']-1:p['start_line']-1+p['max_lines']])
            limited=text.encode()[:16000].decode(errors='ignore')
            return {'text':limited,'sha256':digest,'truncated':limited!=text}
        if digest!=p['sha256']: raise ValueError('SHA precondition failed')
        new=p['content'].encode()
        fd,name=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(new); f.flush();os.fsync(f.fileno())
            if safe(root,p['path'])!=path or (path.read_bytes() if path.exists() else None)!=data: raise ValueError('file changed before commit')
            os.replace(name,path)
        finally: Path(name).unlink(missing_ok=True)
        return {'sha256':hashlib.sha256(new).hexdigest()}
    if operation=='run':
        return bounded_short(p['argv'],root,min(p['timeout'],30))
    jid=p['job_id']
    if not re.fullmatch('[a-f0-9]{64}',jid):raise ValueError('invalid job ID')
    jobs=safe(root,'.bridge-jobs'); jobs.mkdir(exist_ok=True,mode=0o700)
    folder=safe(root,f'.bridge-jobs/{jid}')
    if operation=='cancel':
        folder.mkdir(exist_ok=True,mode=0o700);(folder/'cancel').touch()
        if not (folder/'request.json').exists():atomic(folder/'exit.json',{'status':'cancelled','exit_code':None,'truncated':False})
        return execute('query',p)
    if operation=='start':
        try: folder.mkdir(mode=0o700)
        except FileExistsError: return execute('query',p)
        atomic(folder/'request.json',p)
        if (folder/'cancel').exists():return execute('query',p)
        with (folder/'runner.log').open('ab') as out:
            subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'worker',str(folder)],stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
        atomic(folder/'accepted.json',{'accepted':True})
        return {'status':'running','id':jid}
    if not folder.exists(): return {'status':'unknown','exit_code':None}
    state=json.loads((folder/'exit.json').read_text()) if (folder/'exit.json').exists() else {'status':'running' if (folder/'accepted.json').exists() else 'unknown','exit_code':None}
    if operation=='query':return state
    if operation=='poll':
        cursor=p['cursor']; outputs={1:bytearray(),2:bytearray()}; total=0
        if cursor<0: raise ValueError('invalid cursor')
        if (folder/'output.bin').exists():
            with (folder/'output.bin').open('rb') as f:
                f.seek(cursor)
                while total<16000:
                    header=f.read(5)
                    if len(header)!=5:break
                    stream,size=struct.unpack('!BI',header)
                    if stream not in outputs or size>4096:raise ValueError('invalid cursor')
                    if total+size>16000:break
                    data=f.read(size)
                    if len(data)!=size:break
                    outputs[stream].extend(data);total+=size;cursor=f.tell()
        return {'stdout':outputs[1].decode(errors='replace'),'stderr':outputs[2].decode(errors='replace'),'next_cursor':cursor,'status':state['status'],'exit_code':state['exit_code'],'truncated':state.get('truncated',False)}
    raise ValueError('unknown operation')

if __name__=='__main__':
    if sys.argv[1]=='worker':worker(Path(sys.argv[2]))
    else:
        try: result=execute(sys.argv[1],json.loads(base64.urlsafe_b64decode(sys.argv[2])))
        except Exception as e: result={'error':str(e)}
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8')+b'\n')
