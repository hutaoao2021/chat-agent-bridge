"""Configuration-driven, current-user service supervision."""
import argparse
import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import psutil
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request
from .credentials import read_key
from .desktop_settings import Layout, load_settings, atomic_bytes
from .process_scope import OwnedProcess

RUN_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'ChatAgentBridge'


def child_environment(original, key=None):
    env = dict(original)
    for name in ('CONTROL_PLANE_API_KEY', 'OPENAI_API_KEY', 'PYTHONPATH', 'PYTHONHOME', 'CHAT_AGENT_BRIDGE_DETACHED_RUNNERS'):
        env.pop(name, None)
    if key:
        env['CONTROL_PLANE_API_KEY'] = key
    return env


def startup_command(layout):
    return subprocess.list2cmdline([str(layout.pythonw), '-m', 'chat_agent_bridge.supervisor',
                                   '--app-dir', str(layout.app_dir), '--data-dir', str(layout.data_dir)])


def set_autostart(layout, enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_PATH) as reg:
        if enabled:
            winreg.SetValueEx(reg, RUN_NAME, 0, winreg.REG_SZ, startup_command(layout))
        else:
            try:
                current = winreg.QueryValueEx(reg, RUN_NAME)[0]
                if current == startup_command(layout):
                    winreg.DeleteValue(reg, RUN_NAME)
            except FileNotFoundError:
                pass


def request_stop(layout):
    folder = layout.data_dir / 'autostart'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'stop').touch()
    (folder / 'start-request').unlink(missing_ok=True)


def prepare_start(layout):
    folder = layout.data_dir / 'autostart'
    generation = secrets.token_hex(16)
    (folder / 'stop').unlink(missing_ok=True)
    atomic_bytes(folder / 'start-request', generation.encode())
    return generation


def start_requested(layout, generation):
    folder = layout.data_dir / 'autostart'
    try:
        return not (folder / 'stop').exists() and (folder / 'start-request').read_text() == generation
    except FileNotFoundError:
        return False


def supervisor_alive(layout):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    handle = kernel.OpenMutexW(0x100000, False, 'Local\\ChatAgentBridge-' + instance_id(layout.data_dir))
    if not handle:
        if ctypes.get_last_error() == 5: return True  # Cannot safely establish absence.
        return False
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle(handle)
    return True


def tunnel_argv(layout, settings):
    args = [str(layout.tunnel_client), 'run', '--profile', 'chat-agent-bridge', '--profile-dir',
            str(physical_profile_dir(layout.data_dir / 'tunnel-profiles')), '--health.listen-addr', f'127.0.0.1:{settings.tunnel_port}']
    if settings.proxy_url:
        args.extend(['--control-plane.http-proxy', settings.proxy_url])
    return args


def physical_profile_dir(folder):
    """Give the Go client the real file path when Windows virtualizes Local AppData."""
    profile = folder / 'chat-agent-bridge.yaml'
    if os.name != 'nt' or not profile.is_file():
        return folder
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_path = kernel.GetFinalPathNameByHandleW
    get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    get_path.restype = ctypes.c_ulong
    with profile.open('rb') as file:
        handle = msvcrt.get_osfhandle(file.fileno())
        length = get_path(handle, None, 0, 0)
        if not length:
            raise OSError(ctypes.get_last_error(), 'Cannot resolve Tunnel profile path')
        buffer = ctypes.create_unicode_buffer(length + 1)
        if not get_path(handle, buffer, len(buffer), 0):
            raise OSError(ctypes.get_last_error(), 'Cannot resolve Tunnel profile path')
    return Path(buffer.value.removeprefix('\\\\?\\')).parent


def instance_id(data_dir):
    return hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:24]


def local_json(port, path='/healthz'):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}{path}', timeout=1) as response:
        return json.loads(response.read(1048576))


def occupied(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.3):
            return True
    except OSError:
        return False


