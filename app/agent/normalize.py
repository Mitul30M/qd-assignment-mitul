"""Pure normalization of rider-supplied trip ids and dates — no LLM required here.

Trip ids in the data look like T926334 (T + 6 digits). Riders write t88213, T-88213,
order 88213, kya T88213 tha. Dates come as kal/parso/aaj (Hinglish relatives),
"20 tarikh", "19th", "Sep 19", or explicit ISO dates — always resolved against
received_at in IST calendar days.
"""

from __future__ import annotations

import datetime
import re

TRIP_ID_RE = re.compile(r"\b(?:t|order|trip)[- _]?(?:id)?[- _]?(\d{4,8})\b", re.IGNORECASE)
OTHER_RIDER_RE = re.compile(r"\b(r|rk)[-_ ]?(\d{1,4})\b", re.IGNORECASE)
DAYNUM_RE = re.compile(r"\b(\d{1,2})\s*(?:tarikh|tareekh|ta)?(?:th|st|nd|rd)?(?:\s*(?:sept|sep|sig|sptember)?)\b", re.IGNORECASE)
MONTHDAY_RE = re.compile(r"\b(sept?:?|sep|september|sitambar)\s*(\d{1,2})", re.IGNORECASE)
DAYNUM_WORDS = re.compile(r"\b(\d{1,2})\s*(?:wale?|wala|taraikh|tarikh)\b", re.IGNORECASE)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

MINUTES_PER_DAY = 24 * 60


def ist_day_of(received_at: str) -> datetime.date:
    """IST calendar day of an ISO timestamp (the reference day for relative dates)."""
    d = datetime.datetime.fromisoformat(received_at)
    if d.tzinfo is None:
        d = d.replace(tzinfo=IST)
    return d.astimezone(IST).date()


def normalize_trip_ids(text: str) -> list[str]:
    """t88213 / T-88213 / order 88213 / trip 88213 -> T88213 (deduped, first-seen order)."""
    out: list[str] = []
    for m in TRIP_ID_RE.finditer(text):
        tid = f"T{int(m.group(1))}"
        if tid not in out:
            out.append(tid)
    return out


def mentioned_other_rider(text: str) -> str | None:
    """R005 / r-7 style spellings that name a rider other than the webhook rider."""
    m = OTHER_RIDER_RE.search(text)
    if not m:
        return None
    digit, num = m.group(1).lower(), m.group(2)
    if digit == "r":
        return f"R{int(num):03d}"
    return None


def normalize_dates(text: str, received_at_ist: datetime.date) -> list[datetime.date]:
    """kal (yesterday), parso (day after tomorrow), aaj (today), dd tarikh, Sep dd,
    dd wala — resolved as IST calendar days clamped near received_at."""
    t = text.lower()
    dts: list[datetime.date] = []
    if re.search(r"\baaj\b", t):
        dts.append(received_at_ist)
    if re.search(r"\bkal\b", t):
        dts.append(received_at_ist - datetime.timedelta(days=1))
    if re.search(r"\bparso\b", t):
        dts.append(received_at_ist + datetime.timedelta(days=2))
    if re.search(r"\b(\d{1,2})\s*din pehle\b", t):
        n = int(re.search(r"\b(\d{1,2})\s*din pehle\b", t).group(1))
        dts.append(received_at_ist - datetime.timedelta(days=n))
    for m in MONTHDAY_RE.finditer(t):
        dts.append(existing_or_nearest(received_at_ist, int(m.group(2)), month=9))
    for m in DAYNUM_RE.finditer(t):
        day = int(m.group(1))
        if 1 <= day <= 31:
            dts.append(existing_or_nearest(received_at_ist, day))
    for m in DAYNUM_WORDS.finditer(t):
        day = int(m.group(1))
        if 1 <= day <= 31:
            dts.append(existing_or_nearest(received_at_ist, day))
    seen, out = set(), []
    for d in dts:
        if d not in seen:
            out.append(d)
            seen.add(d)
    return out


def existing_or_nearest(received: datetime.date, day: int, month: int | None = None) -> datetime.date:
    """Resolve dd against the received month/year: same month if the day exists there,
    else the previous month (claims are usually about past days)."""
    import calendar

    def try_date(year: int, month: int, day: int) -> datetime.date | None:
        try:
            return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))
        except ValueError:
            return None

    if month is not None:
        got = try_date(received.year, month, day) or try_date(received.year - 1, month, day)
        return got or received
    same = try_date(received.year, received.month, day)
    if same and same <= received:
        return same
    prev = try_date(received.year, received.month - 1, day) or try_date(received.year - 1, 12, day)
    if prev:
        return prev
    return same or received


def normalize_iso_date(s: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None
