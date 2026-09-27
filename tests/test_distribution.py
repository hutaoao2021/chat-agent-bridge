import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('verify_bundle', Path(__file__).parents[1] / 'scripts/verify_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

def test_missing_runtime_rejected(tmp_path):
    with pytest.raises(ValueError, match='python.exe'):
        bundle.verify_bundle(tmp_path)

@pytest.mark.parametrize('name', ['.data/state.db', 'config.local.toml', '.venv/pyvenv.cfg', '../outside', 'C:/private/file', 'runtime/secret.dpapi'])
def test_private_paths_rejected(name):
    with pytest.raises(ValueError, match='forbidden'):
        bundle.validate_distribution_files([name])

def test_valid_install_paths_allowed():
    bundle.validate_distribution_files(['runtime/python.exe', 'docs/user-guide.md', 'extension/manifest.json'])

def test_partial_bundle_rejected(tmp_path):
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'runtime/python.exe').touch()
    with pytest.raises(ValueError, match='pythonw.exe'):
        bundle.verify_bundle(tmp_path)
