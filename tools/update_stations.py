"""Refresh the bundled station picker from the railway's public app configuration."""
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx

SOURCE = 'https://railspaapi.shohoz.com/v1.0/web/handshake'


def main():
    response = httpx.post(SOURCE, json={}, headers={
        'Origin': 'https://eticket.railway.gov.bd', 'Referer': 'https://eticket.railway.gov.bd/'}, timeout=25)
    response.raise_for_status()
    cities = response.json()['data']['cities']
    stations = []
    for city in cities:
        name = city['city_name']
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('Unexpected station name; existing catalogue retained')
        stations.append({'name': name, 'departure': bool(city['is_enable_for_from'])})
    stations.sort(key=lambda s: s['name'].casefold())
    if len(stations) < 30 or len({s['name'] for s in stations}) != len(stations):
        raise ValueError('Unexpected station catalogue; existing catalogue retained')
    target = Path(__file__).resolve().parent.parent / 'web' / 'stations.json'
    target.write_text(json.dumps({'source': SOURCE, 'updated': datetime.now(timezone.utc).isoformat(),
        'stations': stations}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Saved {len(stations)} railway stations.')


if __name__ == '__main__':
    main()
