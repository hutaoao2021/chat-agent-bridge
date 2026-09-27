"""Real HTTP MCP client and service restart, with a detached child still running."""
import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import psutil
from mcp import Client
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.auth import PairingManager
from chat_agent_bridge.process_scope import OwnedProcess

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def unpack(result):
    assert not result.is_error, str(result)
    if result.structured_content is not None:return result.structured_content
    return json.loads(result.content[0].text)

def test_http_restart_acceptance(tmp_path,monkeypatch):
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    asyncio.run(asyncio.wait_for(flow(tmp_path), 35))

async def flow(tmp_path):
    root=tmp_path/'workspace';root.mkdir();(root/'a.txt').write_bytes(b'old\n')
    mcp_port,control_port=port(),port()
    config=tmp_path/'config.toml'
    config.write_text(f"[server]\nport={mcp_port}\nextension_port={control_port}\n[[workspaces]]\nname='w'\nroot='{root.as_posix()}'\nallow_write=true\nallow_command=true\n",encoding='utf-8')
    data=tmp_path/'data'; data.mkdir()
    store=TaskStore(data/'state.db');auth=PairingManager(store)
    code=auth.issue_code();origin='chrome-extension://'+'a'*32
    process=None; output=(tmp_path/'service.log').open('ab')
    def launch():
        owner=OwnedProcess([sys.executable,'-m','chat_agent_bridge.main','--config',str(config),'--data-dir',str(data)],allow_breakaway=True,stdout=output,stderr=output)
        owner.process._bridge_owner=owner
        return owner.process
    async def ready():
        async with httpx.AsyncClient(trust_env=False,timeout=2) as http:
            for _ in range(100):
                if process.poll() is not None:raise AssertionError((tmp_path/'service.log').read_text())
                try:
                    if (await http.get(f'http://127.0.0.1:{control_port}/healthz')).status_code==200:return
                except httpx.HTTPError:pass
                await asyncio.sleep(.05)
        raise AssertionError('service did not become ready')
    try:
        process=launch();await ready()
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{control_port}',headers={'X-Bridge-Origin':origin},trust_env=False) as http:
            paired=await http.post('/local/v1/pair',json={'code':code}); assert paired.status_code==200
            http.headers['Authorization']='Bearer '+paired.json()['token']
            binding=(await http.post('/local/v1/bind',json={'conversation_id':'chat-test','tab_id':'1'})).json()['binding_code']
            async with Client(f'http://127.0.0.1:{mcp_port}/mcp', mode='legacy', read_timeout_seconds=10) as client:
                task=unpack(await client.call_tool('task_begin',{'binding_code':binding,'goal':'edit and test','idempotency_key':'task'}))
                read=unpack(await client.call_tool('workspace_read_text',{'workspace':'w','relative':'a.txt'}))
                edit={'workspace':'w','relative':'a.txt','content':'new\n','expected_sha256':read['sha256'],'task_id':task['id'],'idempotency_key':'edit'}
                first=unpack(await client.call_tool('workspace_write_text',edit))
                assert unpack(await client.call_tool('workspace_write_text',edit))==first
                args={'workspace':'w','argv':[sys.executable,'-c',"import time; print('ready',flush=True); time.sleep(2); print('done')"],'task_id':task['id'],'idempotency_key':'job'}
                approval=unpack(await client.call_tool('job_start',args))['approval']
                assert (await http.post('/local/v1/approve',json={'approval_id':approval['id'],'approved':True})).status_code==200
                args['approval_id']=approval['id']
                job=unpack(await client.call_tool('job_start',args))
                assert unpack(await client.call_tool('job_start',args))['id']==job['id']
            stop_service(process)
            process=launch();await ready()
            async with Client(f'http://127.0.0.1:{mcp_port}/mcp', mode='legacy', read_timeout_seconds=10) as client:
                for _ in range(100):
                    chunk=unpack(await client.call_tool('job_poll',{'workspace':'w','job_id':job['id']}))
                    if chunk['status']=='completed':break
                    await asyncio.sleep(.05)
                assert chunk['exit_code']==0 and 'done' in chunk['stdout']
                assert unpack(await client.call_tool('job_start',args))['id']==job['id']
                task=unpack(await client.call_tool('task_get',{'task_id':task['id']}))
                task=unpack(await client.call_tool('task_checkpoint',{'task_id':task['id'],'expected_version':task['version'],'text':'Edit verified and job passed.'}))
                finished=unpack(await client.call_tool('task_finish',{'task_id':task['id'],'expected_version':task['version']}))
                assert finished['status']=='completed'
            assert (root/'a.txt').read_bytes()==b'new\n'
    finally:
        if process and process.poll() is None:stop_service(process)
        output.close()

def stop_service(process):
    # Use the same owned scope as desktop startup. Full Python has no venv
    # launcher child; killing all direct children would kill job_runner itself.
    process._bridge_owner.terminate_owned();process.wait(timeout=5)
