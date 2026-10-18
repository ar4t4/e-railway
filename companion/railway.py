"""Read-only availability adapter. No reserve/confirm endpoint runs on the server."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from urllib.parse import urlencode
import httpx

SEARCH_URL = "https://railspaapi.shohoz.com/v1.0/web/bookings/search-trips-v2"
SITE = "https://eticket.railway.gov.bd"


class RailwayError(Exception):
    def __init__(self, message, kind="error", retry_after=30):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def booking_url(origin, destination, day, seat_class):
    return SITE + "/booking/train/search?" + urlencode({"fromcity": origin, "tocity": destination,
        "doj": date.fromisoformat(day).strftime("%d-%b-%Y"), "class": seat_class})


def departure_clock(value):
    match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", str(value), re.I)
    if not match:
        return ""
    hour, minute = int(match[1]), int(match[2])
    if match[3]:
        hour = hour % 12 + (12 if match[3].lower() == "pm" else 0)
    return f"{hour:02}:{minute:02}" if hour < 24 and minute < 60 else ""


def parse_results(payload, query):
    origin, destination, day, seat_class = query
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("trains"), list):
        raise RailwayError("Railway response changed; availability is unknown.", "schema")
    result = []
    for train in data["trains"]:
        if not isinstance(train, dict) or not isinstance(train.get("seat_types"), list):
            raise RailwayError("Railway train details could not be read.", "schema")
        for st in train["seat_types"]:
            if st.get("type") != seat_class:
                continue
            counts = st.get("seat_counts", {})
            if "online" not in counts:
                raise RailwayError("Railway seat counts are missing.", "schema")
            try:
                seats = int(counts["online"])
            except (ValueError, TypeError):
                raise RailwayError("Railway seat count is invalid.", "schema") from None
            name = str(train.get("trip_number") or train.get("train_name") or "")
            if not name:
                raise RailwayError("Railway train identity is missing.", "schema")
            trip_id = str(train.get("trip_id", ""))
            raw_id = "|".join((*query, name, trip_id))
            result.append({"id": hashlib.sha256(raw_id.encode()).hexdigest()[:24],
                "origin": origin, "destination": destination, "date": day, "class": seat_class,
                "train": name, "trip_id": trip_id, "seats": max(0, seats),
                "fare": str(st.get("fare", "—")), "departure": str(train.get("departure_date_time", "")),
                "departure_time": departure_clock(train.get("departure_date_time", "")),
                "url": booking_url(*query), "query": list(query)})
    return result


async def search(query, credentials):
    if not credentials or not credentials.get("token") or not credentials.get("uudid"):
        raise RailwayError("Connect your railway session in Settings.", "session", 60)
    origin, destination, day, seat_class = query
    headers = {"Authorization": "Bearer " + credentials["token"], "X-Device-Id": credentials["uudid"],
        "Accept": "application/json", "Origin": SITE, "Referer": SITE + "/",
        "User-Agent": credentials.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    if credentials.get("ssdk"):
        headers["X-Device-Key"] = credentials["ssdk"]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(SEARCH_URL, headers=headers, params={"from_city": origin,
                "to_city": destination, "date_of_journey": date.fromisoformat(day).strftime("%d-%b-%Y"),
                "seat_class": seat_class})
    except httpx.HTTPError:
        raise RailwayError("Railway connection failed; retrying with backoff.") from None
    if response.status_code in (401, 403):
        raise RailwayError("Railway session needs attention. Reconnect in Settings.", "session", 60)
    if response.status_code == 429:
        try:
            delay = min(3600, max(30, int(response.headers.get("Retry-After", 60))))
        except ValueError:
            delay = 60
        raise RailwayError("Railway requested a cooldown.", "rate_limit", delay)
    if response.status_code != 200:
        raise RailwayError(f"Railway returned HTTP {response.status_code}; no fresh availability.")
    try:
        payload = response.json()
    except ValueError:
        raise RailwayError("Railway returned an unreadable response.", "schema") from None
    return parse_results(payload, query), payload.get("data", {}).get("device_key")
