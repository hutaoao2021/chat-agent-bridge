from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from chat_agent_bridge import desktop_controller as dc, desktop_settings as ds, supervisor as sp
from chat_agent_bridge.config import WorkspacePolicy
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.jobs import JobManager

def test_unavailable_project_preserves_editor_settings(tmp_path):
    project=tmp_path/'project';project.mkdir()
    layout=ds.Layout.detect(tmp_path,tmp_path/'data')
    settings=ds.DesktopSettings(tunnel_id='tunnel_example',proxy_url='http://127.0.0.1:7890',workspaces=(WorkspacePolicy('project',project),))
    ds.save_settings(layout,settings);project.rename(tmp_path/'moved')
    assert ds.load_settings(layout)==settings
    with pytest.raises(ValueError):ds.save_settings(layout,settings)

def test_corrupt_settings_cannot_be_saved_as_defaults(monkeypatch):
    from chat_agent_bridge.desktop import Manager
    actions=[]
    field=lambda value:SimpleNamespace(get=lambda:value)
    manager=SimpleNamespace(_load_error=True,root=None,settings=ds.DesktopSettings(),fields={},workspaces=[],ssh_targets=[],autostart=field(False),secret=field(''),action=lambda *args:actions.append(args))
    monkeypatch.setattr('chat_agent_bridge.desktop.messagebox.showerror',lambda *args,**kwargs:None)
    Manager.save(manager)
    assert not actions

def test_explicit_reset_backs_up_corruption_and_preserves_key_and_tasks(tmp_path,monkeypatch):
    layout=ds.Layout.detect(tmp_path,tmp_path/'data');layout.data_dir.mkdir()
    broken=b'{damaged configuration'
    (layout.data_dir/'settings.json').write_bytes(broken)
    key=layout.data_dir/'autostart/tunnel-key.dpapi';key.parent.mkdir();key.write_bytes(b'encrypted-example')
    store=TaskStore(layout.data_dir/'state.db');task=store.begin(store.register_chat_binding('synthetic','tab',30),'keep','one')
    monkeypatch.setattr(dc,'set_autostart',lambda *args:None)
    backup=dc.DesktopController(layout).backup_reset_settings()
    assert (backup/'settings.json').read_bytes()==broken
    assert key.read_bytes()==b'encrypted-example'
    assert store.get(task.id).goal=='keep'
    assert ds.load_settings(layout)==ds.DesktopSettings()

def test_stale_pid_does_not_block_stop_or_show_alive(tmp_path):
    layout=ds.Layout.detect(tmp_path,tmp_path/'data');folder=layout.data_dir/'autostart';folder.mkdir(parents=True)
    (folder/'supervisor.pid').write_text('99999999')
    before=time.monotonic();dc.DesktopController(layout).stop()
    assert time.monotonic()-before<2
    assert not sp.supervisor_alive(layout)

def test_accepted_stop_cancels_a_pending_start(tmp_path):
    layout=ds.Layout.detect(tmp_path,tmp_path/'data')
    generation=sp.prepare_start(layout)
    sp.request_stop(layout)
    assert not sp.start_requested(layout,generation)

def test_blank_workspace_path_is_rejected():
    from chat_agent_bridge.desktop import workspace_from_input
    with pytest.raises(ValueError,match='目录'):
        workspace_from_input('project','   ',(True,True,False))

def test_installed_default_layout_uses_runtime_parent(tmp_path,monkeypatch):
    app=tmp_path/'installed';runtime=app/'runtime';runtime.mkdir(parents=True)
    monkeypatch.setattr(ds,'__file__',str(runtime/'Lib/site-packages/chat_agent_bridge/desktop_settings.py'))
    layout=ds.Layout.detect()
    assert layout.app_dir==app
    assert layout.python==runtime/'python.exe'

def wait(manager,job):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        chunk=manager.poll(job.id,0)
        if chunk.status in ('completed','failed','cancelled'):return chunk
        time.sleep(.05)
    raise AssertionError('No terminal job receipt')

