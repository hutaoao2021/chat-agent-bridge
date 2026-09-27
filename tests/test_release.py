import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('prepare_release', Path(__file__).parents[1]/'scripts/prepare-release.py')
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

@pytest.mark.parametrize('name', ['.data/state.db', 'config.local.toml', '.venv/pyvenv.cfg', 'secrets.txt', 'dist/setup.exe', 'docs/superpowers/plan.md', '../README.md'])
def test_forbidden_release_paths(name):
    assert not release.publishable_path(name)

@pytest.mark.parametrize('name', ['README.md', 'src/chat_agent_bridge/main.py', 'tests/test_jobs.py', 'extension/content.js', 'docs/user-guide.md', 'packaging/windows/installer.iss'])
def test_source_paths_publishable(name):
    assert release.publishable_path(name)

@pytest.mark.parametrize('text', ['sk-proj-'+'testcredentialvalue12345', 'tunnel_'+'a'*32, 'C:'+chr(92)+'Users'+chr(92)+'PrivatePerson'+chr(92)+'project', '"conduit_token"'+': "private"'])
def test_secret_or_personal_text_rejected(text):
    assert release.scan_text(text)

def test_synthetic_examples_are_publishable():
    assert not release.scan_text('tunnel_替换为你的ID; %LOCALAPPDATA%\\ChatAgentBridge\\data')
