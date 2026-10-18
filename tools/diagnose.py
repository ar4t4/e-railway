"""Print local connection status without credentials or passenger information."""
import json
import time
from pathlib import Path
import httpx
from companion.store import Store
from companion.models import queries


def main():
    directory = Path(__file__).resolve().parent.parent / 'data'
    if not (directory / 'companion.sqlite').exists():
        print('Launch Companion and complete first-time dashboard setup.')
        return
    with httpx.Client(timeout=4, trust_env=False) as client:
        try:
            running = client.get('http://127.0.0.1:8787/healthz').json().get('ok', False)
        except Exception:
            running = False
    store = Store(directory)
    try:
        state = store.read()
        now = time.time()
        report = {
            'service_running': running,
            'monitor_heartbeat_recent': now - state['health'].get('heartbeat', 0) < 15,
            'desktop_connected': now - state['agent'].get('seen', 0) < 20,
            'desktop_ready': state['agent'].get('ready', False),
            'desktop_message': state['agent'].get('message', ''),
            'railway_session_saved': bool(store.secret('railway')),
            'monitor_status': state['health']['status'],
            'monitor_message': state['health'].get('message', ''),
            'successful_searches': state['health'].get('checks', 0),
            'active_watchlists': sum(w['enabled'] for w in state['watches']),
            'planned_searches': len(queries(state['watches'])),
            'telegram_connected': bool(store.secret('telegram_token') and store.secret('telegram_chat')),
            'telegram_enabled': state['settings']['telegram_enabled'],
            'recent_deliveries': [n['status'] for n in state['outbox'][-5:]],
            'mode': state['mode'], 'paused': state['paused'],
        }
        preference = directory / 'desktop' / 'browser.json'
        report['preferred_browser'] = json.loads(preference.read_text(encoding='utf-8-sig')).get('name') if preference.exists() else 'Chrome'
        print(json.dumps(report, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
