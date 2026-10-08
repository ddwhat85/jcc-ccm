"""에어컨 원격 조작 — 틀만(실제로 장비에 쓰지 않는다).

하이퍼커널의 냉방기 원격 조작처럼 '바꾸면 대기 → [적용] → 장비로, [되돌리기]로 취소, 통신이 끊기면 잠금'의
안전한 절차를 미리 갖춰 둔다. 하지만 지금은 장비로 보내지 않는다 — 두 가지가 없어서:
  1) 쓰기 레지스터 번호: 기종마다 다르고 지어내지 않는다(제조사 Modbus 설명서로 레지스터 표에 등록해야 함)
  2) CCM 펌웨어의 Modbus 쓰기: 지금 펌웨어는 에어컨을 읽기만 한다
그래서 [적용]은 요청을 '보류'로 기록하고 이유를 남긴다(누가·언제·무엇을). 둘이 갖춰지면 send()만 채우면 된다.

조작 항목과 허용 범위(장비 설명서로 다시 확인할 것 — 넘으면 거절):
  run         운전 켜기/끄기(끄면 냉각이 멈춘다)
  setpoint    설정 온도 20~50 ℃, 0.5 ℃ 단위
  alarm_temp  판넬 고온 알람 온도 30~60 ℃, 1 ℃ 단위
"""
from __future__ import annotations

import time

FIELDS = {
    "run": {"word": "운전", "kind": "bool"},
    "setpoint": {"word": "설정 온도", "kind": "num", "min": 20.0, "max": 50.0, "step": 0.5, "unit": "℃"},
    "alarm_temp": {"word": "고온 알람 온도", "kind": "num", "min": 30.0, "max": 60.0, "step": 1.0, "unit": "℃"},
}
HOLD_REASON = "보류 — 쓰기 레지스터가 등록되지 않았고 CCM 펌웨어가 에어컨 쓰기를 아직 지원하지 않아 장비로 보내지 않았습니다"

_SCHEMA = """CREATE TABLE IF NOT EXISTS aircon_cmds (
    id INTEGER PRIMARY KEY AUTOINCREMENT, panel TEXT NOT NULL, unit INTEGER NOT NULL, field TEXT NOT NULL, value REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', note TEXT DEFAULT '', by TEXT DEFAULT '', created_at REAL, decided_at REAL);"""


def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


def check_value(field: str, value) -> float:
    """허용 범위 검사 → 저장할 값. 틀리면 ValueError(사람이 읽을 말)."""
    f = FIELDS.get(field)
    if f is None:
        raise ValueError("바꿀 수 없는 항목입니다")
    if f["kind"] == "bool":
        if value not in (0, 1, True, False, "0", "1", "on", "off"):
            raise ValueError("운전은 켜기/끄기만 됩니다")
        return 1.0 if value in (1, True, "1", "on") else 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{f['word']}는 숫자여야 합니다") from None
    if not (f["min"] <= v <= f["max"]):
        raise ValueError(f"{f['word']}는 {f['min']:g}~{f['max']:g}{f['unit']} 사이만 됩니다")
    if abs(v / f["step"] - round(v / f["step"])) > 1e-6:
        raise ValueError(f"{f['word']}는 {f['step']:g}{f['unit']} 단위로 바꿉니다")
    return v


def _unit(storage, panel: str, unit: int):
    from .aircon import panel_status
    return next((u for u in panel_status(storage, panel) if u["index"] == unit), None)


