"""Desktop operations without UI dependencies or remote management endpoints."""
from dataclasses import replace
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import psutil
from . import __version__
from .auth import PairingManager
from .config import Config
from .credentials import read_key, save_key
from .desktop_settings import load_settings, save_settings, DesktopSettings, atomic_bytes
from .state import TaskStore
from .supervisor import Probe, local_json, occupied, request_stop, set_autostart, child_environment, supervisor_alive, prepare_start


class DesktopController:
    def __init__(self, layout):
        self.layout = layout

    def save_secret(self, secret):
        save_key(secret, self.layout.data_dir / 'autostart' / 'tunnel-key.dpapi')

    def backup_reset_settings(self):
        import uuid
        backup = self.layout.data_dir / 'settings-backups' / uuid.uuid4().hex
        backup.mkdir(parents=True)
        for name in ('settings.json', 'config.local.toml'):
            source = self.layout.data_dir / name
            if source.exists():
                shutil.copy2(source, backup / name)
        self.save(DesktopSettings())
        return backup

    def save(self, settings, secret=''):
        settings.validate()
        # Validate encryption before touching configuration, keep old encrypted key if omitted.
        from .credentials import protect
        encrypted = None
        if secret.strip():
            if not secret.strip().startswith('sk-') or len(secret.strip()) < 20:
                raise ValueError('请输入完整的 Tunnel API key')
            encrypted = protect(secret.strip().encode())
        previous = {name: (self.layout.data_dir / name).read_bytes() if (self.layout.data_dir / name).exists() else None
                    for name in ('settings.json', 'config.local.toml', 'autostart/tunnel-key.dpapi')}
        try:
            save_settings(self.layout, settings)
            if encrypted is not None:
                atomic_bytes(self.layout.data_dir / 'autostart' / 'tunnel-key.dpapi', encrypted)
            set_autostart(self.layout, settings.autostart)
        except Exception:
            for name, old in previous.items():
                path = self.layout.data_dir / name
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_bytes(path, old)
            raise

    def start(self):
        settings = load_settings(self.layout)
        if not self.layout.pythonw.is_file():
            raise ValueError('软件运行环境缺失，请重新安装或修复')
        if not (self.layout.data_dir / 'config.local.toml').exists():
            raise ValueError('请先选择工作区并保存设置')
        Config.load(self.layout.data_dir / 'config.local.toml')
        if supervisor_alive(self.layout):
            return None
        generation = prepare_start(self.layout)
        return subprocess.Popen([str(self.layout.pythonw), '-m', 'chat_agent_bridge.supervisor',
                                 '--app-dir', str(self.layout.app_dir), '--data-dir', str(self.layout.data_dir), '--generation', generation],
                                cwd=self.layout.app_dir, env=child_environment(os.environ),
                                creationflags=subprocess.CREATE_NO_WINDOW)

    def stop(self):
        request_stop(self.layout)
        pid = self.layout.data_dir / 'autostart' / 'supervisor.pid'
        deadline = time.monotonic() + 15
        while supervisor_alive(self.layout) and time.monotonic() < deadline:
            time.sleep(.2)
        if supervisor_alive(self.layout):
            raise ValueError('停止尚未确认；请检查诊断，不会强制结束未知进程')
        pid.unlink(missing_ok=True)

    def stop_for_maintenance(self):
        assert_maintenance_ready(self.layout)
        self.stop()
        assert_maintenance_ready(self.layout)

    def restart(self):
        self.stop()
        return self.start()


def issue_pairing(layout):
    code = PairingManager(TaskStore(layout.data_dir / 'state.db')).issue_code()
    return code, time.time() + 300


def assert_maintenance_ready(layout):
    if not (layout.data_dir / 'state.db').exists(): return
    store = TaskStore(layout.data_dir / 'state.db')
    for row in store.records('job'):
        folder = Path(row.get('log_dir') or layout.data_dir / 'jobs' / row.get('workspace', '')) / row['id']
        try:
            identity = json.loads((folder / 'identity.json').read_text())
        except FileNotFoundError:
            continue
        try:
            process = psutil.Process(identity['pid'])
            if abs(process.create_time() - identity['created_at']) < .001 and process.is_running():
                raise ValueError('后台任务仍在运行；请等待完成或在扩展取消任务后再更新/卸载')
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            raise ValueError('后台任务状态无法确认，请先检查任务再更新/卸载')


