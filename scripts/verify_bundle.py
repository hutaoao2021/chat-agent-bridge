"""Validate the installation tree before compiling or distributing it."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess

def validate_distribution_files(names):
    for name in names:
        path = PurePosixPath(name.replace('\\', '/'))
        if path.is_absolute() or ':' in str(path) or '..' in path.parts or any(
            part in {'.data', '.venv', '.git', '.superpowers', '__pycache__'} or
            part == 'config.local.toml' or part.endswith(('.dpapi', '.db', '.log'))
            for part in path.parts
        ):
            raise ValueError(f'forbidden distribution path: {name}')

def verify_bundle(root, run_imports=False):
    root = Path(root).resolve()
    for name in ['runtime/python.exe', 'runtime/pythonw.exe',
                 'runtime/DLLs/_tkinter.pyd', 'runtime/Lib/tkinter/__init__.py',
                 'runtime/Lib/site-packages/chat_agent_bridge/desktop.py',
                 'tools/tunnel-client/tunnel-client.exe', 'extension/manifest.json',
                 'docs/user-guide.md', 'docs/new-computer.md', 'docs/troubleshooting.md',
                 'THIRD-PARTY-NOTICES.md', 'licenses/PYTHON-LICENSE.txt',
                 'licenses/TUNNEL-LICENSE.txt', 'licenses/tunnel-dependencies.txt']:
        if not (root / name).is_file():
            raise ValueError(f'missing bundle file: {name}')
    if not list((root / 'runtime/tcl').glob('tcl*/init.tcl')):
        raise ValueError('missing Tcl runtime')
    names = [p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()]
    validate_distribution_files(names)
    if run_imports:
        code = "import sys, struct, tkinter, chat_agent_bridge, mcp, psutil; from pathlib import Path; assert struct.calcsize('P') == 8; assert sys.version_info[:3] == (3,14,3); assert Path(chat_agent_bridge.__file__).is_relative_to(Path(sys.prefix)); r=tkinter.Tk(); r.withdraw(); r.update(); r.destroy(); print('isolated-imports-and-tk-ok')"
        subprocess.run([str(root/'runtime/python.exe'), '-I', '-B', '-c', code], cwd=root.parent, check=True)
    return {'file_count': len(names), 'version': json.loads((root/'extension/manifest.json').read_text(encoding='utf-8'))['version']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--run-imports', action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify_bundle(args.root, args.run_imports)))
