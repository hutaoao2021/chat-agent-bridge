from dataclasses import dataclass, field
from pathlib import Path
import tomllib
import re

def permission(item,key):
    value=item.get(key,False)
    if type(value) is not bool:raise ValueError(f'{key} must be a TOML boolean')
    return value

@dataclass(frozen=True)
class WorkspacePolicy:
    name: str
    root: Path
    allow_write: bool = False
    allow_command: bool = False
    allow_git_write: bool = False
    ssh_targets: tuple[str, ...] = ()

@dataclass(frozen=True)
class SSHTarget:
    name: str
    alias: str
    remote_root: str
    wrapper_path: str
    allow_write: bool = False
    allow_command: bool = False

@dataclass(frozen=True)
class Config:
    workspaces: dict[str, WorkspacePolicy]
    ssh_targets: dict[str, SSHTarget] = field(default_factory=dict)
    max_jobs: int = 4
    job_timeout_seconds: int = 3600
    max_log_bytes: int = 8 * 1024 * 1024
    port: int = 8899
    extension_port: int = 8900

    def __post_init__(self):
        for key in ['max_jobs','job_timeout_seconds','max_log_bytes','port','extension_port']:
            value=getattr(self,key)
            if type(value) is not int or value<=0:raise ValueError('limits must be positive integers')
        if not 1<=self.port<=65535 or not 1<=self.extension_port<=65535 or self.port==self.extension_port:
            raise ValueError('MCP and extension ports must be distinct valid ports')

    def workspace(self, name):
        if name not in self.workspaces: raise PermissionError('unknown workspace')
        return self.workspaces[name]

    @classmethod
    def load(cls, path: Path):
        raw = tomllib.loads(path.read_text(encoding='utf-8'))
        policies = {}
        for item in raw.get('workspaces', []):
            name = item['name']
            root = Path(item['root']).expanduser()
            if not root.is_absolute(): root = path.parent / root
            if not root.is_dir() or name in policies: raise ValueError('invalid or duplicate workspace')
            policies[name] = WorkspacePolicy(name, root.resolve(), **{k: permission(item,k) for k in ['allow_write', 'allow_command', 'allow_git_write']}, ssh_targets=tuple(item.get('ssh_targets', [])))
        targets = {}
        for item in raw.get('ssh_targets', []):
            name, alias = item['name'], item['alias']
            if name in targets or not re.fullmatch(r'[A-Za-z0-9_.-]+', alias) or alias.startswith('-'): raise ValueError('invalid SSH alias')
            for key in ['remote_root', 'wrapper_path']:
                if not re.fullmatch(r'/[A-Za-z0-9_./-]+', item[key]) or '..' in item[key].split('/'): raise ValueError('remote path must be an absolute simple POSIX path')
            targets[name] = SSHTarget(name, alias, item['remote_root'], item['wrapper_path'], permission(item,'allow_write'), permission(item,'allow_command'))
        options = raw.get('limits', {}) | raw.get('server', {})
        values = {k: options[k] for k in ['max_jobs', 'job_timeout_seconds', 'max_log_bytes', 'port', 'extension_port'] if k in options}
        return cls(policies, targets, **values)
