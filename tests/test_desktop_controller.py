from pathlib import Path
import json
import time
import pytest
from chat_agent_bridge.desktop_settings import DesktopSettings, Layout, save_settings
from chat_agent_bridge import desktop_controller as dc
from chat_agent_bridge.state import TaskStore


def test_empty_secret_preserves_existing_key(tmp_path, monkeypatch):
    layout = Layout.detect(tmp_path, tmp_path / 'state')
    controller = dc.DesktopController(layout)
    calls = []
    monkeypatch.setattr(controller, 'save_secret', calls.append)
    monkeypatch.setattr(dc, 'set_autostart', lambda *args: None)
    controller.save(DesktopSettings(), secret='')
    assert calls == []


def test_export_has_only_whitelisted_information(tmp_path):
    layout = Layout.detect(tmp_path, tmp_path / 'state')
    save_settings(layout, DesktopSettings())
    output = tmp_path / 'diagnostic.json'
    dc.export_diagnostics(layout, output)
    data = json.loads(output.read_text(encoding='utf-8'))
    assert set(data) == {'version', 'created_at', 'checks'}
    assert all(set(row) <= {'component', 'status', 'code', 'message'} for row in data['checks'])
    assert str(tmp_path) not in output.read_text(encoding='utf-8')


def test_diagnostics_does_not_create_database_before_legacy_import(tmp_path):
    layout = Layout.detect(tmp_path, tmp_path / 'state')
    dc.collect_diagnostics(layout)
    assert not (layout.data_dir / 'state.db').exists()


def test_legacy_import_rejects_overwrite(tmp_path):
    layout = Layout.detect(tmp_path, tmp_path / 'state')
    save_settings(layout, DesktopSettings())
    with pytest.raises(ValueError, match='已有'):
        dc.import_legacy(layout, tmp_path / 'source')


def test_legacy_import_normalizes_paths_and_preserves_source(tmp_path):
    source = tmp_path / 'old'
    (source / 'demo').mkdir(parents=True)
    content = '[[workspaces]]\nname="demo"\nroot="demo"\n'
    (source / 'config.local.toml').write_text(content)
    layout = Layout.detect(tmp_path / 'app', tmp_path / 'state')
    dc.import_legacy(layout, source)
    from chat_agent_bridge.desktop_settings import load_settings
    assert load_settings(layout).workspaces[0].root == source / 'demo'
    assert (source / 'config.local.toml').read_text() == content


def test_pairing_code_redeems_with_five_minute_expiry(tmp_path):
    layout = Layout.detect(tmp_path, tmp_path / 'state')
    code, expires = dc.issue_pairing(layout)
    assert 290 < expires - time.time() <= 300
    from chat_agent_bridge.auth import PairingManager
    auth = PairingManager(TaskStore(layout.data_dir / 'state.db'))
    assert auth.redeem(code, 'chrome-extension://' + 'a' * 32)
