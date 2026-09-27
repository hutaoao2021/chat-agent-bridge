from dataclasses import replace
from pathlib import Path
import pytest
from chat_agent_bridge.config import Config, WorkspacePolicy
from chat_agent_bridge import desktop_settings as ds


def test_installed_layout_separates_program_and_data(tmp_path):
    app = tmp_path / '软件 空格'
    (app / 'runtime').mkdir(parents=True)
    layout = ds.Layout.detect(app_dir=app, data_dir=tmp_path / 'state')
    assert layout.python == app / 'runtime' / 'python.exe'
    assert layout.data_dir != layout.app_dir


def test_offline_roundtrip_generates_readonly_config(tmp_path):
    root = tmp_path / '项目'
    root.mkdir()
    layout = ds.Layout.detect(app_dir=tmp_path, data_dir=tmp_path / 'state')
    settings = ds.DesktopSettings(workspaces=(WorkspacePolicy('demo', root),))
    ds.save_settings(layout, settings)
    assert ds.load_settings(layout) == settings
    config = Config.load(layout.data_dir / 'config.local.toml')
    assert config.workspace('demo').root == root
    assert not config.workspace('demo').allow_command
    assert not config.workspace('demo').allow_write


@pytest.mark.parametrize('changes', [
    {'mcp_port': 0}, {'control_port': 8899}, {'tunnel_port': True},
    {'proxy_url': 'http://evil.example:7890'}, {'proxy_url': 'http://127.0.0.1:0'},
    {'tunnel_id': 'bad\nvalue'}, {'autostart': 'true'},
])
def test_invalid_settings_do_not_replace_valid_file(tmp_path, changes):
    layout = ds.Layout.detect(app_dir=tmp_path, data_dir=tmp_path / 'state')
    ds.save_settings(layout, ds.DesktopSettings())
    old = (layout.data_dir / 'settings.json').read_bytes()
    with pytest.raises(ValueError):
        ds.save_settings(layout, replace(ds.DesktopSettings(), **changes))
    assert (layout.data_dir / 'settings.json').read_bytes() == old


def test_workspace_names_and_paths_are_validated(tmp_path):
    layout = ds.Layout.detect(app_dir=tmp_path, data_dir=tmp_path / 'state')
    for workspaces in [
        (WorkspacePolicy('x', tmp_path / 'missing'),),
        (WorkspacePolicy('x', tmp_path), WorkspacePolicy('x', tmp_path)),
        (WorkspacePolicy('', tmp_path),),
    ]:
        with pytest.raises(ValueError):
            ds.save_settings(layout, ds.DesktopSettings(workspaces=workspaces))


def test_failed_commit_restores_both_settings_files(tmp_path, monkeypatch):
    layout = ds.Layout.detect(app_dir=tmp_path, data_dir=tmp_path / 'state')
    ds.save_settings(layout, ds.DesktopSettings())
    old = ds.load_settings(layout)
    real = ds.os.replace
    def fail(source, target):
        if Path(target).name == 'config.local.toml':
            raise OSError('disk failure')
        return real(source, target)
    monkeypatch.setattr(ds.os, 'replace', fail)
    with pytest.raises(OSError):
        ds.save_settings(layout, replace(old, proxy_url='http://127.0.0.1:7890'))
    assert ds.load_settings(layout) == old
