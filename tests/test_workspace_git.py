import hashlib
import subprocess
import pytest
from chat_agent_bridge.config import WorkspacePolicy
from chat_agent_bridge.workspace import WorkspaceTools
from chat_agent_bridge.git_ops import GitTools

def test_edits_bounds_and_sha(tmp_path):
    p=WorkspacePolicy('w',tmp_path,allow_write=True)
    w=WorkspaceTools(p)
    (tmp_path/'a.txt').write_bytes(b'same\nsame\nthird\n')
    assert w.read_text('a.txt',2,1)['text']=='same\n'
    assert len(w.search_text('same',limit=1))==1
    with pytest.raises(ValueError): w.replace_text('a.txt','same','new',1)
    with pytest.raises(ValueError): w.write_text('a.txt','bad','wrong')
    result=w.replace_text('a.txt','same','new',2)
    assert result['sha256']==hashlib.sha256(b'new\nnew\nthird\n').hexdigest()
    with pytest.raises(PermissionError): WorkspaceTools(WorkspacePolicy('r',tmp_path)).write_text('a.txt','no',result['sha256'])

def test_patch_all_or_nothing(tmp_path):
    w=WorkspaceTools(WorkspacePolicy('w',tmp_path,allow_write=True))
    for name in ['a','b']: (tmp_path/name).write_bytes(b'old\n')
    hashes={name:hashlib.sha256(b'old\n').hexdigest() for name in ['a','b']}
    patch='--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n--- a/b\n+++ b/b\n@@ -1 +1 @@\n-wrong\n+new\n'
    with pytest.raises(ValueError): w.apply_patch(patch,hashes)
    assert (tmp_path/'a').read_text()=='old\n'
    w.apply_patch(patch.replace('-wrong','-old'),hashes)
    assert (tmp_path/'b').read_text()=='new\n'
    with pytest.raises(PermissionError): w.apply_patch('--- a/../a\n+++ b/../a\n@@ -1 +1 @@\n-new\n+x\n',{})

def test_git_status_and_policy(tmp_path):
    subprocess.run(['git','init',str(tmp_path)],check=True,capture_output=True)
    (tmp_path/'a').write_text('text')
    g=GitTools(WorkspacePolicy('w',tmp_path))
    assert '?? a' in g.status()['stdout']
    with pytest.raises(PermissionError): g.run_write(['add','a'])
    writer=GitTools(WorkspacePolicy('w',tmp_path,allow_git_write=True))
    writer.run_write(['add','a'])
    assert 'text' in writer.diff([],staged=True)['stdout']
    with pytest.raises(PermissionError): writer.run_write(['clean','-fd'])
