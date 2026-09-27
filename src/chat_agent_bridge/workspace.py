import fnmatch
import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading
from contextlib import ExitStack
from .paths import guarded_path, reject_link, resolve_in_root

MAX_FILE=2*1024*1024
def sha(data): return hashlib.sha256(data).hexdigest()

class WorkspaceTools:
    def __init__(self, policy): self.policy=policy; self.lock=threading.RLock()

    def _read(self, path):
        reject_link(path)
        with path.open('rb') as file:
            reject_link(path)
            info=os.fstat(file.fileno())
            if info.st_size>MAX_FILE: raise ValueError('file exceeds 2 MiB')
            if info.st_ino!=path.stat().st_ino: raise PermissionError('file changed during open')
            data=file.read(MAX_FILE+1)
        if len(data)>MAX_FILE or b'\x00' in data: raise ValueError('binary or oversized file')
        data.decode('utf-8')
        return data

    def list_files(self, relative='.', limit=200):
        if not 1<=limit<=1000: raise ValueError('invalid limit')
        root=resolve_in_root(self.policy.root,relative)
        if not root.is_dir(): raise ValueError('directory required')
        results=[]
        for directory,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in {'.git','.venv','node_modules'} and not (Path(directory)/d).is_symlink() and not (Path(directory)/d).is_junction())
            for name in sorted(files):
                path=Path(directory)/name
                if path.is_symlink(): continue
                results.append(path.relative_to(self.policy.root).as_posix())
                if len(results)>=limit: return results
        return results

    def read_text(self, relative, start_line=1, max_lines=200):
        if not 1<=start_line or not 1<=max_lines<=1000: raise ValueError('invalid line bounds')
        with guarded_path(self.policy.root,relative) as path: data=self._read(path)
        lines=data.decode().splitlines(keepends=True)
        text=''.join(lines[start_line-1:start_line-1+max_lines])
        return {'text':text[:65536],'sha256':sha(data),'total_lines':len(lines),'truncated':len(text)>65536}

    def search_text(self, query, glob='*', limit=100):
        if not query or not 1<=limit<=1000: raise ValueError('invalid search')
        found=[]
        for relative in self.list_files('.',1000):
            if not fnmatch.fnmatch(relative,glob): continue
            try: text=self.read_text(relative,1,1000)['text']
            except (UnicodeError,ValueError,PermissionError,OSError): continue
            for line,content in enumerate(text.splitlines(),1):
                if query in content:
                    found.append({'path':relative,'line':line,'text':content[:1000]})
                    if len(found)>=limit: return found
        return found

    def _check_write(self):
        if not self.policy.allow_write: raise PermissionError('writes disabled')

    def _stage(self,path,data):
        fd,name=tempfile.mkstemp(prefix='.bridge-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as file: file.write(data); file.flush(); os.fsync(file.fileno())
            if path.exists(): os.chmod(name,path.stat().st_mode)
            return Path(name)
        except BaseException: Path(name).unlink(missing_ok=True); raise

    def write_text(self,relative,content,expected_sha256):
        self._check_write(); data=content.encode('utf-8')
        if len(data)>MAX_FILE or b'\x00' in data: raise ValueError('binary or oversized content')
        with self.lock, guarded_path(self.policy.root,relative,allow_missing_leaf=True) as path:
            old=self._read(path) if path.exists() else None
            if expected_sha256!=(sha(old) if old is not None else ''): raise ValueError('SHA precondition failed; empty SHA means create only')
            tmp=self._stage(path,data)
            try:
                resolve_in_root(self.policy.root,relative,allow_missing_leaf=True)
                current=self._read(path) if path.exists() else None
                if current!=old: raise ValueError('file changed before commit')
                os.replace(tmp,path)
            finally: tmp.unlink(missing_ok=True)
        return {'path':relative,'sha256':sha(data)}

    def replace_text(self,relative,old,new,expected_occurrences=1):
        if not old or expected_occurrences<1: raise ValueError('invalid replacement')
        with self.lock, guarded_path(self.policy.root,relative) as path: data=self._read(path)
        text=data.decode()
        if text.count(old)!=expected_occurrences: raise ValueError('replacement count changed')
        return self.write_text(relative,text.replace(old,new),sha(data))

    def apply_patch(self,patch,expected_base_sha256):
        self._check_write()
        lines=patch.splitlines(keepends=True); changes=[]; index=0
        if len(patch.encode())>MAX_FILE: raise ValueError('patch too large')
        with self.lock, ExitStack() as stack:
            while index<len(lines):
                if not lines[index].startswith('--- '): raise ValueError('unified diff required; no metadata')
                oldname=lines[index][4:].strip().split('\t')[0]; index+=1
                if index>=len(lines) or not lines[index].startswith('+++ '): raise ValueError('missing new header')
                newname=lines[index][4:].strip().split('\t')[0]; index+=1
                relative=newname[2:] if newname.startswith('b/') else newname
                oldrelative=oldname[2:] if oldname.startswith('a/') else oldname
                if oldrelative!=relative: raise ValueError('patch supports existing files only; use write_text for creation')
                path=stack.enter_context(guarded_path(self.policy.root,relative))
                if any(p==path for p,_,_ in changes): raise ValueError('duplicate patch target')
                original=self._read(path)
                if expected_base_sha256.get(relative)!=sha(original): raise ValueError('patch SHA precondition failed')
                source=original.decode().splitlines(keepends=True); result=[]; pos=0; hunks=0
                while index<len(lines) and lines[index].startswith('@@ '):
                    match=re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?',lines[index])
                    if not match: raise ValueError('malformed hunk')
                    start=max(0,int(match[1])-1); oldcount=int(match[2] or 1); newcount=int(match[4] or 1)
                    if start<pos or start>len(source): raise ValueError('invalid hunk location')
                    result.extend(source[pos:start]); pos=start; index+=1; removed=added=0
                    while index<len(lines) and lines[index][:1] in {' ','-','+'} and not lines[index].startswith('--- '):
                        line=lines[index]; index+=1; marker=line[0]; value=line[1:]
                        if marker in {' ','-'}:
                            if pos>=len(source) or source[pos]!=value: raise ValueError('hunk context mismatch')
                            pos+=1; removed+=1
                        if marker in {' ','+'}: result.append(value); added+=1
                    if removed!=oldcount or added!=newcount: raise ValueError('hunk count mismatch')
                    hunks+=1
                if not hunks: raise ValueError('missing hunks')
                result.extend(source[pos:]); data=''.join(result).encode()
                if len(data)>MAX_FILE: raise ValueError('patched file too large')
                changes.append((path,original,data))
            temps=[]; committed=[]
            try:
                for path,old,new in changes: temps.append(self._stage(path,new))
                for path,old,new in changes:
                    if self._read(path)!=old: raise ValueError('file changed before commit')
                for (path,old,new),tmp in zip(changes,temps):
                    os.replace(tmp,path); committed.append((path,old))
            except BaseException:
                for path,old in reversed(committed):
                    backup=self._stage(path,old)
                    try: os.replace(backup,path)
                    finally: backup.unlink(missing_ok=True)
                raise
            finally:
                for tmp in temps: tmp.unlink(missing_ok=True)
        return [{'path':p.relative_to(self.policy.root).as_posix(),'sha256':sha(new)} for p,_,new in changes]
