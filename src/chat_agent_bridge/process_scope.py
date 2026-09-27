"""Own a whole process group, including descendants and bounded output."""
import ctypes
from dataclasses import dataclass
import os
import signal
import subprocess
import threading
import psutil

class OwnedProcess:
    def __init__(self,argv,*,allow_breakaway=False,**kwargs):
        self.job=None
        if allow_breakaway:
            kwargs['env'] = dict(kwargs.get('env', os.environ), CHAT_AGENT_BRIDGE_DETACHED_RUNNERS='1')
        if os.name!='nt':
            self.process=subprocess.Popen(argv,start_new_session=True,**kwargs);return
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',ctypes.c_ulonglong*6),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        create=kernel.CreateJobObjectW;create.argtypes=[ctypes.c_void_p,w.LPCWSTR];create.restype=w.HANDLE
        setinfo=kernel.SetInformationJobObject;setinfo.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];setinfo.restype=w.BOOL
        assign=kernel.AssignProcessToJobObject;assign.argtypes=[w.HANDLE,w.HANDLE];assign.restype=w.BOOL
        self.close=kernel.CloseHandle;self.close.argtypes=[w.HANDLE];self.close.restype=w.BOOL
        self.job=create(None,None)
        if not self.job:raise OSError(ctypes.get_last_error(),'CreateJobObject failed')
        info=Extended();info.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if allow_breakaway: info.basic.flags |= 0x800 # JOB_OBJECT_LIMIT_BREAKAWAY_OK
        try:
            if not setinfo(self.job,9,ctypes.byref(info),ctypes.sizeof(info)):raise OSError(ctypes.get_last_error(),'SetInformationJobObject failed')
            self.process=subprocess.Popen(argv,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP|0x4,**kwargs)
            if not assign(self.job,int(self.process._handle)):raise OSError(ctypes.get_last_error(),'AssignProcessToJobObject failed')
            open_thread=kernel.OpenThread;open_thread.argtypes=[w.DWORD,w.BOOL,w.DWORD];open_thread.restype=w.HANDLE
            resume=kernel.ResumeThread;resume.argtypes=[w.HANDLE];resume.restype=w.DWORD
            for thread in psutil.Process(self.process.pid).threads():
                handle=open_thread(2,False,thread.id)
                if not handle:raise OSError(ctypes.get_last_error(),'OpenThread failed')
                try:
                    if resume(handle)==0xffffffff:raise OSError(ctypes.get_last_error(),'ResumeThread failed')
                finally:self.close(handle)
        except BaseException:
            self.terminate_owned()
            if hasattr(self,'process'):
                self.process.kill();self.process.wait()
            raise

    def terminate_owned(self):
        if os.name=='nt':
            if self.job:self.close(self.job);self.job=None
        elif hasattr(self,'process'):
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass

@dataclass
class BoundedResult:
    returncode:int
    stdout:bytes
    stderr:bytes
    truncated:bool

def bounded_run(argv,*,cwd=None,timeout=30,max_bytes=65536):
    owned=OwnedProcess(argv,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    process=owned.process;buffers=[bytearray(),bytearray()];truncated=[False,False]
    def drain(pipe,index):
        with pipe:
            while data:=pipe.read1(8192):
                room=max_bytes-len(buffers[index])
                if len(data)>room:truncated[index]=True
                buffers[index].extend(data[:max(0,room)])
    threads=[threading.Thread(target=drain,args=(process.stdout,0)),threading.Thread(target=drain,args=(process.stderr,1))]
    for thread in threads:thread.start()
    try:code=process.wait(timeout=timeout)
    finally:
        owned.terminate_owned();process.wait()
        for thread in threads:thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):raise RuntimeError('owned output streams failed to close')
    return BoundedResult(code,bytes(buffers[0]),bytes(buffers[1]),any(truncated))
