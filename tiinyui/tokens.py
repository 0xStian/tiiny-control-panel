"""Token usage, as the Tiiny itself counts it (Dashboard → Token usage).

The device's model service keeps its own statistics, whoever asked: this panel, Tiiny's apps, the apps on Integrations
or a program that calls the device directly. Under /api/v1/statistics/ it answers (read on TiinyOS 0.1.39):
  * today_usage_all             {model: counters} for its own local day; the counters are prompt_tokens,
                                completion_tokens, total_tokens and request_count;
  * week_usage?start_date=      {date: {model: counters}} for the 7 days from that date (days without use are {});
  * app_week_usage?start_date=  {app: {total_tokens, models: {model: tokens}}} for the same 7 days, as one sum;
  * app_usage?app_id=           one app's counters for today;
  * app_total                   {app: {total_tokens, models}} over everything it ever counted.
It counts chat requests: /v1/chat/completions, /v1/responses, /v1/messages, /api/chat and /api/generate. Embeddings,
pictures, speech and /v1/completions aren't counted. An app is the name a request carries in its "app_id" field; requests
without one are counted as "default" (OpenAI-style routes), "anthropic" or "ollama". The panel names its own requests
and the ones it forwards for an Integrations app (device.with_name), so they show apart.

read() turns that into what the card shows for one range. Three things shape it:
  * Days are the device's own calendar days. Which one is today comes from the device's answers, not from this
    computer's clock: the two can be in different time zones (a container on UTC, a Tiiny at home).
  * Use per app only comes summed over 7 days, so the ranges are today, 7 days and 4 weeks (four such sums that end
    today). Every table of a range then covers exactly the same days.
  * A device whose clock was once ahead has use dated in the future. No range reaches past today, so such days only
    count in "all time".
"""

from __future__ import annotations

import datetime
import threading
import time
import urllib.parse

from . import activity, device, gateway
from .common import DeviceError, error_message

RANGES = {"today": 1, "7d": 7, "28d": 28, "all": 0}
UNNAMED = {"default": "OpenAI-style API", "anthropic": "Anthropic-style API", "ollama": "Ollama-style API"}
CACHE_FOR = 15         # seconds an answer is reused: several open pages don't each ask the device
MAX_ROWS = 400         # more models, apps or days than this in one answer isn't a Tiiny's statistics
MAX_TODAY_APPS = 40    # today's use is asked per app: the apps used most this week, at most this many

_lock = threading.Lock()
_cache: dict = {}


class Unsupported(Exception):
    """This device's software doesn't have the statistics (an older TiinyOS)."""


