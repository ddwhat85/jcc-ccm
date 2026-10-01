"""CSV 내보내기 — 고객이 감사·보험 자료로 자주 달라는 경보 목록·센서 이력.

엑셀에서 한글이 깨지지 않게 UTF-8 BOM을 붙이고, 시각은 한국 시간 문자열로 쓴다.
범위(고객 계정)는 부르는 쪽이 넘긴 기기·판넬 키로 거른다.
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MAX_ROWS = 200_000
_CAUSE = {"real": "실제 이상", "false": "오경보", "work": "시험·작업", "other": "그 밖"}


def _t(ts) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def _csv(header: list, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def alarms_csv(storage, days: float, keys=None) -> bytes:
    since = time.time() - days * 86400
    owner = {}
    for p in storage.list_panels():
        for c in p["ccms"]:
            owner[c["device_id"]] = p["panel_name"]
        owner[p["panel"]] = p["panel_name"]
    rows = storage.alarms_since(since, keys if keys is not None else set(owner), 500)
    return _csv(["경보번호", "판넬", "기기", "센서", "종류", "심각도", "내용", "발생", "확인", "확인자", "원인", "조치 메모", "해제"],
                ([a["id"], owner.get(a["device_id"], ""), a["device_id"], a.get("sensor_key") or "", a["kind"],
                  "위험" if a.get("severity") == "crit" else "주의", a.get("detail") or "", _t(a["raised_at"]),
                  _t(a.get("acked_at")), a.get("acked_by") or "", _CAUSE.get(a.get("cause") or "", ""),
                  a.get("ack_note") or "", _t(a.get("cleared_at"))] for a in rows))


def readings_csv(storage, device_id: str, sensor_key: str, days: float) -> bytes:
    since = time.time() - days * 86400
    with storage._lock:
        rows = storage._conn.execute(
            "SELECT ts, value, ok, name, unit FROM readings WHERE device_id=? AND sensor_key=? AND ts>=? "
            "ORDER BY ts LIMIT ?", (device_id, sensor_key, since, MAX_ROWS)).fetchall()
    return _csv(["시각", "기기", "센서", "이름", "값", "단위", "정상 수신"],
                ([_t(r["ts"]), device_id, sensor_key, r["name"] or "", "" if r["value"] is None else r["value"],
                  r["unit"] or "", "예" if r["ok"] else "아니오"] for r in rows))
