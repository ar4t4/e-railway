"""Build this PC's installable extension with a session-sync-only capability."""
import hashlib
import json
import secrets
import shutil
from pathlib import Path
from companion.store import Store

ROOT = Path(__file__).resolve().parent.parent

def prepare(directory=None):
    directory = Path(directory or ROOT / 'data')
    if not (directory / 'companion.sqlite').is_file():
        raise ValueError('Launch Companion and create your dashboard password first.')
    store = Store(directory)
    try:
        if not store.secret('owner'):
            raise ValueError('Create your dashboard password first.')
        key = store.secret('browser_bridge_key') or secrets.token_urlsafe(40)
        store.set_secret('browser_bridge_key', key)
        store.set_secret('browser_bridge_hash', hashlib.sha256(key.encode()).hexdigest())
        target = directory / 'browser-extension'
        target.mkdir(parents=True, exist_ok=True)
        for name in ['manifest.json','session.js','worker.js','popup.html','popup.js','popup.css']:
            shutil.copyfile(ROOT / 'browser-extension' / name, target / name)
        (target / 'config.json').write_text(json.dumps({'key':key}), encoding='utf-8')
        return target
    finally:
        store.close()

if __name__ == '__main__':
    print('Load unpacked in Brave using this folder:')
    print(prepare())
