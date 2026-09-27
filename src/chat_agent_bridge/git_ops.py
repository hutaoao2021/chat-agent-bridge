import subprocess
from .paths import resolve_in_root
from .process_scope import bounded_run

class GitTools:
    def __init__(self,policy): self.policy=policy
    def _run(self,argv):
        result=bounded_run(['git','-c','core.hooksPath=NUL','-c','core.fsmonitor=false','-C',str(self.policy.root),*argv],timeout=30)
        return {'stdout':result.stdout.decode('utf-8',errors='replace'),'stderr':result.stderr.decode('utf-8',errors='replace'),'exit_code':result.returncode,'truncated':result.truncated}
    def status(self): return self._run(['status','--porcelain=v1','--untracked-files=normal'])
    def diff(self,paths=None,staged=False):
        paths=paths or []
        for path in paths: resolve_in_root(self.policy.root,path,allow_missing_leaf=True)
        return self._run(['diff','--no-ext-diff','--no-textconv',*(['--cached'] if staged else []),'--',*paths])
    def log(self,limit=20):
        if not 1<=limit<=100: raise ValueError('invalid log limit')
        return self._run(['log',f'-{limit}','--format=%h %s'])
    def run_write(self,argv):
        if not self.policy.allow_git_write: raise PermissionError('Git writes disabled')
        if not argv: raise ValueError('missing Git command')
        if argv[0]=='add':
            if len(argv)<2: raise ValueError('paths required')
            for path in argv[1:]: resolve_in_root(self.policy.root,path)
            return self._run(['add','--',*argv[1:]])
        if argv[0]=='commit' and len(argv)==3 and argv[1]=='-m':
            return self._run(['-c','commit.gpgsign=false','commit','-m',argv[2]])
        raise PermissionError('allowed Git writes: add paths, commit -m message')
