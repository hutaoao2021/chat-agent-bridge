import sys
import time
import pytest
from chat_agent_bridge.config import WorkspacePolicy
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.jobs import JobManager
from chat_agent_bridge.job_runner import stop_tree
import json
import psutil

def fixture(tmp_path):
    root = tmp_path/'work'; root.mkdir()
    s = TaskStore(tmp_path/'state.db')
    t = s.begin(s.register_chat_binding('c','a',30),'goal','k')
    policy = WorkspacePolicy('work',root,allow_command=True)
    return s,t,policy,JobManager(s,tmp_path/'logs',policy)

def wait(manager, job, seconds=10):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        chunk=manager.poll(job.id,0)
        if chunk.status in {'completed','failed','cancelled','unknown'}: return chunk
        time.sleep(.05)
    raise AssertionError('job timed out')

def test_restart_and_duplicate(tmp_path):
    s,t,p,m=fixture(tmp_path)
    argv=[sys.executable,'-c',"import time; print('ready',flush=True); time.sleep(.5); print('done')"]
    j=m.start(argv,p.root,t.id,'job')
    assert m.start(argv,p.root,t.id,'job').id==j.id
    restarted=JobManager(s,tmp_path/'logs',p)
    chunk=wait(restarted,j)
    assert chunk.exit_code==0 and 'done' in chunk.stdout
    assert restarted.start(argv,p.root,t.id,'job').id==j.id
    with pytest.raises(ValueError): m.start([sys.executable,'-c','pass'],p.root,t.id,'job')
    with pytest.raises(PermissionError): m.start(argv,tmp_path,t.id,'other')

def test_output_and_stdin(tmp_path):
    s,t,p,m=fixture(tmp_path)
    j=m.start([sys.executable,'-c',"print(input()); print('x'*100000)"],p.root,t.id,'j')
    m.write_stdin(j.id,'hello\n')
    chunk=wait(m,j)
    assert len(chunk.stdout.encode())<=65536 and 'hello' in chunk.stdout
    second=m.poll(j.id,chunk.next_cursor)
    assert len(second.stdout)>30000

def test_cancel(tmp_path):
    s,t,p,m=fixture(tmp_path)
    j=m.start([sys.executable,'-c','import time; time.sleep(30)'],p.root,t.id,'j')
    m.cancel(j.id)
    assert wait(m,j).status=='cancelled'

def test_stdin_backpressure_cannot_block_cancel(tmp_path):
    s,t,p,m=fixture(tmp_path)
    j=m.start([sys.executable,'-c','import time; time.sleep(30)'],p.root,t.id,'j')
    try:
        m.write_stdin(j.id,'x'*65536);time.sleep(.3);m.cancel(j.id)
        assert wait(m,j,3).status=='cancelled'
    finally:
        ident=json.loads((m.log_dir/j.id/'identity.json').read_text())
        try:
            if abs(psutil.Process(ident['pid']).create_time()-ident['created_at'])<.001:stop_tree(ident['pid'])
        except psutil.NoSuchProcess:pass

def test_descendant_is_gone_before_completion_receipt(tmp_path):
    s,t,p,m=fixture(tmp_path)
    command="import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True)"
    j=m.start([sys.executable,'-c',command],p.root,t.id,'j')
    pid=None
    try:
        chunk=wait(m,j,10);pid=int(chunk.stdout.strip())
        assert not psutil.pid_exists(pid), 'completed job left descendant alive'
    finally:
        if pid and psutil.pid_exists(pid):stop_tree(pid)

def test_partial_log_truncation_is_reported(tmp_path):
    s,t,p,m=fixture(tmp_path);m.max_log_bytes=20
    j=m.start([sys.executable,'-c',"print('x'*30)"],p.root,t.id,'j')
    chunk=wait(m,j)
    assert chunk.truncated and len(chunk.stdout.encode())==20

def test_cancel_pending_intent_prevents_launch(tmp_path,monkeypatch):
    import chat_agent_bridge.jobs as jobs_module
    s,t,p,m=fixture(tmp_path)
    original=jobs_module.subprocess.Popen
    def race(*args,**kwargs):
        m.cancel(s.records('job')[0]['id'])
        return original(*args,**kwargs)
    monkeypatch.setattr(jobs_module.subprocess,'Popen',race)
    j=m.start([sys.executable,'-c',"from pathlib import Path; Path('effect').write_text('ran')"],p.root,t.id,'j')
    chunk=wait(m,j)
    assert chunk.status=='cancelled' and not (p.root/'effect').exists()

def test_cancelled_task_cannot_reserve_job(tmp_path):
    s,t,p,m=fixture(tmp_path);s.finish(t.id,t.version,'cancelled')
    with pytest.raises(ValueError,match='ended'):m.start([sys.executable,'-c','pass'],p.root,t.id,'j')

def test_progress_is_visible_before_exit(tmp_path):
    s,t,p,m=fixture(tmp_path)
    j=m.start([sys.executable,'-c',"import time; print('ready',flush=True); time.sleep(3)"],p.root,t.id,'j')
    try:
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            chunk=m.poll(j.id)
            if 'ready' in chunk.stdout:break
            time.sleep(.02)
        assert 'ready' in chunk.stdout and chunk.status=='running'
    finally:m.cancel(j.id)
