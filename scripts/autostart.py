"""Windows per-user background startup. Credentials stay in user DPAPI storage."""
import ctypes
from ctypes import wintypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '.data' / 'autostart'
KEY = DATA / 'tunnel-key.dpapi'
STOP = DATA / 'stop'
RUN_NAME = 'ChatAgentBridge'
RUN_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
PYTHONW = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
CLIENT = ROOT / '.tools' / 'tunnel-client' / 'tunnel-client.exe'
PROFILE_DIR = ROOT / '.data' / 'tunnel-profiles'
PROXY = 'http://127.0.0.1:7892'


def should_start(process_running, service_healthy, prerequisites):
    return not process_running and not service_healthy and prerequisites


def tunnel_ready(*, bridge, proxy, credential):
    return bridge and proxy and credential


def child_environment(original, key=None):
    env = dict(original)
    env.pop('CONTROL_PLANE_API_KEY', None)
    if key:
        env['CONTROL_PLANE_API_KEY'] = key
    return env


def startup_command(pythonw, script):
    return subprocess.list2cmdline([str(pythonw), str(script), 'run'])


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN, current-user scope (no machine-wide flag).
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(), 'Windows credential encryption failed')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def protect(data):
    return crypt(data)


def unprotect(data):
    return crypt(data, True)


def read_key():
    return unprotect(KEY.read_bytes()).decode('utf-8')


def save_key(secret, target=KEY):
    secret = secret.strip()
    if not secret.startswith('sk-') or len(secret) < 20:
        raise ValueError('请输入完整的 Tunnel API key')
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_bytes(protect(secret.encode('utf-8')))
    os.replace(temp, target)


def healthy(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def proxy_ready():
    try:
        with socket.create_connection(('127.0.0.1', 7892), timeout=1):
            return True
    except OSError:
        return False


def logger():
    DATA.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger('bridge-autostart')
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = RotatingFileHandler(DATA / 'startup.log', maxBytes=1048576, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log.addHandler(handler)
    return log


def single_instance():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    name = 'Local\\ChatAgentBridge-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), 'Startup lock failed')
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        return None
    return handle


def launch(argv, log, name, secret=None):
    from chat_agent_bridge.process_scope import OwnedProcess
    owner = OwnedProcess(argv, cwd=ROOT, env=child_environment(os.environ, secret),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    def drain():
        with owner.process.stdout as pipe:
            while line := pipe.readline(16384):
                text = line.decode('utf-8', errors='replace').strip()
                if secret:
                    text = text.replace(secret, '[redacted]')
                log.info('%s: %s', name, text)
    threading.Thread(target=drain, daemon=True).start()
    log.info('%s started pid=%s', name, owner.process.pid)
    return owner


def run():
    mutex = single_instance()
    if not mutex:
        return
    log = logger()
    STOP.unlink(missing_ok=True)
    (DATA / 'supervisor.pid').write_text(str(os.getpid()), encoding='ascii')
    owners = {}
    log.info('Background startup active; existing services are left running')
    try:
        while not STOP.exists():
            for name, owner in list(owners.items()):
                if owner.process.poll() is not None:
                    log.info('%s exited code=%s; will restart', name, owner.process.returncode)
                    owner.terminate_owned()
                    del owners[name]
            bridge = healthy(8900)
            if should_start('Bridge' in owners, bridge, True):
                try:
                    owners['Bridge'] = launch([str(PYTHON), '-u', '-m', 'chat_agent_bridge.main',
                        '--config', str(ROOT / 'config.local.toml'), '--data-dir', str(ROOT / '.data')], log, 'Bridge')
                except Exception as error:
                    log.info('Bridge startup failed (%s)', type(error).__name__)
            ready = tunnel_ready(bridge=bridge, proxy=proxy_ready(), credential=KEY.exists())
            if should_start('Tunnel' in owners, healthy(8901), ready):
                try:
                    owners['Tunnel'] = launch([str(CLIENT), 'run', '--profile', 'chat-agent-bridge',
                        '--profile-dir', str(PROFILE_DIR), '--control-plane.http-proxy', PROXY], log, 'Tunnel', read_key())
                except Exception as error:
                    log.info('Tunnel startup failed (%s)', type(error).__name__)
            # A stop request is observed within one second, retries every five seconds.
            for _ in range(5):
                if STOP.exists():
                    break
                time.sleep(1)
    finally:
        for owner in owners.values():
            owner.terminate_owned()
            owner.process.wait(timeout=10)
        log.info('Background startup stopped; owned services terminated')


def configure():
    import tkinter as tk
    from tkinter import messagebox, simpledialog
    import winreg
    root = tk.Tk()
    root.withdraw()
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        if not KEY.exists():
            secret = simpledialog.askstring('Chat Agent Bridge 开机启动',
                '请粘贴之前的 Tunnel API key。\n仅在本机使用 Windows 用户加密保存，以后无需输入。', show='*', parent=root)
            if secret is None:
                return
            # Actual tunnel-client handles authentication; setup has no extra API requirement.
            save_key(secret)
            del secret
        else:
            read_key()  # Verify the saved key can be decrypted by this Windows user.
        for required in [PYTHONW, CLIENT, ROOT / 'config.local.toml']:
            if not required.exists():
                raise ValueError('缺少启动文件：' + str(required))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_PATH) as reg:
            winreg.SetValueEx(reg, RUN_NAME, 0, winreg.REG_SZ, startup_command(PYTHONW, Path(__file__)))
        subprocess.Popen([str(PYTHONW), str(Path(__file__)), 'run'], cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
        messagebox.showinfo('设置完成', '已启用登录 Windows 后后台启动。\nBridge 和 Tunnel 会自动运行，无需终端。\n代理 7892 就绪后 Tunnel 会连接。', parent=root)
    except Exception as error:
        # HTTP error details may contain sensitive request context; show type/status only.
        message = str(error) if isinstance(error, ValueError) else f'设置失败：{type(error).__name__}，请检查网络和 key 权限。'
        messagebox.showerror('Chat Agent Bridge', message, parent=root)
    finally:
        root.destroy()


def disable():
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_PATH) as reg:
        try:
            winreg.DeleteValue(reg, RUN_NAME)
        except FileNotFoundError:
            pass
    DATA.mkdir(parents=True, exist_ok=True)
    STOP.touch()


if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('Windows only')
    action = sys.argv[1] if len(sys.argv) > 1 else 'configure'
    if action == 'configure':
        configure()
    elif action == 'run':
        try:
            run()
        except Exception as error:
            logger().error('Supervisor failed (%s)', type(error).__name__)
    elif action == 'disable':
        disable()
    elif action == 'stop':
        DATA.mkdir(parents=True, exist_ok=True)
        STOP.touch()
