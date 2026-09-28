import queue
from types import SimpleNamespace
from chat_agent_bridge import desktop_controller as dc
from chat_agent_bridge.config import WorkspacePolicy
from chat_agent_bridge.desktop import Manager
from chat_agent_bridge.desktop_settings import DesktopSettings, Layout, save_settings


def test_files_and_tcp_do_not_confirm_runtime_credentials_or_cloud(tmp_path, monkeypatch):
    layout = Layout.detect(tmp_path/'app', tmp_path/'data')
    save_settings(layout, DesktopSettings(proxy_url='http://127.0.0.1:7890'))
    for path in (layout.python, layout.pythonw, layout.tunnel_client, layout.data_dir/'autostart/tunnel-key.dpapi'):
        path.parent.mkdir(parents=True, exist_ok=True); path.touch()
    monkeypatch.setattr(dc.Probe, 'bridge', lambda *args: 'ready')
    monkeypatch.setattr(dc.Probe, 'proxy', lambda *args: True)
    monkeypatch.setattr(dc, 'occupied', lambda *args: True)
    monkeypatch.setattr(dc, 'local_json', lambda *args: {'control_plane_connected': True})
    monkeypatch.setattr(dc, 'read_key', lambda *args: 'synthetic')
    monkeypatch.setattr(dc, 'supervisor_alive', lambda *args: True)
    checks = {row['component']: row for row in dc.collect_diagnostics(layout)}
    for component in ('运行环境', '凭据', '代理', 'Tunnel 客户端', 'Tunnel'):
        assert checks[component]['status'] == 'unknown', component
    assert '健康接口' in checks['Bridge']['message']
    assert '云端' in checks['Tunnel']['message']


def test_unavailable_workspace_is_reported_without_losing_configuration(tmp_path):
    project = tmp_path/'project'; project.mkdir()
    layout = Layout.detect(tmp_path/'app', tmp_path/'data')
    save_settings(layout, DesktopSettings(workspaces=(WorkspacePolicy('demo', project),)))
    project.rename(tmp_path/'moved')
    checks = {row['component']: row for row in dc.collect_diagnostics(layout)}
    assert checks['工作区']['status'] == 'error'
    assert checks['工作区']['code'] == 'workspace_unavailable'
    assert '1' in checks['工作区']['message']


def test_start_response_is_a_request_not_service_readiness():
    results = queue.Queue(); results.put(('启动', None, None))
    messages = []
    manager = SimpleNamespace(results=results, busy=True, buttons=[],
                              message=SimpleNamespace(set=messages.append),
                              root=SimpleNamespace(after=lambda *args: None))
    manager.consume = lambda: Manager.consume(manager)
    Manager.consume(manager)
    assert '请求' in messages[-1]
    assert '启动完成' not in messages[-1]