def collect_diagnostics(layout):
    checks = []
    def add(component, status, code, message):
        checks.append({'component': component, 'status': status, 'code': code, 'message': message})
    try:
        settings = load_settings(layout)
        config = layout.data_dir / 'config.local.toml'
        if not config.exists():
            add('设置', 'waiting', 'missing_config', '请先选择工作区并保存设置')
        else:
            Config.load(config)
            add('设置', 'ok', 'valid', '配置有效')
        add('工作区', 'ok' if settings.workspaces else 'waiting', 'configured' if settings.workspaces else 'missing_workspace',
            f'已配置 {len(settings.workspaces)} 个工作区' if settings.workspaces else '请添加工作目录')
    except Exception:
        add('设置', 'error', 'invalid_config', '配置不可读取或不合法，请修复设置')
        settings = DesktopSettings()
    add('运行环境', 'ok' if layout.python.is_file() and layout.pythonw.is_file() else 'error',
        'installed' if layout.python.is_file() else 'runtime_missing', '运行环境完整' if layout.python.is_file() and layout.pythonw.is_file() else '运行环境缺失，请修复安装')
    probe = Probe()
    bridge = probe.bridge(layout, settings)
    add('Bridge', 'ok' if bridge == 'ready' else 'error' if bridge == 'conflict' else 'waiting',
        {'ready': 'local_ready', 'conflict': 'port_conflict', 'missing': 'not_running'}[bridge],
        {'ready': '本地服务就绪', 'conflict': '端口由其他实例或程序占用，请检查旧服务', 'missing': '本地服务未启动'}[bridge])
    add('代理', 'ok' if probe.proxy(settings) else 'waiting', 'direct' if not settings.proxy_url else 'local_proxy',
        '使用直连' if not settings.proxy_url else '代理端口可用' if probe.proxy(settings) else '等待本地代理启动')
    key = layout.data_dir / 'autostart' / 'tunnel-key.dpapi'
    try:
        if not key.exists():
            add('凭据', 'waiting', 'credential_missing', '尚未保存 Tunnel 密钥')
        else:
            read_key(key)
            add('凭据', 'ok', 'encrypted', '密钥已加密保存；空白输入表示保留')
    except Exception:
        add('凭据', 'error', 'decrypt_failed', '当前用户无法解密，请重新填写密钥')
    add('Tunnel 客户端', 'ok' if layout.tunnel_client.is_file() else 'error', 'installed' if layout.tunnel_client.is_file() else 'client_missing',
        '客户端存在' if layout.tunnel_client.is_file() else '客户端缺失，请修复安装')
    tunnel_state = 'not_running'
    try:
        # Only an explicit control-plane connectivity indicator can mean cloud connected.
        status = local_json(settings.tunnel_port, '/status')
        if status.get('control_plane_connected') is True:
            tunnel_state = 'cloud_connected'
        else:
            tunnel_state = 'cloud_unknown'
    except Exception:
        if occupied(settings.tunnel_port):
            tunnel_state = 'cloud_unknown'
    add('Tunnel', 'ok' if tunnel_state == 'cloud_connected' else 'waiting', tunnel_state,
        {'not_running': 'Tunnel 未启动', 'cloud_unknown': '本地监听存在；云端状态请在 Tunnel 管理页或 ChatGPT 实测核对', 'cloud_connected': '客户端明确报告云端连接成功'}[tunnel_state])
    alive = supervisor_alive(layout)
    add('后台', 'ok' if alive else 'waiting', 'supervised' if alive else 'not_running', '监督程序已启动' if alive else '监督程序未启动')
    add('Git', 'ok' if shutil.which('git') else 'waiting', 'available' if shutil.which('git') else 'missing', 'Git 可用' if shutil.which('git') else '需要 Git 操作时请安装 Git')
    add('SSH', 'ok' if shutil.which('ssh') else 'waiting', 'available' if shutil.which('ssh') else 'missing', 'OpenSSH 可用；远端连接需单独验证' if shutil.which('ssh') else '需要远端工作时请安装 OpenSSH')
    try:
        if not (layout.data_dir / 'state.db').exists():
            add('扩展配对', 'waiting', 'unpaired', '尚未配对；可先导入旧版或生成一次性码')
            raise FileNotFoundError('No task database yet')
        store = TaskStore(layout.data_dir / 'state.db')
        tokens = [t for t in store.records('token') if not t.get('revoked') and t.get('expires', 0) > time.time()]
        add('扩展配对', 'ok' if tokens else 'waiting', 'paired' if tokens else 'unpaired',
            f'存在 {len(tokens)} 个有效浏览器配对；对话工作模式在扩展里查看' if tokens else '请生成一次性码，在浏览器扩展首次配对')
        with store.transaction() as db:
            active = db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('completed','blocked','cancelled')").fetchone()[0]
            pending = db.execute("SELECT count(*) FROM approvals WHERE decision='pending'").fetchone()[0]
        add('任务', 'waiting' if pending else 'ok', 'pending_approval' if pending else 'state_available', f'未完成任务 {active}；待审批 {pending}')
    except Exception:
        add('任务', 'waiting', 'state_unavailable', '任务状态暂不可读')
    return checks


