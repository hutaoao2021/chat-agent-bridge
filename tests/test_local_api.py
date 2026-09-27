import pytest
from starlette.testclient import TestClient
from chat_agent_bridge.config import Config,WorkspacePolicy
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.auth import PairingManager
from chat_agent_bridge.app import create_local_app
from chat_agent_bridge.app import Runtime
import sys
import time

ORIGIN='chrome-extension://'+'a'*32

def test_pair_binding_and_approval(tmp_path):
    s=TaskStore(tmp_path/'s.db'); auth=PairingManager(s)
    client=TestClient(create_local_app(Config({'w':WorkspacePolicy('w',tmp_path)}),s,auth))
    headers={'X-Bridge-Origin':ORIGIN}
    code=auth.issue_code()
    assert client.post('/local/v1/pair',json={'code':code},headers={'X-Bridge-Origin':'https://evil.test'}).status_code==403
    result=client.post('/local/v1/pair',json={'code':code},headers=headers)
    assert result.status_code==200
    headers['Authorization']='Bearer '+result.json()['token']
    assert client.post('/local/v1/pair',json={'code':code},headers=headers).status_code==403
    result=client.post('/local/v1/bind',json={'conversation_id':'c','tab_id':'tab'},headers=headers)
    t=s.begin(result.json()['binding_code'],'goal','k')
    a=s.request_approval(t.id,'digest','command')
    assert client.post('/local/v1/approve',json={'approval_id':a.id,'approved':True}).status_code==403
    assert client.get('/local/v1/status?conversation_id=c',headers=headers).json()['approvals'][0]['summary']=='command'
    assert client.post('/local/v1/approve',json={'approval_id':a.id,'approved':True},headers=headers).status_code==200
    assert client.post('/local/v1/command',json={},headers=headers).status_code==404
    assert client.post('/local/v1/revoke',json={},headers=headers).status_code==200
    assert client.get('/local/v1/status?conversation_id=c',headers=headers).status_code==403

def test_expired_pairing(tmp_path):
    auth=PairingManager(TaskStore(tmp_path/'s.db'))
    code=auth.issue_code(-1)
    with pytest.raises(PermissionError): auth.redeem(code,ORIGIN)

def test_pair_response_reports_credential_expiry(tmp_path):
    s=TaskStore(tmp_path/'s.db');auth=PairingManager(s)
    client=TestClient(create_local_app(Config({}),s,auth))
    result=client.post('/local/v1/pair',json={'code':auth.issue_code()},headers={'X-Bridge-Origin':ORIGIN})
    assert result.status_code==200
    assert 29*86400 < result.json()['expires_at']-time.time() <= 30*86400

def test_stop_signals_task_jobs(tmp_path):
    root=tmp_path/'work';root.mkdir();s=TaskStore(tmp_path/'s.db');auth=PairingManager(s)
    config=Config({'w':WorkspacePolicy('w',root,allow_command=True)})
    runtime=Runtime(config,s,tmp_path/'logs')
    client=TestClient(create_local_app(config,s,auth,runtime=runtime))
    token=auth.redeem(auth.issue_code(),ORIGIN)
    headers={'X-Bridge-Origin':ORIGIN,'Authorization':'Bearer '+token}
    t=s.begin(s.register_chat_binding('c','a',30),'goal','k')
    j=runtime.jobs['w'].start([sys.executable,'-c','import time; time.sleep(10)'],root,t.id,'j')
    response=client.post('/local/v1/stop',json={'task_id':t.id},headers=headers)
    assert response.status_code==200
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        status=runtime.jobs['w'].poll(j.id).status
        if status=='cancelled':break
        time.sleep(.05)
    if status!='cancelled':runtime.jobs['w'].cancel(j.id)
    assert status=='cancelled'
