import base64
import json
import subprocess
import sys
import time
from pathlib import Path
import pytest
from chat_agent_bridge.config import Config, SSHTarget
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.ssh_ops import SSHManager

def test_transport_and_recover(tmp_path):
    root=tmp_path/'remote'; root.mkdir()
    target=SSHTarget('host','my-alias','/srv/work','/opt/bridge.py',True,True)
    store=TaskStore(tmp_path/'state.db')
    task=store.begin(store.register_chat_binding('c','a',30),'goal','key')
    calls=[]
    def transport(argv,timeout):
        calls.append(argv)
        assert argv[:5]==['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10']
        payload=json.loads(base64.urlsafe_b64decode(argv[-1])); payload['root']=str(root)
        encoded=base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return subprocess.run([sys.executable,str(Path('scripts/remote_job_runner.py').resolve()),argv[-2],encoded],capture_output=True,timeout=timeout)
    m=SSHManager(Config({}, {'host':target}),store,transport=transport)
    with pytest.raises(PermissionError): m.run('missing',['pwd'])
    with pytest.raises(PermissionError): m.read_text('host','../secret')
    m.write_text('host','a.txt','hello','')
    assert m.read_text('host','a.txt')['text']=='hello'
    argv=[sys.executable,'-c',"import time; time.sleep(.2); print('done')"]
    job=m.start_job('host',argv,task.id,'j')
    assert m.start_job('host',argv,task.id,'j').id==job.id
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        chunk=m.poll_job(job.id,0)
        if chunk.status=='completed': break
        time.sleep(.05)
    assert chunk.exit_code==0 and 'done' in chunk.stdout
    assert calls[0][5]=='my-alias'

def test_disconnect_never_restarts(tmp_path):
    s=TaskStore(tmp_path/'s.db'); t=s.begin(s.register_chat_binding('c','a',30),'goal','k')
    calls=[]
    def lost(argv,timeout):
        calls.append(argv[-2]); return subprocess.CompletedProcess(argv,255,b'',b'connection lost')
    m=SSHManager(Config({}, {'h':SSHTarget('h','alias','/srv/work','/opt/runner.py',allow_command=True)}),s,transport=lost)
    j=m.start_job('h',['echo','hello'],t.id,'j')
    assert j.status=='unknown'
    assert m.start_job('h',['echo','hello'],t.id,'j').id==j.id
    assert calls==['start','query']

def test_remote_job_limit_counts_uncertain_jobs(tmp_path):
    s=TaskStore(tmp_path/'s.db');t=s.begin(s.register_chat_binding('c','a',30),'goal','k')
    def lost(argv,timeout):return subprocess.CompletedProcess(argv,255,b'',b'lost')
    m=SSHManager(Config({}, {'h':SSHTarget('h','alias','/srv/work','/opt/runner.py',allow_command=True)},max_jobs=1),s,transport=lost)
    assert m.start_job('h',['echo','1'],t.id,'j1').status=='unknown'
    with pytest.raises(ValueError,match='limit'):m.start_job('h',['echo','2'],t.id,'j2')

def test_unicode_remote_transport(tmp_path):
    root=tmp_path/'remote';root.mkdir();(root/'chinese.txt').write_bytes(('中'*30000).encode())
    target=SSHTarget('h','alias','/srv/work','/opt/runner.py')
    def transport(argv,timeout):
        payload=json.loads(base64.urlsafe_b64decode(argv[-1]));payload['root']=str(root)
        encoded=base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return subprocess.run([sys.executable,'scripts/remote_job_runner.py',argv[-2],encoded],capture_output=True,timeout=timeout)
    m=SSHManager(Config({}, {'h':target}),TaskStore(tmp_path/'s.db'),transport=transport)
    result=m.read_text('h','chinese.txt')
    assert result['text'] and len(result['text'].encode())<=65536

def test_remote_cancel_before_acceptance_is_a_tombstone(tmp_path):
    import runpy
    execute=runpy.run_path('scripts/remote_job_runner.py')['execute']
    payload={'root':str(tmp_path),'job_id':'a'*64}
    execute('cancel',payload)
    result=execute('start',{**payload,'argv':[sys.executable,'-c','pass'],'timeout':30,'max_log_bytes':10000})
    assert result['status']=='cancelled'

def test_short_remote_output_has_serializable_byte_budget(tmp_path):
    import runpy
    execute=runpy.run_path('scripts/remote_job_runner.py')['execute']
    result=execute('run',{'root':str(tmp_path),'argv':[sys.executable,'-X','utf8','-c',"print('中'*30000)"],'timeout':5})
    assert len(result['stdout'].encode())<=24000 and result['truncated']
