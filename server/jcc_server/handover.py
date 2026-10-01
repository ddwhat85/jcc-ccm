"""근무 인계 요약 — '내가 마지막으로 확인한 뒤' 무슨 일이 있었고 지금 무엇을 손봐야 하나.

교대·출근 때 직원이 경보 목록·현장 목록·점검 기록을 따로 뒤지지 않게 한 장으로 모은다.
기준 시각은 사람마다 서버에 둔다(휴대폰·사무실 PC 어디서 봐도 같게). 처음이면 12시간 전, 최대 7일.
범위(고객 계정)는 부르는 쪽이 넘긴 기기·판넬 키로 거른다.
"""
from __future__ import annotations

import time

DEFAULT_HOURS = 12
MAX_DAYS = 7

SCHEMA = "CREATE TABLE IF NOT EXISTS handover_seen (username TEXT PRIMARY KEY, ts REAL);"


def ensure(storage) -> None:
    if getattr(storage, "_handover_ready", False):
        return
    with storage._lock:
        storage._conn.executescript(SCHEMA)
        storage._conn.commit()
    storage._handover_ready = True


def last_seen(storage, username: str):
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT ts FROM handover_seen WHERE username=?", (username,)).fetchone()
    return r["ts"] if r else None


def mark_seen(storage, username: str, now: float | None = None) -> float:
    ensure(storage)
    now = time.time() if now is None else now
    with storage._lock:
        storage._conn.execute("INSERT INTO handover_seen (username, ts) VALUES (?, ?) "
                              "ON CONFLICT(username) DO UPDATE SET ts=excluded.ts", (username, now))
        storage._conn.commit()
    return now


def summary(storage, username: str, keys=None, panels=None, now: float | None = None) -> dict:
    """keys = 허용 기기·판넬 키(None이면 전부), panels = 허용 판넬 id(None이면 전부)."""
    from .inspection import history as insp_history
    now = time.time() if now is None else now
    seen = last_seen(storage, username)
    since = max(seen if seen is not None else now - DEFAULT_HOURS * 3600, now - MAX_DAYS * 86400)

    plist = [p for p in storage.list_panels() if panels is None or p["panel"] in panels]
    pname, sname = {}, {}
    for p in plist:
        pname[p["panel"]] = p["panel_name"]
        for c in p["ccms"]:
            pname[c["device_id"]] = p["panel_name"]
            for s in c.get("latest") or []:
                sname[(c["device_id"], s["sensor_key"])] = s.get("name") or s["sensor_key"]

    def where(a):
        return {"panel_name": pname.get(a["device_id"], a["device_id"]),
                "sensor_name": sname.get((a["device_id"], a.get("sensor_key") or ""), a.get("sensor_key") or "")}

    new = storage.alarms_since(since, keys, 100_000)
    active = [a for a in storage.list_active_alarms() if keys is None or a.get("device_id") in keys]
    unacked = sorted((a for a in active if not a.get("acked_at")),
                     key=lambda a: (a.get("severity") != "crit", a["raised_at"]))
    offline = [{"panel_name": p["panel_name"], "device_id": c["device_id"], "last_seen": c.get("last_seen")}
               for p in plist for c in p["ccms"] if not c.get("online")]
    notes = [{"ts": e["ts"], "detail": e["detail"]} for e in storage.events_since(since, keys, 200)
             if e.get("etype") == "ack" and e.get("source") == "user" and " · " in (e.get("detail") or "")]
    done = [{"kind": "정기 점검", "panel_name": h.get("panel_name") or h["panel"], "ts": h["done_at"], "by": h.get("by"),
             "result": {"ok": "이상 없음", "fix": "현장 조치 완료", "bad": "조치 필요 항목 있음"}.get(h.get("overall"), "")}
            for h in insp_history(storage, panels, include_drafts=False) if (h.get("done_at") or 0) >= since]
    done += [{"kind": "설치 점검", "panel_name": pname.get(r["panel"], r["panel"]), "ts": r["ts"], "by": r.get("by"),
              "result": {"pass": "합격", "warn": "조건부", "fail": "불합격", "wait": "미완료"}.get(r.get("overall"), "")}
             for r in storage.list_commission_reports(panels, 200) if (r.get("ts") or 0) >= since]
    done.sort(key=lambda x: -x["ts"])
    out = {
        "since": since, "seen": seen, "now": now,
        "todo": {
            "unacked": len(unacked),
            "unacked_list": [{"id": a["id"], "detail": a.get("detail") or a["kind"], "severity": a.get("severity"),
                              "raised_at": a["raised_at"], **where(a)} for a in unacked[:6]],
            "offline": offline[:10], "offline_count": len(offline),
        },
        "happened": {
            "alarms": len(new), "crit": sum(1 for a in new if a.get("severity") == "crit"),
            "cleared": sum(1 for a in new if a.get("cleared_at")),
            "no_cause": sum(1 for a in new if a.get("cleared_at") and not a.get("cause")),
            "notes": notes[:6], "done": done[:8],
        },
    }
    out["quiet"] = not (out["todo"]["unacked"] or offline or new or notes or done)
    return out
