import base64
from dataclasses import dataclass,asdict
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import subprocess
from .jobs import JobChunk
from .process_scope import bounded_run

@dataclass(frozen=True)
class RemoteJob:
    id: str
    target: str
    task_id: str
    argv: list[str]
    status: str
    def as_dict(self): return asdict(self)

class SSHManager:
    def __init__(self,config,store,transport=None):
        self.config,self.store=config,store
        self.transport=transport or (lambda argv,timeout: bounded_run(argv,timeout=timeout,max_bytes=131072))

    def _target(self,name):
        if name not in self.config.ssh_targets: raise PermissionError('unknown SSH target')
        return self.config.ssh_targets[name]

    def _call(self,name,operation,payload,timeout=30):
        target=self._target(name)
        encoded=base64.urlsafe_b64encode(json.dumps({**payload,'root':target.remote_root},ensure_ascii=True).encode()).decode()
        argv=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',target.alias,'python3',target.wrapper_path,operation,encoded]
        result=self.transport(argv,timeout)
        if result.returncode: raise ConnectionError(f'SSH operation failed (exit {result.returncode}); query existing job before retry')
        if len(result.stdout)>131072 or getattr(result,'truncated',False): raise ValueError('SSH response too large')
        value=json.loads(result.stdout)
        if 'error' in value: raise ValueError(value['error'])
        return value

    def _argv(self,argv):
        if not argv or len(argv)>200 or any(not isinstance(a,str) or '\x00' in a or len(a)>65536 for a in argv): raise ValueError('invalid argv')

    def _path(self,relative):
        p=PurePosixPath(relative)
        if not relative or p.is_absolute() or '..' in p.parts or '\\' in relative or '\x00' in relative: raise PermissionError('relative remote path required')

    def run(self,target_name,argv,timeout=30):
        if not self._target(target_name).allow_command: raise PermissionError('remote commands disabled')
        self._argv(argv)
        if not 1<=timeout<=30: raise ValueError('use background jobs beyond 30 seconds')
        return self._call(target_name,'run',{'argv':argv,'timeout':timeout},timeout+10)

    def start_job(self,target_name,argv,task_id,idempotency_key):
        if not self._target(target_name).allow_command: raise PermissionError('remote commands disabled')
        self._argv(argv); self.store.get(task_id)
        job_id=hashlib.sha256(f'{target_name}:{task_id}:{idempotency_key}'.encode()).hexdigest()
        previous=self.store.record('remote_job',job_id)
        if previous:
            if previous['argv']!=argv: raise ValueError('conflicting idempotency key')
            try: previous['status']=self._call(target_name,'query',{'job_id':job_id})['status']
            except (ConnectionError,subprocess.TimeoutExpired): previous['status']='unknown'
            self.store.put_record('remote_job',job_id,previous)
            return RemoteJob(**previous)
        row=RemoteJob(job_id,target_name,task_id,argv,'unknown').as_dict()
        # Refresh known remote receipts before reserving a shared local/remote slot.
        for current in self.store.records('remote_job'):
            if current['status'] in {'completed','failed','cancelled'}:continue
            try:current['status']=self._call(current['target'],'query',{'job_id':current['id']})['status']
            except (ConnectionError,subprocess.TimeoutExpired,ValueError):current['status']='unknown'
            self.store.put_record('remote_job',current['id'],current)
        with self.store.transaction() as db:
            if self.store._get(db,task_id).status in {'completed','blocked','cancelled'}:raise ValueError('task ended')
            found=db.execute("SELECT value FROM records WHERE namespace='remote_job' AND key=?",(job_id,)).fetchone()
            if found:
                existing=json.loads(found['value'])
                if existing['argv']!=argv:raise ValueError('conflicting idempotency key')
                return RemoteJob(**existing)
            rows=[json.loads(r['value']) for r in db.execute("SELECT value FROM records WHERE namespace IN ('job','remote_job')")]
            count=0
            for current in rows:
                if 'workspace' in current:
                    receipt=Path(current.get('log_dir') or self.store.path.parent/'jobs')/current['id']/'exit.json'
                    # Unknown intents occupy a slot until an operator resolves them.
                    if not receipt.exists():count+=1
                elif current['status'] not in {'completed','failed','cancelled'}:count+=1
            if count>=self.config.max_jobs:raise ValueError('job limit reached')
            db.execute('INSERT INTO records VALUES(?,?,?)',('remote_job',job_id,json.dumps(row)))
        try:
            result=self._call(target_name,'start',{'argv':argv,'job_id':job_id,'timeout':self.config.job_timeout_seconds,'max_log_bytes':self.config.max_log_bytes})
            row['status']=result['status']
        except (ConnectionError,subprocess.TimeoutExpired): pass
        self.store.put_record('remote_job',job_id,row)
        return RemoteJob(**row)

    def poll_job(self,remote_job_id,cursor=0):
        row=self.store.record('remote_job',remote_job_id)
        if not row: raise ValueError('unknown remote job')
        try:
            result=JobChunk(**self._call(row['target'],'poll',{'job_id':remote_job_id,'cursor':cursor}))
            row['status']=result.status;self.store.put_record('remote_job',remote_job_id,row)
            return result
        except (ConnectionError,subprocess.TimeoutExpired): return JobChunk('','',cursor,'unknown',None)

    def cancel_job(self,remote_job_id):
        row=self.store.record('remote_job',remote_job_id)
        if not row: raise ValueError('unknown remote job')
        return self._call(row['target'],'cancel',{'job_id':remote_job_id})

    def read_text(self,target_name,allowed_relative,start_line=1,max_lines=200):
        self._path(allowed_relative)
        return self._call(target_name,'read',{'path':allowed_relative,'start_line':start_line,'max_lines':max_lines})

    def write_text(self,target_name,allowed_relative,content,expected_sha256):
        if not self._target(target_name).allow_write: raise PermissionError('remote writes disabled')
        self._path(allowed_relative)
        if len(content.encode())>1048576: raise ValueError('remote file too large')
        return self._call(target_name,'write',{'path':allowed_relative,'content':content,'sha256':expected_sha256})