def stage(storage, panel: str, unit: int, field: str, value, by: str) -> dict:
    """변경 대기에 올린다(같은 항목의 대기는 새 값으로 바꾼다). 통신이 끊긴 에어컨은 잠금."""
    ensure(storage)
    u = _unit(storage, panel, unit)
    if u is None:
        raise ValueError("통신으로 연결된 에어컨이 아닙니다")
    if u["state"] != "live":
        raise ValueError("통신 끊김 · 조작 잠금 — 연결이 돌아오면 바꿀 수 있습니다")
    v = check_value(field, value)
    now = time.time()
    with storage._lock:
        storage._conn.execute("DELETE FROM aircon_cmds WHERE panel=? AND unit=? AND field=? AND status='pending'", (panel, unit, field))
        storage._conn.execute("INSERT INTO aircon_cmds (panel, unit, field, value, status, by, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                              (panel, unit, field, v, by, now))
        storage._conn.commit()
    return pending_view(storage, panel)


def revert(storage, panel: str, unit: int, by: str) -> int:
    ensure(storage)
    now = time.time()
    with storage._lock:
        n = storage._conn.execute("UPDATE aircon_cmds SET status='cancelled', note=?, decided_at=? WHERE panel=? AND unit=? AND status='pending'",
                                  (f"되돌림 ({by})", now, panel, unit)).rowcount
        storage._conn.commit()
    return n


def send(storage, lk: dict, field: str, value: float):
    """장비로 보내기 — 지금은 보내지 않는다(위 설명). (보냈는가, 이유)."""
    return False, HOLD_REASON


def apply(storage, panel: str, unit: int, by: str) -> dict:
    """대기 중인 변경을 적용. 통신이 끊겼으면 잠금. 지금은 모두 '보류'로 기록된다."""
    ensure(storage)
    u = _unit(storage, panel, unit)
    if u is None:
        raise ValueError("통신으로 연결된 에어컨이 아닙니다")
    if u["state"] != "live":
        raise ValueError("통신 끊김 · 조작 잠금 — 연결이 돌아오면 적용할 수 있습니다")
    with storage._lock:
        rows = [dict(r) for r in storage._conn.execute("SELECT * FROM aircon_cmds WHERE panel=? AND unit=? AND status='pending' ORDER BY id",
                                                       (panel, unit))]
    if not rows:
        raise ValueError("적용할 변경이 없습니다")
    from . import equipment as eqm
    lk = ((eqm.get(storage, panel) or {}).get("equipment") or [{}] * (unit + 1))[unit].get("link") or {}
    now, out = time.time(), []
    for r in rows:
        sent, why = send(storage, lk, r["field"], r["value"])
        st = "sent" if sent else "held"
        with storage._lock:
            storage._conn.execute("UPDATE aircon_cmds SET status=?, note=?, decided_at=? WHERE id=?", (st, why or "", now, r["id"]))
            storage._conn.commit()
        f = FIELDS[r["field"]]
        what = (("켜기" if r["value"] >= 0.5 else "끄기") if f["kind"] == "bool" else f"{r['value']:g}{f['unit']}")
        storage.log_event(lk.get("device_id", ""), "", "aircon_cmd",
                          f"에어컨 #{unit + 1} {f['word']} {what} — {'장비로 보냄' if sent else '보류(장비로 보내지 않음)'} ({by})", source="user")
        out.append({"field": r["field"], "value": r["value"], "status": st, "note": why})
    return {"results": out, "pending": pending_view(storage, panel)}


def pending_view(storage, panel: str) -> dict:
    """판넬의 에어컨마다: 대기 중인 변경과 최근 처리 5건."""
    ensure(storage)
    with storage._lock:
        rows = [dict(r) for r in storage._conn.execute("SELECT * FROM aircon_cmds WHERE panel=? ORDER BY id DESC LIMIT 200", (panel,))]
    out: dict = {}
    for r in rows:
        u = out.setdefault(str(r["unit"]), {"pending": [], "recent": []})
        if r["status"] == "pending":
            u["pending"].append({"field": r["field"], "value": r["value"], "by": r["by"], "at": r["created_at"]})
        elif len(u["recent"]) < 5:
            u["recent"].append({"field": r["field"], "value": r["value"], "status": r["status"], "note": r["note"], "by": r["by"], "at": r["decided_at"]})
    return {"units": out, "fields": FIELDS, "can_send": False, "hold_reason": HOLD_REASON}
