"""Export a checked public source tree without the development Git history."""
import argparse
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

ROOT_FILES={'README.md','CHANGELOG.md','THIRD-PARTY-NOTICES.md','pyproject.toml','requirements.lock','config.example.toml','.gitignore'}
ROOT_DIRS={'src','tests','extension','scripts','packaging','.github'}
PUBLIC_DOCS={'user-guide.md','new-computer.md','troubleshooting.md','build.md','setup.md','autostart.md','verification.md'}

def publishable_path(name):
    p=PurePosixPath(name.replace('\\','/'))
    if p.is_absolute() or ':' in str(p) or '..' in p.parts or not p.parts: return False
    if any(x.startswith(('secrets','config.local')) or x in {'.data','.tools','.venv','.git','__pycache__','node_modules'} or x.endswith('.egg-info')
           or x.endswith(('.db','.dpapi','.log','.pyc','.exe')) for x in p.parts): return False
    return (len(p.parts)==1 and name in ROOT_FILES) or p.parts[0] in ROOT_DIRS or (p.parts[0]=='docs' and len(p.parts)==2 and p.name in PUBLIC_DOCS)

def scan_text(text):
    rules={'credential':r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}',
           'personal_tunnel':r'tunnel_[A-Za-z0-9]{24,}',
           'personal_windows_path':r'[A-Za-z]:[\\/]Users[\\/][^\s"\x27]+',
           'auth_response':r'["\x27](?:conduit_token|access_token)["\x27]\s*:\s*["\x27][^"\x27]+',
           'real_task_id':r'(?:task_id|binding_code)\s*[:=]\s*["\x27]?[a-f0-9]{32}\b'}
    return [(text.count('\n',0,m.start())+1, name) for name,pattern in rules.items() for m in re.finditer(pattern,text)]

def check_tree(root):
    errors=[]
    for p in Path(root).rglob('*'):
        if not p.is_file() or '.git' in p.relative_to(root).parts: continue
        name=p.relative_to(root).as_posix()
        if not publishable_path(name) and name!='demo/hello.py': errors.append((name,0,'forbidden_path')); continue
        try: text=p.read_text(encoding='utf-8')
        except UnicodeDecodeError: errors.append((name,0,'binary_file')); continue
        errors.extend((name,line,rule) for line,rule in scan_text(text))
    if errors: raise ValueError('\n'.join(f'{name}:{line}: {rule}' for name,line,rule in errors))

def check_history(root):
    revisions=subprocess.check_output(['git','rev-list','--all'],cwd=root,text=True).splitlines()
    for rev in revisions:
        names=subprocess.check_output(['git','ls-tree','-r','--name-only',rev],cwd=root,text=True).splitlines()
        for name in names:
            if not publishable_path(name) and name!='demo/hello.py': raise ValueError(f'{rev[:7]}: forbidden path {name}')
            content=subprocess.check_output(['git','show',f'{rev}:{name}'],cwd=root).decode('utf-8')
            if scan_text(content): raise ValueError(f'{rev[:7]}: sensitive content {name}')

def prepare(source, output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.exists(): raise ValueError('Public source output must be a new directory')
    output.mkdir(parents=True)
    for p in source.rglob('*'):
        if p.is_file() and publishable_path(p.relative_to(source).as_posix()):
            target=output/p.relative_to(source); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target)
    (output/'demo').mkdir()
    (output/'demo/hello.py').write_text('def greet(name):\n    return f"Hello, {name}!" if name and name.strip() else "Hello, World!"\n\nif __name__ == "__main__":\n    print(greet("Bridge"))\n',encoding='utf-8')
    check_tree(output)
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--source',default='.',type=Path);p.add_argument('--output',type=Path);p.add_argument('--check',type=Path);p.add_argument('--history',action='store_true')
    a=p.parse_args()
    if a.check:
        check_tree(a.check)
        if a.history: check_history(a.check)
        print('Public source scan passed')
    elif a.output: print(prepare(a.source,a.output))
    else:p.error('--output or --check required')
