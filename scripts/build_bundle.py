"""Build an offline application tree from verified, clean upstream inputs."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from verify_bundle import verify_bundle

def sha(path):
    return hashlib.file_digest(Path(path).open('rb'), 'sha256').hexdigest()

def run(argv, **kwargs):
    subprocess.run([str(x) for x in argv], check=True, **kwargs)

def build(source, python_root, tunnel_root, output):
    source, python_root, tunnel_root, output = map(lambda p: Path(p).resolve(), (source, python_root, tunnel_root, output))
    output.mkdir(parents=True, exist_ok=True)
    stage = output / 'bundle'
    if stage.exists():
        raise ValueError('Output bundle already exists; use a fresh output directory')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for key in ('PYTHONPATH', 'PYTHONHOME', 'CONTROL_PLANE_API_KEY', 'OPENAI_API_KEY'):
        env.pop(key, None)
    py = python_root / 'python.exe'
    run([py, '-I', '-B', '-c', "import sys,struct,tkinter; assert sys.version_info[:3]==(3,14,3); assert struct.calcsize('P')==8"], env=env)
    wheelhouse = output / 'wheelhouse'
    wheelhouse.mkdir()
    run([py, '-I', '-B', '-m', 'pip', 'download', '--only-binary=:all:', '-d', wheelhouse,
         '-c', source/'requirements.lock', 'mcp==2.2.0', 'psutil==7.2.2'], env=env)
    run([py, '-I', '-B', '-m', 'pip', 'wheel', '--no-deps', '-w', wheelhouse, source], env=env)
    shutil.copytree(python_root, stage/'runtime', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    runtime = stage/'runtime/python.exe'
    run([runtime, '-I', '-B', '-m', 'pip', 'install', '--no-index', '--no-compile', '--find-links', wheelhouse,
         '-c', source/'requirements.lock', 'chat-agent-bridge==0.2.2'], env=env)
    for folder in ('extension', 'docs'):
        shutil.copytree(source/folder, stage/folder, ignore=shutil.ignore_patterns('tests', 'superpowers', '__pycache__', '*.pyc'))
    shutil.copytree(tunnel_root, stage/'tools/tunnel-client')
    licenses = stage/'licenses'; licenses.mkdir()
    shutil.copy2(python_root/'LICENSE.txt', licenses/'PYTHON-LICENSE.txt')
    shutil.copy2(tunnel_root/'LICENSE', licenses/'TUNNEL-LICENSE.txt')
    shutil.copy2(tunnel_root/'NOTICE', licenses/'TUNNEL-NOTICE.txt')
    shutil.copy2(tunnel_root/'tunnel-client-v0.0.15-windows-amd64-licenses.txt', licenses/'tunnel-dependencies.txt')
    shutil.copy2(source/'THIRD-PARTY-NOTICES.md', stage/'THIRD-PARTY-NOTICES.md')
    # Installed wheel metadata includes each dependency's license and provenance.
    code = "import importlib.metadata,json; print(json.dumps([{'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License',''),'license_files':[str(p) for p in (d.files or []) if 'license' in str(p).lower() or 'copying' in str(p).lower()]} for d in importlib.metadata.distributions()]))"
    metadata = json.loads(subprocess.check_output([runtime, '-I', '-B', '-c', code], env=env, text=True))
    (licenses/'python-dependencies.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    verify_bundle(stage, run_imports=True)
    with zipfile.ZipFile(output/'ChatAgentBridge-Extension-0.2.2.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted((stage/'extension').rglob('*')):
            if p.is_file(): archive.write(p, p.relative_to(stage/'extension'))
    manifest = {'version':'0.2.2', 'python':'3.14.3', 'tunnel_client':'v0.0.15',
                'sources':{'python':'https://www.python.org/ftp/python/3.14.3/',
                           'tunnel':'https://github.com/openai/tunnel-client/releases/tag/v0.0.15'},
                'python_exe_sha256':sha(runtime), 'tunnel_exe_sha256':sha(stage/'tools/tunnel-client/tunnel-client.exe'),
                'dependencies':metadata, 'acceptance':{'clean_windows':False, 'real_account':False}}
    (output/'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return stage

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    for name in ('source','python-root','tunnel-root','output'): p.add_argument('--'+name, required=True, type=Path)
    a=p.parse_args(); print(build(a.source,a.python_root,a.tunnel_root,a.output))
