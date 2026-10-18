from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator, model_validator

DHAKA = ZoneInfo("Asia/Dhaka")
CLASSES = {"S_CHAIR", "SHOVAN", "SNIGDHA", "AC_S", "AC_B", "F_SEAT", "F_BERTH", "SHULOV"}


def today() -> str:
    return datetime.now(DHAKA).date().isoformat()


class Watchlist(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    origins: list[str] = Field(min_length=1, max_length=10)
    destinations: list[str] = Field(min_length=1, max_length=10)
    dates: list[str] = Field(min_length=1, max_length=31)
    classes: list[str] = Field(min_length=1, max_length=8)
    passengers: int = Field(default=1, ge=1, le=4)
    trains: list[str] = Field(default_factory=list, max_length=20)
    action: Literal["watch", "seats", "payment"] = "watch"
    telegram: bool = True
    sound: bool = True
    enabled: bool = True
    priority: int = Field(default=1, ge=1, le=100)
    # Inclusive hour window in Bangladesh time. Empty means unrestricted.
    departure_after: str = ""
    departure_before: str = ""

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        if not value.strip():
            raise ValueError("Give the watchlist a name")
        return value.strip()

    @field_validator("origins", "destinations", "trains")
    @classmethod
    def strings(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(len(v) > 100 or any(ord(c) < 32 for c in v) for v in result):
            raise ValueError("Station or train name is invalid")
        return result

    @field_validator("classes")
    @classmethod
    def classes_valid(cls, values):
        values = list(dict.fromkeys(v.upper().strip() for v in values))
        if not set(values) <= CLASSES:
            raise ValueError("Unknown seat class")
        return values

    @field_validator("dates")
    @classmethod
    def dates_valid(cls, values):
        for value in values:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use YYYY-MM-DD dates")
        return list(dict.fromkeys(values))

    @field_validator("departure_after", "departure_before")
    @classmethod
    def time_valid(cls, value):
        if value:
            datetime.strptime(value, "%H:%M")
            if len(value) != 5:
                raise ValueError("Use HH:MM")
        return value

    @model_validator(mode="after")
    def combinations(self):
        if not self.origins or not self.destinations:
            raise ValueError("Choose a departure and destination")
        pairs = [(a, b) for a in self.origins for b in self.destinations if a.casefold() != b.casefold()]
        if not pairs:
            raise ValueError("Departure and destination must differ")
        if len(pairs) * len(self.dates) * len(self.classes) > 300:
            raise ValueError("Limit a watchlist to 300 route/date/class combinations")
        return self


class Preferences(BaseModel):
    telegram_enabled: bool = False
    sound: bool = True
    interval: int = Field(default=10, ge=5, le=300)
    alert_cooldown: int = Field(default=300, ge=60, le=3600)
    quiet_start: str = ""
    quiet_end: str = ""
    public_url: str = ""

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def quiet_time(cls, value):
        return Watchlist.time_valid(value)

    @field_validator("public_url")
    @classmethod
    def safe_url(cls, value):
        from urllib.parse import urlparse
        value = value.strip().rstrip("/")
        if value:
            p = urlparse(value)
            if p.scheme != "https" or not p.hostname or p.username or p.password:
                raise ValueError("Dashboard address must be an HTTPS URL")
        return value


def queries(watches):
    """Deduplicate overlapping watchlists, preserve priority and preference order."""
    result = {}
    for w in sorted(watches, key=lambda x: (x["priority"], x["id"])):
        if not w["enabled"]:
            continue
        for d in w["dates"]:
            if d < today():
                continue
            for origin in w["origins"]:
                for dest in w["destinations"]:
                    if origin.casefold() == dest.casefold():
                        continue
                    for seat_class in w["classes"]:
                        result.setdefault((origin, dest, d, seat_class), []).append(w)
    return result


def matches(watch, result):
    if result["origin"] not in watch["origins"] or result["destination"] not in watch["destinations"]:
        return False
    if result["date"] not in watch["dates"] or result["class"] not in watch["classes"]:
        return False
    if result["seats"] < watch["passengers"]:
        return False
    if watch["trains"] and not any(t.casefold() in result["train"].casefold() for t in watch["trains"]):
        return False
    start, end = watch.get("departure_after"), watch.get("departure_before")
    if start or end:
        departure = result.get("departure_time", "")
        if not departure:
            return False
        if start and end and start > end:
            if not (departure >= start or departure <= end):
                return False
        elif (start and departure < start) or (end and departure > end):
            return False
    return True


def in_quiet_hours(settings, now=None):
    start, end = settings.get("quiet_start"), settings.get("quiet_end")
    if not start or not end or start == end:
        return False
    current = (now or datetime.now(DHAKA)).strftime("%H:%M")
    return start <= current < end if start < end else current >= start or current < end
