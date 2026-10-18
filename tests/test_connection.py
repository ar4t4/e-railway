import os
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from companion import desktop, local_desktop, server


def test_session_auto_sync_changes_and_retries(tmp_path):
    agent = desktop.DesktopAgent({'url': 'http://localhost', 'id': 'test'}, tmp_path)
    agent.browser = Mock()
    agent.call = Mock()
    agent.browser.session.return_value = None
    agent.sync_browser_session()
    agent.call.assert_not_called()
    assert not agent.ready
    session = {'token': 'test-token', 'uudid': 'device'}
    agent.browser.session.return_value = session
    agent.sync_browser_session()
    agent.sync_browser_session()
    agent.call.assert_called_once_with('session', session)
    agent.browser.session.return_value = dict(session, token='renewed')
    agent.call.side_effect = RuntimeError('Disconnected')
    with pytest.raises(RuntimeError):
        agent.sync_browser_session()
    assert agent.synced_session == session
    agent.call.side_effect = None
    agent.sync_browser_session()
    assert agent.synced_session['token'] == 'renewed'
    agent.browser.session.return_value = None
    agent.sync_browser_session()
    assert not agent.ready and agent.synced_session is None


@pytest.mark.skipif(os.name != 'nt', reason='Local desktop launch is Windows only')
def test_local_connect_launches_and_queues_once(tmp_path, monkeypatch):
    monkeypatch.delenv('RC_PUBLIC_URL', raising=False)
    monkeypatch.delenv('RC_SETUP_TOKEN', raising=False)
    app = server.create_app(tmp_path, background=False)
    def start(store, url):
        assert url == 'http://127.0.0.1:8787'
        store.mutate(lambda s: s['agent'].update(seen=time.time()))
    launch = Mock(side_effect=start)
    monkeypatch.setattr(server, 'start_local_agent', launch)
    with TestClient(app, base_url='http://127.0.0.1:8787', client=('127.0.0.1', 50000)) as c:
        assert c.post('/api/desktop/connect').status_code == 401
        launch.assert_not_called()
        c.post('/api/auth/setup', json={'password': 'test'})
        assert c.post('/api/desktop/connect', headers={'Origin': 'https://foreign.example'}).status_code == 403
        launch.assert_not_called()
        assert c.post('/api/desktop/connect').status_code == 200
        assert c.post('/api/desktop/connect').status_code == 200
        launch.assert_called_once()
        assert app.state.store.read()['commands'] == ['connect']
        assert app.state.store.read()['mode'] == 'away'
        app.state.store.mutate(lambda s: s.update(attempts=[{'status': 'needs_attention'}]))
        assert c.post('/api/desktop/connect').status_code == 409


def test_remote_request_cannot_launch_local_process(tmp_path, monkeypatch):
    monkeypatch.delenv('RC_PUBLIC_URL', raising=False)
    monkeypatch.setenv('RC_SETUP_TOKEN', 'setup-test')
    launch = Mock()
    monkeypatch.setattr(server, 'start_local_agent', launch)
    app = server.create_app(tmp_path, background=False)
    with TestClient(app, base_url='http://127.0.0.1:8787', client=('192.0.2.1', 50000)) as c:
        assert c.post('/api/auth/setup', json={'password': 'test', 'setup_token': 'setup-test'}).status_code == 200
        assert c.post('/api/desktop/connect').status_code == 409
        launch.assert_not_called()


def test_local_launcher_repairs_unreadable_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv('RC_DESKTOP_DIR', str(tmp_path))
    monkeypatch.delenv('RC_LOCAL_URL', raising=False)
    monkeypatch.setattr(desktop.sys, 'argv', ['run_desktop.py', '--local'])
    monkeypatch.setattr(desktop, 'load_config', Mock(side_effect=RuntimeError('Cannot decrypt')))
    config = {'url': 'http://127.0.0.1:8787', 'id': 'repaired', 'token': 'test'}
    repair = Mock(return_value=config)
    monkeypatch.setattr(local_desktop, 'pair_local', repair)
    agent = Mock()
    monkeypatch.setattr(desktop, 'DesktopAgent', agent)
    desktop.main()
    repair.assert_called_once()
    agent.assert_called_once_with(config, tmp_path)
    agent.return_value.run.assert_called_once()


def test_remote_launcher_does_not_replace_unreadable_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv('RC_DESKTOP_DIR', str(tmp_path))
    monkeypatch.setattr(desktop.sys, 'argv', ['run_desktop.py'])
    monkeypatch.setattr(desktop, 'load_config', Mock(side_effect=RuntimeError('Cannot decrypt')))
    repair = Mock()
    monkeypatch.setattr(local_desktop, 'pair_local', repair)
    with pytest.raises(RuntimeError):
        desktop.main()
    repair.assert_not_called()
