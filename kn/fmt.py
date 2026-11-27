"""One place for number/date formatting (RULES.md T2-T4)."""
from datetime import datetime


def format_duration(sec: int) -> str:
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{m} min" if m else f"{sec} s"


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def format_date(ts: float, short=False) -> str:
    d = datetime.fromtimestamp(ts)
    return d.strftime("%d.%m") if short else d.strftime("%d.%m.%Y")
