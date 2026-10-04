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
MAX_ROWS = 200_000                 # 센서 이력
MAX_ALARMS = 100_000               # storage.alarms_since 상한과 같게
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
    rows = storage.alarms_since(since, keys if keys is not None else set(owner), MAX_ALARMS)
    return _csv(["경보번호", "판넬", "기기", "센서", "종류", "심각도", "내용", "발생", "확인", "확인자", "원인", "조치 메모", "해제"],
                ([a["id"], owner.get(a["device_id"], ""), a["device_id"], a.get("sensor_key") or "", a["kind"],
                  "위험" if a.get("severity") == "crit" else "주의", a.get("detail") or "", _t(a["raised_at"]),
                  _t(a.get("acked_at")), a.get("acked_by") or "", _CAUSE.get(a.get("cause") or "", ""),
                  a.get("ack_note") or "", _t(a.get("cleared_at"))] for a in rows))


def _names(storage) -> tuple:
    panel, sensor = {}, {}
    for p in storage.list_panels():
        panel[p["panel"]] = p["panel_name"]
        for c in p["ccms"]:
            panel[c["device_id"]] = p["panel_name"]
            for s in c.get("latest") or []:
                sensor[(c["device_id"], s["sensor_key"])] = s.get("name") or s["sensor_key"]
    return panel, sensor


def _scope(storage, keys):
    if keys is None:
        return "", []
    if not keys:
        return " AND 0", []
    frag, fa = storage._in_devices(set(keys))
    return " AND " + frag, list(fa)


def events_csv(storage, days: float, keys=None) -> bytes:
    """활동 기록 — 판넬이 스스로 한 조치·자동 복구·확인 등(장기 보관 고객사는 그 햇수만큼 남아 있다)."""
    since = time.time() - days * 86400
    panel, sensor = _names(storage)
    frag, fa = _scope(storage, keys)
    with storage._lock:
        rows = storage._conn.execute("SELECT ts, device_id, sensor_key, etype, detail, source FROM events WHERE ts >= ?" + frag +
                                     " AND etype != 'vent_hold' ORDER BY ts LIMIT ?", (since, *fa, MAX_ROWS)).fetchall()
    return _csv(["시각", "판넬", "기기", "센서", "종류", "내용", "누가"],
                ([_t(r["ts"]), panel.get(r["device_id"], ""), r["device_id"], sensor.get((r["device_id"], r["sensor_key"]), r["sensor_key"] or ""),
                  r["etype"], r["detail"] or "", {"system": "시스템", "user": "사람", "edge": "판넬(현장)"}.get(r["source"], r["source"] or "")]
                 for r in rows))


def hourly_csv(storage, days: float, keys=None) -> bytes:
    """시간별 센서 값(평균·최저·최고) — 몇 년치 추세·감사용. 원본(몇 초 간격)은 readings_csv로 최근 것만."""
    from .forecast import ensure
    ensure(storage)
    since = time.time() - days * 86400
    panel, sensor = _names(storage)
    frag, fa = _scope(storage, keys)
    with storage._lock:
        rows = storage._conn.execute("SELECT * FROM (SELECT hour, device_id, sensor_key, avg, vmin, vmax, n FROM hourly WHERE hour >= ?" + frag +
                                     " ORDER BY hour DESC LIMIT ?) ORDER BY hour, device_id, sensor_key", (since, *fa, MAX_ROWS)).fetchall()
    r3 = lambda v: "" if v is None else round(v, 3)  # noqa: E731
    return _csv(["시각(1시간)", "판넬", "기기", "센서", "평균", "최저", "최고", "측정 수"],
                ([_t(r["hour"]), panel.get(r["device_id"], ""), r["device_id"], sensor.get((r["device_id"], r["sensor_key"]), r["sensor_key"]),
                  r3(r["avg"]), r3(r["vmin"]), r3(r["vmax"]), r["n"]] for r in rows))


def readings_csv(storage, device_id: str, sensor_key: str, days: float) -> bytes:
    since = time.time() - days * 86400
    with storage._lock:
        rows = storage._conn.execute(
            # 상한을 넘으면 오래된 쪽을 버린다(최근 값이 감사·원인 조사에 더 중요) — 최신 N행을 뽑아 시간순으로
            "SELECT * FROM (SELECT ts, value, ok, name, unit FROM readings WHERE device_id=? AND sensor_key=? AND ts>=? "
            "ORDER BY ts DESC LIMIT ?) ORDER BY ts", (device_id, sensor_key, since, MAX_ROWS)).fetchall()
    return _csv(["시각", "기기", "센서", "이름", "값", "단위", "정상 수신"],
                ([_t(r["ts"]), device_id, sensor_key, r["name"] or "", "" if r["value"] is None else r["value"],
                  r["unit"] or "", "예" if r["ok"] else "아니오"] for r in rows))
