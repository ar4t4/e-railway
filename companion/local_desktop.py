"""Pair a same-user desktop with the service backed by this local data directory."""
import hashlib
import json
import secrets
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import httpx
from .store import Store


def pair_local(root, directory=None, url="http://127.0.0.1:8787"):
    from .desktop import windows_protect
    directory = Path(directory or Path(__file__).resolve().parent.parent / 'data')
    if not (directory / 'companion.sqlite').exists():
        raise ValueError('Launch Companion and create your dashboard password first.')
    store = Store(directory)
    session = secrets.token_urlsafe(40)
    hashed = hashlib.sha256(session.encode()).hexdigest()
    try:
        if not store.secret('owner'):
            raise ValueError('Create your dashboard password, then launch the desktop agent again.')
        # Check Windows protection before rotating a previously usable key.
        windows_protect(b'pairing check')
        store.session(hashed, time.time() + 60)
        with httpx.Client(base_url=url, cookies={'rc_session': session}, timeout=15, trust_env=False) as client:
            # Prove that the running service uses the same local store before replacing a key.
            status = client.get('/api/auth/status')
            if status.status_code != 200 or not status.json().get('authenticated'):
                raise ValueError('This service uses another data directory. Pair it manually from Settings.')
            response = client.post('/api/agent/pair')
            if response.status_code != 200:
                raise ValueError('Pairing was rejected. Resolve any outstanding booking in the dashboard first.')
            config = {'url': url, 'token': response.json()['token'], 'id': secrets.token_hex(8)}
            root.mkdir(parents=True, exist_ok=True)
            protected = windows_protect(json.dumps(config).encode())
            pending = root / 'agent.dpapi.tmp'
            pending.write_bytes(protected)
            pending.replace(root / 'agent.dpapi')
            heartbeat = client.post('/api/agent/heartbeat', headers={'Authorization': 'Bearer ' + config['token']},
                json={'id': config['id'], 'ready': False, 'message': 'Paired locally. Starting the desktop agent.'})
            if heartbeat.status_code != 200:
                raise ValueError('The service did not accept the new desktop connection.')
        return config
    finally:
        with store.lock, store.db:
            store.db.execute('DELETE FROM sessions WHERE hash=?', (hashed,))
        store.close()


_launch_lock = threading.Lock()


def open_regular_browser(directory, page='login'):
    """Open the user's usual browser profile without WebDriver or debug flags."""
    preference = Path(directory) / 'desktop' / 'browser.json'
    browser = json.loads(preference.read_text(encoding='utf-8-sig')) if preference.exists() else {}
    binary = browser.get('binary')
    if not binary or not Path(binary).is_file():
        raise RuntimeError('Choose an installed browser with Start-Desktop.ps1 -Browser Brave or -Browser Chrome.')
    url = ('brave://extensions/' if browser.get('name') == 'Brave' else 'chrome://extensions/') if page == 'extensions' else 'https://eticket.railway.gov.bd/login'
    subprocess.Popen([binary, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def start_local_agent(store, url):
    """Start a same-user agent; serialize repeated dashboard clicks."""
    with _launch_lock:
        if time.time() - store.read()['agent'].get('seen', 0) < 20:
            return
        project = Path(__file__).resolve().parent.parent
        root = store.directory / 'desktop'
        root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, RC_DATA_DIR=str(store.directory.resolve()),
                   RC_DESKTOP_DIR=str(root.resolve()), RC_LOCAL_URL=url)
        with (root / 'agent.log').open('ab') as output, (root / 'agent-error.log').open('ab') as errors:
            process = subprocess.Popen([sys.executable, str(project / 'run_desktop.py'), '--local'],
                cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(60):
            if process.poll() not in (None, 0):
                raise RuntimeError('Desktop could not start. Open data/desktop/agent-error.log for details.')
            if time.time() - store.read()['agent'].get('seen', 0) < 20:
                return
            time.sleep(.25)
        raise RuntimeError('Desktop is taking longer to connect. Wait a moment, then click Connect Railway again.')
