import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('bridge_autostart', Path(__file__).parents[1] / 'scripts' / 'autostart.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def test_running_or_existing_service_is_not_started_twice():
    assert not startup.should_start(True, False, True)
    assert not startup.should_start(False, True, True)
    assert startup.should_start(False, False, True)


def test_tunnel_waits_for_bridge_and_proxy():
    assert not startup.should_start(False, False, False)
    assert startup.tunnel_ready(bridge=True, proxy=True, credential=True)
    for missing in ['bridge', 'proxy', 'credential']:
        inputs = dict(bridge=True, proxy=True, credential=True)
        inputs[missing] = False
        assert not startup.tunnel_ready(**inputs)


@pytest.mark.skipif(os.name != 'nt', reason='Windows user encryption')
def test_user_secret_roundtrip_is_encrypted():
    secret = b'example-secret-for-roundtrip-test'
    encrypted = startup.protect(secret)
    assert secret not in encrypted
    assert startup.unprotect(encrypted) == secret


def test_startup_command_quotes_paths_with_spaces():
    command = startup.startup_command(Path('C:/A B/pythonw.exe'), Path('C:/A B/autostart.py'))
    assert command == f'"{Path("C:/A B/pythonw.exe")}" "{Path("C:/A B/autostart.py")}" run'


def test_child_environment_does_not_inherit_key_into_bridge():
    original = {'PATH': 'value', 'CONTROL_PLANE_API_KEY': 'old-secret'}
    assert 'CONTROL_PLANE_API_KEY' not in startup.child_environment(original)
    assert startup.child_environment(original, 'new-secret')['CONTROL_PLANE_API_KEY'] == 'new-secret'
    assert original['CONTROL_PLANE_API_KEY'] == 'old-secret'


@pytest.mark.skipif(os.name != 'nt', reason='Windows user encryption')
def test_save_credential_does_not_require_extra_metadata_api_permissions(tmp_path, monkeypatch):
    def network_not_allowed(*args, **kwargs):
        raise AssertionError('Configuration must not query extra APIs')
    monkeypatch.setattr(startup.urllib.request, 'build_opener', network_not_allowed)
    target=tmp_path/'tunnel-key.dpapi'
    example_key = 'sk-' + 'test-valid-looking-key-for-test'
    startup.save_key(example_key, target)
    assert startup.unprotect(target.read_bytes()).decode()==example_key