def test_import_moves_completed_receipts_and_outputs(tmp_path):
    source=tmp_path/'old';project=source/'demo';project.mkdir(parents=True)
    (source/'config.local.toml').write_text('[[workspaces]]\nname="demo"\nroot="demo"\nallow_command=true\n')
    store=TaskStore(source/'.data/state.db');task=store.begin(store.register_chat_binding('synthetic','tab',30),'read','k')
    manager=JobManager(store,source/'.data/jobs/demo',WorkspacePolicy('demo',project,allow_command=True))
    job=manager.start([sys.executable,'-c',"print('saved evidence')"],project,task.id,'one');assert wait(manager,job).status=='completed'
    layout=ds.Layout.detect(tmp_path/'app',tmp_path/'new-data')
    dc.import_legacy(layout,source)
    (source/'.data/jobs').rename(source/'.data/retired-jobs')
    imported=JobManager(TaskStore(layout.data_dir/'state.db'),layout.data_dir/'jobs/demo',manager.policy)
    chunk=imported.poll(job.id,0)
    assert chunk.status=='completed' and 'saved evidence' in chunk.stdout
    assert imported._record(job.id)['log_dir']==str(layout.data_dir/'jobs/demo')

def test_import_rejects_unfinished_job_without_touching_source(tmp_path):
    source=tmp_path/'old';(source/'demo').mkdir(parents=True)
    (source/'config.local.toml').write_text('[[workspaces]]\nname="demo"\nroot="demo"\n')
    store=TaskStore(source/'.data/state.db');store.put_record('job','synthetic',{'id':'a'*32,'log_dir':str(source/'.data/jobs/demo'),'status':'running'})
    layout=ds.Layout.detect(tmp_path/'app',tmp_path/'new-data')
    with pytest.raises(ValueError,match='任务'):dc.import_legacy(layout,source)
    assert not layout.data_dir.exists()
    assert store.record('job','synthetic')['status']=='running'

def test_persistent_runner_survives_service_stop_and_blocks_maintenance(tmp_path):
    from chat_agent_bridge.process_scope import OwnedProcess
    project=tmp_path/'project';project.mkdir()
    data=tmp_path/'data';store=TaskStore(data/'state.db');task=store.begin(store.register_chat_binding('synthetic','tab',30),'job','k')
    script="""import sys,time
from pathlib import Path
from chat_agent_bridge.state import TaskStore
from chat_agent_bridge.jobs import JobManager
from chat_agent_bridge.config import WorkspacePolicy
data,project,task=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
m=JobManager(TaskStore(data/'state.db'),data/'jobs/project',WorkspacePolicy('project',project,allow_command=True),max_jobs=1)
j=m.start([sys.executable,'-c',"import time; time.sleep(2); print('persistent-done')"],project,task,'one')
(data/'started').write_text(j.id)
time.sleep(30)
"""
    owner=OwnedProcess([sys.executable,'-c',script,str(data),str(project),task.id],allow_breakaway=True)
    try:
        deadline=time.monotonic()+6
        while not (data/'started').exists() and time.monotonic()<deadline:time.sleep(.05)
        assert (data/'started').exists()
        manager=JobManager(store,data/'jobs/project',WorkspacePolicy('project',project,allow_command=True),max_jobs=1)
        job=manager._record((data/'started').read_text())
        layout=replace(ds.Layout.detect(tmp_path,data),python=Path(sys.executable))
        with pytest.raises(ValueError,match='任务'):dc.assert_maintenance_ready(layout)
        owner.terminate_owned();owner.process.wait(timeout=5)
        chunk=wait(manager,SimpleNamespace(id=job['id']))
        assert chunk.status=='completed' and 'persistent-done' in chunk.stdout
        dc.assert_maintenance_ready(layout)
        following=manager.start([sys.executable,'-c','pass'],project,task.id,'two');assert wait(manager,following).status=='completed'
    finally:
        owner.terminate_owned();owner.process.wait(timeout=5)
