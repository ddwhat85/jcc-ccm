"""데이터 자동 백업 — 하루 한 번 데이터베이스 사본을 남기고, 운영자 화면에서 내려받는다.

Render 디스크 스냅숏과 별도로, 운영자가 사본을 회사 PC·USB로 바로 받아 둘 수 있게 한다
(호스팅 쪽 문제와 상관없이 데이터가 남는다). SQLite 온라인 백업이라 서버를 멈추지 않는다.

  JCC_BACKUP_DIR   백업 폴더(기본: 데이터베이스 옆 backups/)
  JCC_BACKUP_KEEP  보관 일수(기본 7) — 더 오래된 것은 지운다
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NAME_RE = re.compile(r"^jcc-\d{8}(-\d{6})?\.db$")


def backup_dir(storage) -> str:
    d = os.environ.get("JCC_BACKUP_DIR") or os.path.join(os.path.dirname(os.path.abspath(storage.path)), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def _keep() -> int:
    try:
        return max(1, int(os.environ.get("JCC_BACKUP_KEEP") or 7))
    except ValueError:
        return 7


def make_backup(storage, now: float | None = None, manual: bool = False) -> dict:
    """지금 사본 하나. 자동은 날짜당 한 개(jcc-YYYYMMDD.db), 수동은 시각까지(jcc-YYYYMMDD-HHMMSS.db)."""
    now = time.time() if now is None else now
    t = datetime.fromtimestamp(now, KST)
    name = t.strftime("jcc-%Y%m%d-%H%M%S.db") if manual else t.strftime("jcc-%Y%m%d.db")
    d = backup_dir(storage)
    tmp, final = os.path.join(d, name + ".part"), os.path.join(d, name)
    dst = sqlite3.connect(tmp)
    try:
        with storage._lock:
            storage._conn.backup(dst)
    finally:
        dst.close()
    os.replace(tmp, final)            # 다 쓴 뒤에 이름을 바꿔 반쯤 쓴 사본이 목록에 보이지 않게
    size = os.path.getsize(final)
    prune(storage, keep_name=name)    # 방금 만든 사본은 날짜가 어떻든 지우지 않는다(시계가 틀렸던 사본이 있어도)
    return {"name": name, "size": size, "ts": now}


def prune(storage, keep_name: str = "") -> int:
    """보관 일수를 넘긴 사본을 지운다(날짜가 오래된 것부터). keep_name은 지우지 않는다. 지운 개수."""
    files = list_backups(storage)
    days = sorted({f["name"][4:12] for f in files}, reverse=True)
    keep_days = set(days[:_keep()])
    n = 0
    for f in files:
        if f["name"][4:12] not in keep_days and f["name"] != keep_name:
            try:
                os.remove(os.path.join(backup_dir(storage), f["name"]))
                n += 1
            except OSError:
                pass
    return n


def daily(storage, now: float | None = None):
    """오늘(한국 날짜) 자동 사본이 없으면 만든다. 만들었으면 그 정보, 이미 있으면 None."""
    now = time.time() if now is None else now
    name = datetime.fromtimestamp(now, KST).strftime("jcc-%Y%m%d.db")
    if os.path.exists(os.path.join(backup_dir(storage), name)):
        return None
    return make_backup(storage, now)


def list_backups(storage) -> list:
    d = backup_dir(storage)
    out = []
    for nm in os.listdir(d):
        if NAME_RE.match(nm):
            p = os.path.join(d, nm)
            out.append({"name": nm, "size": os.path.getsize(p), "ts": os.path.getmtime(p), "manual": "-" in nm[4:]})
    return sorted(out, key=lambda f: f["name"], reverse=True)


def path_for(storage, name: str):
    """내려받을 사본의 경로 — 이름 형식이 맞고 실제로 있을 때만(경로 탈출 차단)."""
    if not NAME_RE.match(name or ""):
        return None
    p = os.path.join(backup_dir(storage), name)
    return p if os.path.isfile(p) else None