class Probe:
    def bridge(self, layout, settings):
        try:
            result = local_json(settings.control_port)
            return 'ready' if result.get('service') == 'chat-agent-bridge' and result.get('instance_id') == instance_id(layout.data_dir) else 'conflict'
        except Exception:
            return 'conflict' if occupied(settings.control_port) or occupied(settings.mcp_port) else 'missing'

    def proxy(self, settings):
        return not settings.proxy_url or occupied(int(settings.proxy_url.rsplit(':', 1)[1]))

    def tunnel(self, settings):
        return occupied(settings.tunnel_port)

    def launch(self, argv, layout, secret, log, name):
        owner = OwnedProcess(argv, allow_breakaway=name == 'Bridge', cwd=layout.app_dir, env=child_environment(os.environ, secret),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        def drain():
            with owner.process.stdout as pipe:
                while pipe.readline(16384):
                    # Raw client messages may include account IDs and credentials. Record lifecycle only.
                    pass
        threading.Thread(target=drain, daemon=True).start()
        log.info('%s started pid=%s', name, owner.process.pid)
        return owner


class Supervisor:
    def __init__(self, layout, probe=None):
        self.layout = layout
        self.probe = probe or Probe()
        self.settings = load_settings(layout)
        self.owners = {}
        self.status = {}
        self.log = logging.getLogger('bridge-supervisor-' + instance_id(layout.data_dir))

    def ensure_profile(self):
        settings = self.settings
        folder = self.layout.data_dir / 'tunnel-profiles'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = folder / 'settings.json'
        identity = {'id': settings.tunnel_id, 'mcp': settings.mcp_port, 'health': settings.tunnel_port}
        if stamp.exists() and json.loads(stamp.read_text()) == identity and (folder / 'chat-agent-bridge.yaml').exists():
            return
        argv = [str(self.layout.tunnel_client), 'init', '--sample', 'sample_mcp_remote_no_auth',
                '--profile', 'chat-agent-bridge', '--profile-dir', str(folder), '--force',
                '--tunnel-id', settings.tunnel_id, '--mcp-server-url', f'http://127.0.0.1:{settings.mcp_port}/mcp',
                '--health-listen-addr', f'127.0.0.1:{settings.tunnel_port}',
                '--control-plane-api-key-ref', 'env:CONTROL_PLANE_API_KEY']
        result = subprocess.run(argv, capture_output=True, env=child_environment(os.environ),
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        if result.returncode:
            raise ValueError('Tunnel profile 初始化失败，请检查客户端版本')
        atomic_bytes(stamp, json.dumps(identity).encode())

    def poll_once(self):
        for name, owner in list(self.owners.items()):
            if owner.process.poll() is not None:
                self.log.warning('%s exited (%s)', name, owner.process.returncode)
                owner.terminate_owned()
                del self.owners[name]
        state = self.probe.bridge(self.layout, self.settings)
        self.status['Bridge'] = {'conflict': 'port_conflict', 'ready': 'local_ready', 'missing': 'not_running'}[state]
        config = self.layout.data_dir / 'config.local.toml'
        if state == 'missing' and 'Bridge' not in self.owners and config.exists():
            self.owners['Bridge'] = self.probe.launch([str(self.layout.python), '-u', '-m', 'chat_agent_bridge.main',
                '--config', str(config), '--data-dir', str(self.layout.data_dir)], self.layout, None, self.log, 'Bridge')
            self.status['Bridge'] = 'starting'
        if state != 'ready':
            self.status['Tunnel'] = 'waiting_bridge'
            return
        key = self.layout.data_dir / 'autostart' / 'tunnel-key.dpapi'
        if not self.settings.tunnel_id:
            self.status['Tunnel'] = 'missing_config'
        elif not key.exists():
            self.status['Tunnel'] = 'credential_missing'
        elif not self.probe.proxy(self.settings):
            self.status['Tunnel'] = 'waiting_proxy'
        elif 'Tunnel' in self.owners:
            self.status['Tunnel'] = 'local_ready' if self.probe.tunnel(self.settings) else 'starting'
        elif self.probe.tunnel(self.settings):
            self.status['Tunnel'] = 'port_conflict'
        else:
            if not self.layout.tunnel_client.is_file():
                self.status['Tunnel'] = 'client_missing'
                return
            self.ensure_profile()
            self.owners['Tunnel'] = self.probe.launch(tunnel_argv(self.layout, self.settings), self.layout,
                                                     read_key(key), self.log, 'Tunnel')
            self.status['Tunnel'] = 'starting'

    def stop_owned(self):
        for owner in self.owners.values():
            owner.terminate_owned()
            try:
                owner.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.log.warning('Owned process termination timed out')
        self.owners.clear()


def single_instance(layout):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, 'Local\\ChatAgentBridge-' + instance_id(layout.data_dir))
    if not handle:
        raise OSError('Supervisor lock failed')
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
        return None
    return handle


def run(layout, generation=None):
    handle = single_instance(layout)
    if not handle:
        return
    folder = layout.data_dir / 'autostart'
    folder.mkdir(parents=True, exist_ok=True)
    if generation is None:
        # A stop accepted after this process was launched must remain effective.
        stop = folder / 'stop'
        if stop.exists() and stop.stat().st_mtime >= psutil.Process().create_time():
            kernel = ctypes.WinDLL('kernel32'); kernel.CloseHandle.argtypes = [ctypes.c_void_p]; kernel.CloseHandle(handle)
            return
        generation = prepare_start(layout)
    if not start_requested(layout, generation):
        kernel = ctypes.WinDLL('kernel32'); kernel.CloseHandle.argtypes = [ctypes.c_void_p]; kernel.CloseHandle(handle)
        return
    controller = Supervisor(layout)
    handler = RotatingFileHandler(folder / 'startup.log', maxBytes=1048576, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    controller.log.addHandler(handler)
    controller.log.setLevel(logging.INFO)
    atomic_bytes(folder / 'supervisor.pid', str(os.getpid()).encode())
    try:
        while not (folder / 'stop').exists():
            try:
                controller.poll_once()
            except Exception as error:
                controller.log.error('Startup failure (%s)', type(error).__name__)
                controller.status['error'] = type(error).__name__
            atomic_bytes(folder / 'status.json', json.dumps({'pid': os.getpid(), 'at': time.time(), 'states': controller.status}).encode())
            for _ in range(5):
                if (folder / 'stop').exists(): break
                time.sleep(1)
    finally:
        controller.stop_owned()
        (folder / 'supervisor.pid').unlink(missing_ok=True)
        atomic_bytes(folder / 'status.json', json.dumps({'pid': None, 'at': time.time(), 'states': {'Bridge': 'stopped', 'Tunnel': 'stopped'}}).encode())
        controller.log.removeHandler(handler)
        handler.close()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app-dir', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--disable', action='store_true')
    parser.add_argument('--generation')
    args = parser.parse_args()
    layout = Layout.detect(args.app_dir, args.data_dir)
    if args.stop or args.disable:
        if args.disable: set_autostart(layout, False)
        request_stop(layout)
    else:
        run(layout, args.generation)


if __name__ == '__main__':
    main()