def _num(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if 0 <= n < 2 ** 62 else 0


def _counters(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {"read": _num(raw.get("prompt_tokens")), "written": _num(raw.get("completion_tokens")), "requests": _num(raw.get("request_count"))}


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("read", "written", "requests")}


def _get(info, path: str):
    """One statistics answer, a dict. Raises Unsupported when the device has no such route, DeviceError when it can't be
    reached or answers with an error."""
    status, data, text = device.call(info, "core", "GET", "/api/v1/statistics/" + path, timeout=20)
    if status == 404:
        raise Unsupported()
    if not 200 <= status < 300 or not isinstance(data, dict):
        raise DeviceError(error_message(data, text, status))
    return data


def _day(text):
    try:
        return datetime.date.fromisoformat(str(text))
    except ValueError:
        return None


def _days(info, start: datetime.date) -> dict:
    """{date: {model: counters}} for the 7 days from `start`."""
    out = {}
    for key, models in list(_get(info, "week_usage?start_date=" + start.isoformat()).items())[:MAX_ROWS]:
        day = _day(key)
        if day and isinstance(models, dict):
            out[day] = {str(m)[:300]: _counters(c) for m, c in list(models.items())[:MAX_ROWS]}
    return out


def _apps(data) -> dict:
    """{app: {model: tokens}} from an app_total or app_week_usage answer."""
    out = {}
    for app, v in list(data.items())[:MAX_ROWS]:
        models = v.get("models") if isinstance(v, dict) else None
        if isinstance(models, dict):
            out[str(app)[:120]] = {str(m)[:300]: _num(n) for m, n in list(models.items())[:MAX_ROWS]}
    return out


def _week_apps(info, start: datetime.date) -> dict:
    return _apps(_get(info, "app_week_usage?start_date=" + start.isoformat()))


def device_today(info, guess: datetime.date):
    """The device's own date, and what it counted on the days around it: (date, {date: {model: counters}}). Its
    "today" answer is matched against its days around this computer's date (`guess`); with nothing counted today, or no
    match, it's `guess`. (A day with use after `guess` proves nothing: see the module's note on clocks.)"""
    around = _days(info, guess - datetime.timedelta(days=1))   # yesterday, today and the five days after
    today = {str(m)[:300]: _counters(c) for m, c in list(_get(info, "today_usage_all").items())[:MAX_ROWS]}
    used = lambda models: {m: (c["read"], c["written"]) for m, c in models.items() if c["read"] or c["written"]}   # noqa: E731
    if used(today):
        for day in (guess, guess + datetime.timedelta(days=1), guess - datetime.timedelta(days=1)):
            if used(around.get(day) or {}) == used(today):
                return day, around
    return guess, around


def _label(app: str, names: dict, mine: set) -> dict:
    if app == activity.PANEL_APP:
        return {"name": app, "kind": "panel"}
    if app in UNNAMED:
        return {"name": "No name given", "kind": "unnamed", "api": UNNAMED[app]}
    if app in mine:
        return {"name": app, "kind": "integration"}
    return {"name": names.get(app) or app, "kind": "app"}


def read(info, rng: str, *, apps=(), names=None, today: datetime.date = None) -> dict:
    """What the card shows for one range ("today", "7d", "28d" or "all"). `apps` are the Integrations apps
    (gateway.Apps.list()), `names` maps the device's own app ids to names (activity.app_names). All time has totals
    only: the device keeps no split into read and written there, and no days."""
    if rng not in RANGES:
        rng = "7d"
    n = RANGES[rng]
    guess = today or datetime.date.today()
    mine = {gateway.device_name(a) for a in apps}
    names = names or {}
    out = {"supported": True, "range": rng, "split": bool(n), "days": [], "models": [], "apps": []}
    per_app = {}   # app -> {"tokens", "models": {model: tokens}} and, for today, its counters
    try:
        everything = _apps(_get(info, "app_total"))
        if n:
            day, days = device_today(info, guess)
            first = day - datetime.timedelta(days=n - 1)
            week = day - datetime.timedelta(days=6)
            # the days, in sums of seven that end today; the first of them covers a single day's range too
            for start in [day - datetime.timedelta(days=6 + 7 * i) for i in range((n + 6) // 7)]:
                if not all(start + datetime.timedelta(days=d) in days for d in range(7) if start + datetime.timedelta(days=d) >= first):
                    days.update(_days(info, start))
                if n >= 7:
                    for app, models in _week_apps(info, start).items():
                        mine_ = per_app.setdefault(app, {"tokens": 0, "models": {}})
                        for m, t in models.items():
                            mine_["models"][m] = mine_["models"].get(m, 0) + t
                            mine_["tokens"] += t
            if n == 1:   # today per app: asked for each app that was used this week
                recent = _week_apps(info, week)
                for app in sorted(recent, key=lambda a: -sum(recent[a].values()))[:MAX_TODAY_APPS]:
                    c = _counters(_get(info, "app_usage?app_id=" + urllib.parse.quote(app, safe="")))
                    if c["read"] or c["written"]:
                        per_app[app] = dict(c, tokens=c["read"] + c["written"], models={})
            totals = {}
            for i in range(n):
                d = first + datetime.timedelta(days=i)
                row = {"read": 0, "written": 0, "requests": 0}
                for m, c in (days.get(d) or {}).items():
                    row = _add(row, c)
                    totals[m] = _add(totals.get(m, {}), c)
                out["days"].append(dict(row, date=d.isoformat()))
            out["models"] = [dict(c, id=m, tokens=c["read"] + c["written"]) for m, c in totals.items() if c["read"] or c["written"] or c["requests"]]
            out["today"] = day.isoformat()
            out["total"] = {k: sum(d[k] for d in out["days"]) for k in ("read", "written", "requests")}
            out["total"]["tokens"] = out["total"]["read"] + out["total"]["written"]
        else:
            totals = {}
            for app, models in everything.items():
                per_app[app] = {"tokens": sum(models.values()), "models": models}
                for m, t in models.items():
                    totals[m] = totals.get(m, 0) + t
            out["models"] = [{"id": m, "tokens": t} for m, t in totals.items() if t]
            out["total"] = {"tokens": sum(totals.values())}
    except Unsupported:
        return {"supported": False, "range": rng}
    out["models"].sort(key=lambda m: (-m["tokens"], m["id"]))
    for app, v in per_app.items():
        if v["tokens"]:
            row = dict(_label(app, names, mine), id=app, tokens=v["tokens"],
                       models=sorted(({"id": m, "tokens": t} for m, t in v["models"].items() if t), key=lambda m: (-m["tokens"], m["id"])))
            row.update({k: v[k] for k in ("read", "written", "requests") if k in v})
            out["apps"].append(row)
    out["apps"].sort(key=lambda a: (-a["tokens"], a["name"], a["id"]))
    out["all_time"] = sum(sum(models.values()) for models in everything.values())
    return out


def cached(key, make):
    """make()'s answer, reused for a few seconds. `key` says which device and range it's for."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_FOR:
            return hit[1]
    value = make()
    with _lock:
        if len(_cache) > 32:
            _cache.clear()
        _cache[key] = (time.time(), value)
    return value