def export_diagnostics(layout, output):
    data = {'version': __version__, 'created_at': time.time(), 'checks': collect_diagnostics(layout)}
    atomic_bytes(Path(output), json.dumps(data, ensure_ascii=False, indent=2).encode())


def import_legacy(layout, source):
    source = Path(source).resolve()
    if (layout.data_dir / 'settings.json').exists() or (layout.data_dir / 'state.db').exists():
        raise ValueError('目标已有设置或任务数据，不能覆盖导入')
    config = Config.load(source / 'config.local.toml')
    settings = DesktopSettings(workspaces=tuple(config.workspaces.values()), ssh_targets=tuple(config.ssh_targets.values()),
                               mcp_port=config.port, control_port=config.extension_port, max_jobs=config.max_jobs,
                               job_timeout_seconds=config.job_timeout_seconds, max_log_bytes=config.max_log_bytes)
    profile = source / '.data' / 'tunnel-profiles' / 'chat-agent-bridge.yaml'
    if profile.exists():
        import re
        text = profile.read_text(encoding='utf-8')
        # Import only two scalar fields, never evaluate YAML tags or secret references.
        def scalar(name):
            match = re.search(r'^\s+' + name + r':\s*[\"\']?([^\"\'\s#]+)', text, re.M)
            return match[1] if match else ''
        settings = replace(settings, tunnel_id=scalar('tunnel_id'), proxy_url=scalar('http_proxy'))
    layout.data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bridge-import-', dir=layout.data_dir.parent) as staging:
        from .desktop_settings import Layout
        stage = Layout.detect(layout.app_dir, Path(staging))
        save_settings(stage, settings)
        old_key = source / '.data' / 'autostart' / 'tunnel-key.dpapi'
        if old_key.exists():
            save_key(read_key(old_key), stage.data_dir / 'autostart' / 'tunnel-key.dpapi')
        old_db = source / '.data' / 'state.db'
        if old_db.exists():
            with closing(sqlite3.connect(f'{old_db.as_uri()}?mode=ro', uri=True)) as src, closing(sqlite3.connect(stage.data_dir / 'state.db')) as dest:
                src.backup(dest)
            migrate_job_history(stage.data_dir, layout.data_dir, source / '.data')
        if layout.data_dir.exists() and any(layout.data_dir.iterdir()):
            raise ValueError('目标已有数据，不能覆盖导入')
        if layout.data_dir.exists():
            layout.data_dir.rmdir()
        # Staging is on the same volume; an atomic rename avoids partial import.
        os.replace(stage.data_dir, layout.data_dir)


def migrate_job_history(staging, destination, legacy_data):
    """Copy only completed local job evidence; never assume ownership of live legacy runners."""
    import re
    old_root = legacy_data / 'jobs'
    store = TaskStore(staging / 'state.db')
    for row in store.records('job'):
        if not re.fullmatch(r'[a-f0-9]{32}', row.get('id', '')):
            raise ValueError('旧版任务记录不合法，不能导入')
        log_dir = Path(row.get('log_dir') or old_root / row.get('workspace', ''))
        try:
            relative = log_dir.relative_to(old_root)
        except ValueError:
            raise ValueError('旧版任务日志不在数据目录中，不能导入')
        folder = log_dir / row['id']
        for path in (folder, *folder.parents):
            if path.is_symlink() or path.is_junction(): raise ValueError('旧版任务日志包含链接，不能导入')
            if path == legacy_data: break
        receipt = folder / 'exit.json'
        if not receipt.exists() or json.loads(receipt.read_text())['status'] not in {'completed', 'failed', 'cancelled'}:
            raise ValueError('旧版仍有运行中或未确认的任务，请先检查并结束任务后导入')
        for path in folder.rglob('*'):
            if path.is_symlink() or path.is_junction(): raise ValueError('旧版任务日志包含链接，不能导入')
        shutil.copytree(folder, staging / 'jobs' / relative / row['id'])
        row['log_dir'] = str(destination / 'jobs' / relative)
        store.put_record('job', row['key'], row)
