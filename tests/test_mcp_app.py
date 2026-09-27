from starlette.testclient import TestClient
from chat_agent_bridge.config import Config,WorkspacePolicy
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.app import create_app

def test_mcp_handshake_and_read(tmp_path):
    (tmp_path/'a.txt').write_bytes(b'hello\n')
    store=TaskStore(tmp_path/'s.db')
    app=create_app(Config({'w':WorkspacePolicy('w',tmp_path)}),store,tmp_path/'logs')
    headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json','Host':'127.0.0.1:8899'}
    def call(client,method,params,id=1):
        return client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':id,'method':method,'params':params}).json()
    with TestClient(app) as client:
        init=call(client,'initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
        assert init['result']['serverInfo']['name']=='Chat Agent Bridge'
        names={x['name'] for x in call(client,'tools/list',{})['result']['tools']}
        assert {'workspace_read_text','task_begin','job_start'}<=names
        read=call(client,'tools/call',{'name':'workspace_read_text','arguments':{'workspace':'w','relative':'a.txt'}})
        assert 'hello' in str(read['result'])
        denied=call(client,'tools/call',{'name':'workspace_write_text','arguments':{'workspace':'w','relative':'a.txt','content':'bad','expected_sha256':'','task_id':'missing','idempotency_key':'k'}})
        assert denied['result']['isError']
