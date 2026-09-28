from dataclasses import replace
import ctypes
import os
from pathlib import Path
import tempfile
import pytest
from chat_agent_bridge.desktop_settings import Layout, DesktopSettings
from chat_agent_bridge import supervisor as sp


def test_direct_tunnel_has_no_proxy(tmp_path):
    layout = Layout.detect(app_dir=tmp_path, data_dir=tmp_path / 'state')
    args = sp.tunnel_argv(layout, DesktopSettings(tunnel_id='tunnel_example'))
    assert '--control-plane.http-proxy' not in args
    assert str(layout.data_dir / 'tunnel-profiles') in args


def test_configured_proxy_is_passed_as_one_argument(tmp_path):
    layout = Layout.detect(app_dir=tmp_path, data_dir=tmp_path / 'state')
    args = sp.tunnel_argv(layout, DesktopSettings(proxy_url='http://127.0.0.1:7890'))
    assert args[args.index('--control-plane.http-proxy') + 1] == 'http://127.0.0.1:7890'


@pytest.mark.skipif(os.name != 'nt', reason='Windows package file virtualization')
def test_tunnel_uses_physical_profile_dir_when_windows_redirects_local_app_data():
    import msvcrt
    with tempfile.TemporaryDirectory(prefix='bridge-profile-', dir=os.environ['LOCALAPPDATA']) as root:
        profile = Path(root) / 'tunnel-profiles' / 'chat-agent-bridge.yaml'
        profile.parent.mkdir()
        profile.write_text('config_version: 1\n', encoding='utf-8')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get_path = kernel.GetFinalPathNameByHandleW
        get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
        get_path.restype = ctypes.c_ulong
        with profile.open('rb') as file:
            buffer = ctypes.create_unicode_buffer(32768)
            assert get_path(msvcrt.get_osfhandle(file.fileno()), buffer, len(buffer), 0)
        physical_dir = str(Path(buffer.value.removeprefix('\\\\?\\')).parent)
        if physical_dir == str(profile.parent):
            pytest.skip('Local AppData is not redirected in this process')
        layout = Layout.detect(data_dir=root)
        args = sp.tunnel_argv(layout, DesktopSettings())
        assert args[args.index('--profile-dir') + 1] == physical_dir


def test_unknown_healthy_process_is_not_stopped(tmp_path):
    class Probe:
        def bridge(self, layout, settings): return 'conflict'
        def proxy(self, settings): return True
        def tunnel(self, settings): return False
        def launch(self, *args): pytest.fail('Unknown process must not be replaced')
    controller = sp.Supervisor(Layout.detect(tmp_path, tmp_path / 'state'), Probe())
    controller.settings = DesktopSettings()
    controller.poll_once()
    controller.stop_owned()
    assert controller.owners == {}
    assert controller.status['Bridge'] == 'port_conflict'


def test_bridge_does_not_inherit_tunnel_key():
    original = {'PATH': 'value', 'CONTROL_PLANE_API_KEY': 'secret', 'PYTHONPATH': 'dev'}
    result = sp.child_environment(original)
    assert 'CONTROL_PLANE_API_KEY' not in result
    assert 'PYTHONPATH' not in result
    assert original['CONTROL_PLANE_API_KEY'] == 'secret'


def test_startup_command_quotes_installation_paths(tmp_path):
    layout = Layout.detect(tmp_path / 'app space', tmp_path / 'data space')
    command = sp.startup_command(layout)
    assert '-m chat_agent_bridge.supervisor' in command
    assert f'"{layout.data_dir}"' in command
