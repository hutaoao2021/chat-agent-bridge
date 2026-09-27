"""Installation paths and offline, validated desktop settings."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import re
import sys
import tempfile
import tomllib
from .config import Config, WorkspacePolicy, SSHTarget


@dataclass(frozen=True)
class Layout:
    app_dir: Path
    data_dir: Path
    python: Path
    pythonw: Path
    tunnel_client: Path
    extension_dir: Path

    @classmethod
    def detect(cls, app_dir=None, data_dir=None):
        module = Path(__file__).resolve()
        default = module.parents[4] if module.parents[3].name.lower() == 'runtime' and module.parents[2].name.lower() == 'lib' else module.parents[2]
        app = Path(app_dir or default).resolve()
        installed = (app / 'runtime').is_dir()
        data = Path(data_dir or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ChatAgentBridge' / 'data' if installed else app / '.data')).resolve()
        runtime = app / 'runtime' if installed else app / '.venv' / 'Scripts'
        return cls(app, data, runtime / 'python.exe', runtime / 'pythonw.exe',
                   app / ('tools' if installed else '.tools') / 'tunnel-client' / 'tunnel-client.exe', app / 'extension')


@dataclass(frozen=True)
class DesktopSettings:
    tunnel_id: str = ''
    proxy_url: str = ''
    mcp_port: int = 8899
    control_port: int = 8900
    tunnel_port: int = 8901
    autostart: bool = False
    workspaces: tuple[WorkspacePolicy, ...] = ()
    ssh_targets: tuple[SSHTarget, ...] = ()
    max_jobs: int = 4
    job_timeout_seconds: int = 3600
    max_log_bytes: int = 8388608

    def validate(self, *, require_existing=True):
        ports = (self.mcp_port, self.control_port, self.tunnel_port)
        if any(type(p) is not int or not 1 <= p <= 65535 for p in ports) or len(set(ports)) != 3:
            raise ValueError('三个端口必须是 1–65535 之间互不相同的整数')
        if type(self.autostart) is not bool:
            raise ValueError('开机启动必须是布尔值')
        if self.tunnel_id and not re.fullmatch(r'tunnel_[A-Za-z0-9]+', self.tunnel_id):
            raise ValueError('Tunnel ID 格式不正确')
        if self.proxy_url:
            match = re.fullmatch(r'http://127\.0\.0\.1:(\d{1,5})', self.proxy_url)
            if not match or not 1 <= int(match[1]) <= 65535:
                raise ValueError('代理请使用 http://127.0.0.1:端口，或留空直连')
        names = set()
        for policy in self.workspaces:
            if not re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]+', policy.name) or policy.name in names or not policy.root.is_absolute() or (require_existing and not policy.root.is_dir()):
                raise ValueError('工作区名称重复、不合法或目录不存在')
            names.add(policy.name)
            if any(type(getattr(policy, k)) is not bool for k in ('allow_write', 'allow_command', 'allow_git_write')):
                raise ValueError('工作区权限必须是布尔值')
        Config({w.name: w for w in self.workspaces}, {t.name: t for t in self.ssh_targets},
               self.max_jobs, self.job_timeout_seconds, self.max_log_bytes, self.mcp_port, self.control_port)
        for target in self.ssh_targets:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', target.alias) or target.alias.startswith('-'):
                raise ValueError('SSH 别名不合法')
            for value in (target.remote_root, target.wrapper_path):
                if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value) or '..' in value.split('/'):
                    raise ValueError('SSH 路径必须是简单绝对路径')
            if any(type(x) is not bool for x in (target.allow_write, target.allow_command)):
                raise ValueError('SSH 权限必须是布尔值')
        if len({t.name for t in self.ssh_targets}) != len(self.ssh_targets):
            raise ValueError('SSH 目标名称重复')
        for w in self.workspaces:
            if any(t not in {x.name for x in self.ssh_targets} for t in w.ssh_targets):
                raise ValueError('工作区引用了未配置的 SSH 目标')


def render_bridge_config(settings):
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    boolean = lambda value: 'true' if value else 'false'
    lines = ['[server]', f'port = {settings.mcp_port}', f'extension_port = {settings.control_port}',
             '[limits]', f'max_jobs = {settings.max_jobs}', f'job_timeout_seconds = {settings.job_timeout_seconds}', f'max_log_bytes = {settings.max_log_bytes}']
    for w in settings.workspaces:
        lines.extend(['', '[[workspaces]]', f'name = {quote(w.name)}', f'root = {quote(w.root)}'])
        lines.extend(f'{k} = {boolean(getattr(w, k))}' for k in ('allow_write', 'allow_command', 'allow_git_write'))
        lines.append('ssh_targets = [' + ', '.join(quote(x) for x in w.ssh_targets) + ']')
    for t in settings.ssh_targets:
        lines.extend(['', '[[ssh_targets]]'])
        lines.extend(f'{k} = {quote(getattr(t, k))}' for k in ('name', 'alias', 'remote_root', 'wrapper_path'))
        lines.extend(f'{k} = {boolean(getattr(t, k))}' for k in ('allow_write', 'allow_command'))
    text = '\n'.join(lines) + '\n'
    tomllib.loads(text)
    return text


def settings_dict(settings):
    value = asdict(settings)
    for w in value['workspaces']:
        w['root'] = str(w['root'])
    return value


def load_settings(layout):
    path = layout.data_dir / 'settings.json'
    if not path.exists():
        return DesktopSettings()
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['workspaces'] = tuple(WorkspacePolicy(**{**w, 'root': Path(w['root']), 'ssh_targets': tuple(w.get('ssh_targets', []))}) for w in raw.get('workspaces', []))
    raw['ssh_targets'] = tuple(SSHTarget(**t) for t in raw.get('ssh_targets', []))
    settings = DesktopSettings(**raw)
    settings.validate(require_existing=False)
    return settings


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_settings(layout, settings):
    settings.validate()
    contents = {'settings.json': json.dumps(settings_dict(settings), ensure_ascii=False, indent=2).encode(),
                'config.local.toml': render_bridge_config(settings).encode()}
    originals = {}
    replaced = []
    try:
        for name, content in contents.items():
            path = layout.data_dir / name
            originals[path] = path.read_bytes() if path.exists() else None
            atomic_bytes(path, content)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, originals[path])
        raise
