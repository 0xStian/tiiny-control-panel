"""A backup of the panel's own data, as one ZIP file (System → Your panel's data).

The device's backup only covers what's on the Tiiny. This one covers what the panel keeps on this computer, in
~/.local/share/tiiny-ui/:
  * chats;
  * projects, with their files and search indexes;
  * meetings, the Gallery, code reviews, routines and Studio jobs;
  * the performance history, if you ask for it.
Two things come along from the config folder: the panel's settings, without their secrets, and the apps on Integrations
(apps.json: what each may use, its counters and the fingerprint of its key). A restored app's key works again, because a
key is checked against its fingerprint; the key itself isn't in the backup, and isn't kept anywhere by the panel.

It never contains sign-in secrets (the browser key, paired phones), a key of any kind (the device's, Brave Search's,
an app's) or the panel's HTTPS certificate.

Restoring either adds what's missing (the default; when both have the same item, the newer one wins) or replaces
everything. Anything a restore would overwrite is first moved to .before-restore-<time>/ in the data folder, so it
can be undone. Performance history is only ever added: days the panel already has stay as they are. Apps are restored
together with Integrations' own settings (APP_SETTINGS), so a move to another computer brings Integrations as it was.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path

from . import VERSION
from .common import BadRequest, valid_id
from .store import COLLECTIONS

KIND = "tiiny-control-panel/panel-data"
FORMAT = 1
SECRET_SETTINGS = ("api_key", "search_brave_key")   # never written to a backup
STARTUP_SETTINGS = ("tiiny_bin", "cli_config")       # only set when starting the panel
APP_SETTINGS = ("gateway_enabled", "gateway_port", "gateway_lan", "gateway_keyless_ollama")   # restored with the apps
APPS_FILE = "apps.json"                              # the apps on Integrations, at the ZIP's root
MAX_APPS_FILE = 4 * 1024 ** 2
ROOT_FILES = ("manifest.json", "settings.json", APPS_FILE)
STORED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".mp4", ".m4a", ".ogg", ".opus", ".webm", ".glb", ".zip",
          ".gz", ".flac", ".mov"}                    # already compressed: stored as they are
METRICS_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl")
MAX_UNPACKED = 64 * 1024 ** 3
NAMES = {"chats": "chats", "projects": "projects", "gallery": "Gallery items", "meetings": "meetings",
         "reviews": "code reviews", "routines": "routines", "studio": "Studio jobs"}


def _files(d: Path):
    """Every regular file under `d`, without the half-written temporary files of atomic_write()."""
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if p.name.startswith(".") or p.is_symlink() or not p.is_file():
            continue
        out.append(p)
    return out


def summary(store) -> dict:
    """What's stored, per kind: how many items and how much space."""
    out, total = {}, 0
    for coll in sorted(COLLECTIONS):
        d = store.root / coll
        files = _files(d)
        size = sum(p.stat().st_size for p in files)
        out[coll] = {"records": len([p for p in files if p.parent == d and p.suffix == ".json"]), "bytes": size}
        total += size
    metrics = _files(store.root / "metrics")
    m = sum(p.stat().st_size for p in metrics)
    return {"collections": out, "metrics_bytes": m, "bytes": total + m, "folder": str(store.root)}


def clean_settings(settings) -> dict:
    return {k: settings.get(k) for k in settings.FIELDS if k not in SECRET_SETTINGS + STARTUP_SETTINGS}


def export(store, settings, out_path, *, metrics=False, apps=None, job=None) -> dict:
    """Write the backup ZIP to `out_path`. `apps` is the gateway's list of apps (gateway.Apps). Returns its manifest."""
    root = store.root
    entries = []
    counts = {}
    for coll in sorted(COLLECTIONS):
        d = root / coll
        files = _files(d)
        counts[coll] = {"records": len([p for p in files if p.parent == d and p.suffix == ".json"]),
                        "bytes": sum(p.stat().st_size for p in files)}
        entries += [(coll, p) for p in files]
    if metrics:
        entries += [("metrics", p) for p in _files(root / "metrics") if METRICS_FILE.fullmatch(p.name)]
    total = sum(p.stat().st_size for _, p in entries) or 1
    app_list = apps.export_all() if apps is not None else None
    manifest = {"kind": KIND, "format": FORMAT, "version": VERSION, "created": time.time(), "collections": counts,
                "metrics": bool(metrics), "settings": True}
    if app_list is not None:
        manifest["apps"] = len(app_list)
    done, last = 0, 0.0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("settings.json", json.dumps(clean_settings(settings), indent=2))
        if app_list is not None:
            z.writestr(APPS_FILE, json.dumps({"apps": app_list}, indent=1))
        for coll, p in entries:
            if job:
                job.check()
            arc = "data/" + p.relative_to(root).as_posix()
            ctype = zipfile.ZIP_STORED if p.suffix.lower() in STORED else zipfile.ZIP_DEFLATED
            try:
                if coll in COLLECTIONS and p.parent == root / coll and p.suffix == ".json":
                    with store.locks[coll]:   # a record is never caught half-written
                        data = p.read_bytes()
                    z.writestr(zipfile.ZipInfo.from_file(p, arc), data, compress_type=ctype)
                else:
                    z.write(p, arc, compress_type=ctype)
            except FileNotFoundError:
                continue   # deleted while the backup ran
            done += p.stat().st_size if p.exists() else 0
            if job and time.time() - last > 0.4:
                last = time.time()
                job.update(message=f"Packing… {done / 1048576:.0f} of {total / 1048576:.0f} MB", progress=done / total)
    return manifest


