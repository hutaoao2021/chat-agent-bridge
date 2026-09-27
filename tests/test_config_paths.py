from pathlib import Path
import subprocess
import os
import pytest
from chat_agent_bridge.config import Config

def test_paths_reject_escape_and_absolute(tmp_path):
    from chat_agent_bridge.paths import resolve_in_root
    root = tmp_path / 'root'; root.mkdir()
    (root / 'a.txt').write_text('a')
    assert resolve_in_root(root, 'a.txt') == root / 'a.txt'
    for value in ['../outside', str(tmp_path), '', 'C:relative']:
        with pytest.raises((PermissionError, ValueError)):
            resolve_in_root(root, value)
    assert resolve_in_root(root, 'new.txt', allow_missing_leaf=True) == root / 'new.txt'

def test_link_escape(tmp_path):
    from chat_agent_bridge.paths import resolve_in_root
    root = tmp_path / 'root'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'secret').write_text('private')
    link = root / 'link'
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError):
        resolve_in_root(root, 'link/secret')

def test_config_default_read_only_and_invalid_roots(tmp_path):
    from chat_agent_bridge.config import Config
    cfg = tmp_path / 'config.toml'
    cfg.write_text(f'[[workspaces]]\nname="test"\nroot={str(tmp_path.as_posix())!r}\n', encoding='utf-8')
    policy = Config.load(cfg).workspace('test')
    assert not policy.allow_write and not policy.allow_command
    with pytest.raises(PermissionError):
        Config.load(cfg).workspace('unknown')
    cfg.write_text('[[workspaces]]\nname="x"\nroot="nonexistent"\n')
    with pytest.raises(ValueError):
        Config.load(cfg)

def test_duplicate_workspaces_rejected(tmp_path):
    from chat_agent_bridge.config import Config
    cfg=tmp_path/'config.toml'
    item=f'[[workspaces]]\nname="x"\nroot={str(tmp_path.as_posix())!r}\n'
    cfg.write_text(item+item)
    with pytest.raises(ValueError): Config.load(cfg)
@pytest.mark.parametrize('body',[
    "allow_write='false'",
    "allow_command=1",
    "allow_git_write='true'",
])
def test_permissions_require_actual_toml_booleans(tmp_path,body):
    config=tmp_path/'config.toml'
    config.write_text("[[workspaces]]\nname='w'\nroot='.'\n"+body,encoding='utf-8')
    with pytest.raises(ValueError):Config.load(config)

def test_ports_must_be_distinct_and_valid(tmp_path):
    config=tmp_path/'config.toml'
    for ports in [(8899,8899),(70000,8900)]:
        config.write_text(f'[server]\nport={ports[0]}\nextension_port={ports[1]}',encoding='utf-8')
        with pytest.raises(ValueError):Config.load(config)
