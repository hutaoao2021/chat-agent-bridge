from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import os
import secrets
import struct
import subprocess
import sys
import time
import psutil
from .job_runner import atomic_json
from .paths import resolve_in_root

@dataclass(frozen=True)
class Job:
    id: str
    task_id: str
    workspace: str
    argv: list[str]
    cwd: str
    key: str
    status: str='pending'
    log_dir: str=''
    def as_dict(self): return asdict(self)

@dataclass(frozen=True)
class JobChunk:
    stdout: str
    stderr: str
    next_cursor: int
    status: str
    exit_code: int | None
    truncated: bool=False
    def as_dict(self): return asdict(self)

class JobManager:
    def __init__(self, store, log_dir, policy, *, max_jobs=4, timeout=3600, max_log_bytes=8388608):
        self.store,self.log_dir,self.policy=store,Path(log_dir),policy
        self.max_jobs,self.timeout,self.max_log_bytes=max_jobs,timeout,max_log_bytes
        self.log_dir.mkdir(parents=True,exist_ok=True)

    def _record(self, job_id):
        for row in self.store.records('job'):
            if row['id']==job_id and row['workspace']==self.policy.name: return row
        raise ValueError('unknown job in this workspace')

    def _state(self, row):
        folder=Path(row.get('log_dir') or self.log_dir)/row['id']
        if (folder/'exit.json').exists(): return json.loads((folder/'exit.json').read_text(encoding='utf-8'))
        if (folder/'identity.json').exists():
            ident=json.loads((folder/'identity.json').read_text(encoding='utf-8'))
            try:
                p=psutil.Process(ident['pid'])
                if abs(p.create_time()-ident['created_at'])<.001 and p.is_running(): return {'status':'running','exit_code':None}
            except psutil.Error: pass
        return {'status':'unknown','exit_code':None}

    def start(self, argv, cwd, task_id, idempotency_key):
        if not self.policy.allow_command: raise PermissionError('commands disabled')
        if not argv or len(argv)>200 or not all(isinstance(a,str) and '\x00' not in a and len(a)<65536 for a in argv): raise ValueError('invalid argv')
        self.store.get(task_id)
        cwd=Path(cwd).resolve(strict=True)
        try: relative=cwd.relative_to(self.policy.root.resolve()).as_posix()
        except ValueError: raise PermissionError('cwd outside workspace')
        resolve_in_root(self.policy.root,relative or '.')
        if not cwd.is_dir(): raise ValueError('cwd must be directory')
        key=hashlib.sha256(f'{self.policy.name}:{task_id}:{idempotency_key}'.encode()).hexdigest()
        previous=self.store.record('job',key)
        if previous:
            if previous['argv']!=argv or previous['cwd']!=str(cwd): raise ValueError('idempotency key reused with different command')
            return Job(**previous)
        row=Job(secrets.token_hex(16),task_id,self.policy.name,argv,str(cwd),key,log_dir=str(self.log_dir.resolve())).as_dict()
        # Reserve both intent and concurrency slot under one write transaction.
        with self.store.transaction() as db:
            if self.store._get(db,task_id).status in {'completed','blocked','cancelled'}:raise ValueError('task ended')
            existing=db.execute("SELECT value FROM records WHERE namespace='job' AND key=?",(key,)).fetchone()
            if existing:
                found=json.loads(existing['value'])
                if found['argv']!=argv or found['cwd']!=str(cwd): raise ValueError('conflicting command')
                return Job(**found)
            records=[json.loads(r['value']) for r in db.execute("SELECT value FROM records WHERE namespace IN ('job','remote_job')")]
            if sum((self._state(r)['status'] if 'workspace' in r else r['status']) not in {'completed','failed','cancelled'} for r in records)>=self.max_jobs:
                raise ValueError('job limit reached')
            db.execute('INSERT INTO records VALUES(?,?,?)',('job',key,json.dumps(row)))
        folder=self.log_dir/row['id']; folder.mkdir(exist_ok=True); (folder/'stdin').mkdir(exist_ok=True)
        atomic_json(folder/'request.json',{'argv':argv,'cwd':str(cwd),'timeout':self.timeout,'max_log_bytes':self.max_log_bytes})
        flags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP) if os.name=='nt' else 0
        if os.name=='nt' and os.environ.get('CHAT_AGENT_BRIDGE_DETACHED_RUNNERS') == '1':
            flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
        with (folder/'runner.log').open('ab') as err:
            subprocess.Popen([sys.executable,'-m','chat_agent_bridge.job_runner',str(folder.resolve())],
                stdin=subprocess.DEVNULL,stdout=err,stderr=err,creationflags=flags,start_new_session=os.name!='nt')
        deadline=time.monotonic()+5
        while not (folder/'identity.json').exists() and time.monotonic()<deadline: time.sleep(.02)
        row['status']=self._state(row)['status']; self.store.put_record('job',key,row)
        return Job(**row)

    def poll(self, job_id, cursor=0, limit=65536):
        row=self._record(job_id); state=self._state(row)
        if cursor<0 or not 1<=limit<=65536: raise ValueError('invalid output bounds')
        path=self.log_dir/job_id/'output.bin'; outputs={1:bytearray(),2:bytearray()}; total=0; next_cursor=cursor
        if path.exists():
            with path.open('rb') as log:
                if cursor>path.stat().st_size: raise ValueError('cursor beyond log')
                log.seek(cursor)
                while total<limit:
                    header=log.read(5)
                    if len(header)!=5: break
                    stream,size=struct.unpack('!BI',header)
                    if stream not in outputs or size>4096: raise ValueError('invalid log cursor')
                    if total+size>limit: break
                    data=log.read(size)
                    if len(data)!=size: break
                    outputs[stream].extend(data); total+=size; next_cursor=log.tell()
        return JobChunk(outputs[1].decode('utf-8',errors='replace'),outputs[2].decode('utf-8',errors='replace'),next_cursor,state['status'],state['exit_code'],state.get('truncated',False))

    def write_stdin(self, job_id, text):
        row=self._record(job_id)
        if self._state(row)['status']!='running': raise ValueError('job is not running')
        if len(text.encode())>65536: raise ValueError('stdin too large')
        folder=self.log_dir/job_id/'stdin'
        if sum(p.stat().st_size for p in folder.glob('*.json'))>1048576:raise ValueError('stdin backlog full')
        atomic_json(folder/f'{time.time_ns():020d}-{secrets.token_hex(4)}.json',text)

    def cancel(self, job_id):
        row=self._record(job_id)
        if self._state(row)['status'] not in {'completed','failed','cancelled'}:
            folder=self.log_dir/job_id;folder.mkdir(exist_ok=True);(folder/'cancel').touch()
        return Job(**{**row,'status':self._state(row)['status']})