# -- reading a backup ----------------------------------------------------------------------------------------------
def _open(path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise BadRequest("That isn't a ZIP file.")


def _manifest(z: zipfile.ZipFile) -> dict:
    try:
        man = json.loads(z.read("manifest.json"))
    except (KeyError, ValueError):
        raise BadRequest("That ZIP isn't a backup of the panel's data. (A backup of the device itself is restored under "
                         "Device backup.)")
    if not isinstance(man, dict) or man.get("kind") != KIND:
        raise BadRequest("That ZIP isn't a backup of the panel's data.")
    if not isinstance(man.get("format"), int) or man["format"] > FORMAT:
        raise BadRequest("That backup was made by a newer version of the panel. Update the panel first, then restore it.")
    return man


def _safe_parts(parts) -> bool:
    return all(p not in ("", ".", "..") and "\\" not in p and ":" not in p for p in parts)


def _plan(z: zipfile.ZipFile) -> dict:
    """Sort the backup's files into records, their files and the history; refuse unsafe names outright."""
    records, metrics, skipped, size = {}, [], 0, 0
    for i in z.infolist():
        name = i.filename
        if name.endswith("/") or name in ROOT_FILES:
            continue
        parts = name.split("/")
        if name.startswith("/") or not _safe_parts(parts) or stat.S_ISLNK((i.external_attr >> 16) & 0o177777):
            raise BadRequest(f"The backup has a file with an unsafe name ({name[:120]}), so nothing was restored.")
        size += i.file_size
        if size > MAX_UNPACKED:
            raise BadRequest("The backup unpacks to more than the panel accepts, so nothing was restored.")
        if parts[0] != "data" or len(parts) < 3:
            skipped += 1
            continue
        coll = parts[1]
        if coll == "metrics":
            if len(parts) == 3 and METRICS_FILE.fullmatch(parts[2]):
                metrics.append(i)
            else:
                skipped += 1
            continue
        if coll not in COLLECTIONS:   # made by a newer panel that keeps more kinds of things
            skipped += 1
            continue
        if len(parts) == 3 and parts[2].endswith(".json") and valid_id(parts[2][:-5]):
            records.setdefault(coll, {}).setdefault(parts[2][:-5], {"json": None, "files": []})["json"] = i
        elif len(parts) >= 4 and valid_id(parts[2]):
            records.setdefault(coll, {}).setdefault(parts[2], {"json": None, "files": []})["files"].append(i)
        else:
            skipped += 1
    for coll in list(records):   # a record's files without the record itself aren't restored
        for rid in [r for r, rec in records[coll].items() if rec["json"] is None]:
            skipped += len(records[coll].pop(rid)["files"])
    return {"records": records, "metrics": metrics, "skipped": skipped, "size": size}


def _apps_in(z: zipfile.ZipFile):
    """The backup's list of apps; None when it has none (a backup made before the panel put them in)."""
    try:
        info = z.getinfo(APPS_FILE)
    except KeyError:
        return None
    if info.file_size > MAX_APPS_FILE:
        raise BadRequest("The backup's list of apps is larger than the panel accepts, so nothing was restored.")
    try:
        data = json.loads(z.read(info))
    except ValueError:
        return None
    found = data.get("apps") if isinstance(data, dict) else None
    return found if isinstance(found, list) else None


def inspect(store, path, apps=None) -> dict:
    """What a backup holds and how much of it is here already. `apps` is the gateway's list of apps (gateway.Apps)."""
    with _open(path) as z:
        man = _manifest(z)
        plan = _plan(z)
        cols = {}
        for coll in sorted(COLLECTIONS):
            recs = plan["records"].get(coll, {})
            here = sum(1 for rid in recs if (store.root / coll / f"{rid}.json").exists())
            cols[coll] = {"records": len(recs), "here": here, "name": NAMES.get(coll, coll)}
        found = _apps_in(z)
        listed = None
        if found is not None:
            names = [str(a.get("name") or "")[:60] for a in found if isinstance(a, dict) and a.get("name")]
            listed = {"count": len(found), "here": apps.known(found) if apps is not None else 0, "names": names[:12]}
        return {"created": man.get("created"), "version": man.get("version"), "collections": cols,
                "metrics": len(plan["metrics"]), "settings": "settings.json" in z.namelist(), "apps": listed,
                "skipped": plan["skipped"], "bytes": plan["size"]}


# -- restoring -----------------------------------------------------------------------------------------------------
def _updated(raw: bytes) -> float:
    try:
        v = json.loads(raw).get("updated")
        return float(v) if isinstance(v, (int, float)) else 0.0
    except (ValueError, AttributeError):
        return 0.0


def _write(z: zipfile.ZipFile, info, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.restore")
    with z.open(info) as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 16)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def restore(store, settings, path, *, mode="merge", with_settings=False, apps=None, with_apps=False, job=None) -> dict:
    """Restore a backup: "merge" adds what's missing (the newer copy wins), "replace" swaps all the data for it.
    What it overwrites is moved to .before-restore-<time>/ first. `with_apps` also restores the apps on Integrations
    into `apps` (gateway.Apps), together with Integrations' own settings."""
    if mode not in ("merge", "replace"):
        raise BadRequest("Choose to add what's missing or to replace everything.")
    root = store.root
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    aside = root / f".before-restore-{stamp}"
    while aside.exists():   # a second restore within the same second gets a folder of its own
        n += 1
        aside = root / f".before-restore-{stamp}-{n}"
    added = replaced = kept = 0
    with _open(path) as z:
        _manifest(z)
        plan = _plan(z)
        found = _apps_in(z) if with_apps and apps is not None else None   # read first: it can refuse the whole backup
        free = shutil.disk_usage(root).free
        if plan["size"] * 1.05 + 200 * 1024 ** 2 > free:
            raise BadRequest(f"There isn't enough free disk space to restore this backup "
                             f"({plan['size'] / 1024 ** 3:.1f} GB needed).")

        def set_aside(rel: Path):
            src = root / rel
            if src.exists():
                dst = aside / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)

        if mode == "replace":   # the performance history isn't moved: the panel keeps writing today's file
            for coll in sorted(COLLECTIONS):
                with store.locks[coll]:
                    set_aside(Path(coll))
        todo = sum(len(r) for r in plan["records"].values()) or 1
        n, last = 0, 0.0
        for coll in sorted(plan["records"]):
            for rid, rec in sorted(plan["records"][coll].items()):
                if job:
                    job.check()
                with store.locks[coll]:
                    local = root / coll / f"{rid}.json"
                    raw = z.read(rec["json"])
                    if local.exists():
                        try:
                            mine = local.read_bytes()
                        except OSError:
                            mine = b""
                        if _updated(raw) <= _updated(mine):
                            kept += 1
                            continue
                        set_aside(Path(coll) / f"{rid}.json")
                        set_aside(Path(coll) / rid)
                        replaced += 1
                    else:
                        added += 1
                    for info in rec["files"]:
                        _write(z, info, root.joinpath(*info.filename.split("/")[1:]))
                    _write(z, rec["json"], local)
                n += 1
                if job and time.time() - last > 0.4:
                    last = time.time()
                    job.update(message=f"Restoring… {n} of {todo}", progress=n / todo)
        history = 0
        for info in plan["metrics"]:   # days that aren't here yet; a day the panel already has stays as it is
            target = root / "metrics" / info.filename.split("/")[-1]
            if not target.exists():
                _write(z, info, target)
                history += 1
        saved = None   # the backup's settings, without the ones a restore never sets
        if "settings.json" in z.namelist():
            try:
                data = json.loads(z.read("settings.json"))
            except ValueError:
                data = None
            if isinstance(data, dict):
                saved = {k: v for k, v in data.items() if k in settings.FIELDS and k not in SECRET_SETTINGS + STARTUP_SETTINGS}
        apps_done = None
        if found is not None:
            if mode == "replace" and apps.path.exists():   # like the data: what's replaced can be put back by hand
                aside.mkdir(parents=True, exist_ok=True)
                shutil.copy2(apps.path, aside / APPS_FILE)
                os.chmod(aside / APPS_FILE, 0o600)
            apps_done = apps.restore(found, mode)
        settings_done = bool(with_settings and saved is not None)
        changes = dict(saved) if settings_done else {}
        if apps_done is not None and saved and not settings_done:   # Integrations as it was: its switches go with its apps
            changes = {k: v for k, v in saved.items() if k in APP_SETTINGS}
        if changes:
            settings.update(changes)
    return {"added": added, "replaced": replaced, "kept": kept, "history_days": history, "settings": settings_done,
            "apps": apps_done, "gateway": any(k in changes for k in APP_SETTINGS),
            "skipped": plan["skipped"], "aside": str(aside) if aside.exists() else None}
