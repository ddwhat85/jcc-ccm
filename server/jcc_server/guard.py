"""고객 화면(JCC GUARD) — 안심 지수·지금 상태·판넬·위험 순간·이번 달 지켜낸 것.

고객이 보고 "돈 낼 만하다"고 느끼게 하되, 숫자는 실제 기록에서만 만든다. 가정이 든 값(순찰 비교)은
기준을 함께 돌려 화면이 '추정'이라고 밝힌다. 기존 계산(현장 목록·운영 리포트·경보 통계·남은 여유)을 재사용한다.
범위: panels=None이면 전부(JCC 직원 미리보기), 아니면 그 판넬만.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
PENALTY = {"crit": 20, "warn": 5, "offline": 10, "fire_watch": 5, "caution": 3, "life": 3, "overdue": 5}
# 한 판넬에서 경보가 줄줄이 나도(화재 때 여러 센서가 함께 넘음) 그 판넬 감점은 여기까지 — 0점은 고장난 화면처럼 보인다
PANEL_CAP = {"crit": 40, "warn": 15}
_INC_ORDER = {"fire": 0, "contact": 1, "actuator_fault": 2}   # 위험 순간 머리기사: 화재·단자 과열을 먼저
LIFE_DAYS = 60                 # 남은 여유가 이 안이면 감점
PATROLS_PER_DAY = 3            # 순찰 비교 추정의 기준(화면에 밝힌다)
_WATCH = ("watch", "warning")
_CAUTION = ("watch", "warning", "danger")
_WORD = {"fire": {"normal": "낮음", "watch": "지켜보는 중", "warning": "주의", "danger": "위험", "critical": "위험"},
         "contact": {"normal": "정상", "watch": "지켜보는 중", "warning": "주의", "danger": "위험"},
         "dew": {"normal": "정상", "watch": "지켜보는 중", "warning": "결로 주의", "danger": "결로 위험"}}
_STATUS_WORD = {"crit": "위험", "offline": "연결 끊김", "warn": "지켜보는 중", "ok": "정상"}
_SELF = ("vent_open", "vent_close", "vent_hold", "dew_actuate", "edge_actuate")   # 판넬이 스스로 한 일


def _kst(ts) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%m월 %d일 %H:%M") if ts else ""


def life_plain(label: str) -> str:
    """남은 여유 항목 이름을 고객 말로 — 직원용 '접점 발열 잔차 → 위험 기준 11°C'는 고객에게 뜻이 없다."""
    label = label or ""
    if "접점" in label:
        return "단자 점검 시기"
    if "→" in label:
        return f"{label.split('→')[0].strip()} 기준선까지"
    return label or "부품 점검 시기"


def _life(lf):
    return dict(lf, label=life_plain(lf.get("label"))) if lf else None


# ── 안심 지수(투명한 감점식) ───────────────────────────────
def score(rows: list, now: float) -> dict:
    items, crit_any = [], False
    off_total = 0
    for p in rows:
        nm, al, pr = p["panel_name"], p.get("alarms") or {}, p.get("predict") or {}
        if al.get("crit"):
            crit_any = True
            items.append({"text": f"{nm} 위험 경보 {al['crit']}건", "minus": min(PANEL_CAP["crit"], PENALTY["crit"] * al["crit"])})
        if al.get("warn"):
            items.append({"text": f"{nm} 주의 경보 {al['warn']}건", "minus": min(PANEL_CAP["warn"], PENALTY["warn"] * al["warn"])})
        off = max(0, (p.get("ccm_total") or 0) - (p.get("ccm_online") or 0))
        if off:
            off_total += off
            items.append({"text": f"{nm} 감시 장치 끊김 {off}대", "minus": PENALTY["offline"] * off})
        if pr.get("fire") in _WATCH:
            items.append({"text": f"{nm} 화재 징조 지켜보는 중", "minus": PENALTY["fire_watch"]})
        if pr.get("contact") in _CAUTION:
            items.append({"text": f"{nm} 단자 발열 주의", "minus": PENALTY["caution"]})
        if pr.get("dew") in _CAUTION:
            items.append({"text": f"{nm} 결로 주의", "minus": PENALTY["caution"]})
        lf = p.get("life")
        if lf and (lf.get("status") == "reached" or (lf.get("days") is not None and lf["days"] <= LIFE_DAYS)):
            items.append({"text": f"{nm} {life_plain(lf['label'])} {lf['say']}", "minus": PENALTY["life"]})
        if p.get("next_inspection") and p["next_inspection"] < now:
            items.append({"text": f"{nm} 정기 점검 기한 지남", "minus": PENALTY["overdue"]})
    items.sort(key=lambda i: -i["minus"])
    total = sum(i["minus"] for i in items)
    dues = [p["next_inspection"] for p in rows if p.get("next_inspection")]
    nxt = min(dues) if dues else None
    checks = [{"label": "위험 경보", "value": str(sum((p.get("alarms") or {}).get("crit", 0) for p in rows))},
              {"label": "끊긴 감시 장치", "value": str(off_total)},
              {"label": "정기 점검", "value": "기록 없음" if nxt is None else
               ("기한 지남" if nxt < now else f"D-{int((nxt - now) // 86400)}")}]
    watching = any(p.get("status") not in (None, "ok") for p in rows)
    return {"score": max(0, 100 - total), "color": "crit" if crit_any else "warn" if (total and watching) else "ok",
            "items": items, "checks": checks}


def state_line(rows: list) -> dict:
    if not rows:
        return {"title": "아직 연결된 판넬이 없습니다", "sub": "설치가 끝나면 이곳에서 24시간 지켜봅니다"}
    crit = [p for p in rows if (p.get("alarms") or {}).get("crit") or p.get("status") == "crit"]
    if crit:
        p = crit[0]
        return {"title": f"위험 — {p['panel_name']}", "sub": (p.get("why") or ["확인이 필요합니다"])[0]}
    watch = [p for p in rows if p.get("status") != "ok"]
    if watch:
        p = watch[0]
        return {"title": f"판넬 {len(rows)}면 · {len(watch)}곳 지켜보는 중",
                "sub": f"{p['panel_name']} — {(p.get('why') or ['지켜보는 중'])[0]}"}
    return {"title": "모든 판넬 안전합니다", "sub": "24시간 감시 중"}


# ── 저장소를 읽는 부분 ─────────────────────────────────────
def _kind(s) -> str:
    """센서 종류 — 탐색 결과(kind)가 있으면 그것, 없으면 단위·키로 짐작(온도·습도만 필요)."""
    if s.get("kind"):
        return s["kind"]
    unit, key = (s.get("unit") or "").upper(), (s.get("sensor_key") or "").lower()
    if "RH" in unit or "humid" in key:
        return "humidity"
    if unit in ("C", "°C", "DEGC") or "temp" in key:
        return "temp"
    return ""


def _latest(storage) -> dict:
    """판넬 → {temp, humidity, names{(dev,key)}, keys}."""
    out = {}
    for p in storage.list_panels():
        d = out.setdefault(p["panel"], {"temp": None, "humidity": None, "names": {}, "keys": {p["panel"]}})
        for c in p["ccms"]:
            d["keys"].add(c["device_id"])
            for s in c.get("latest") or []:
                d["names"][(c["device_id"], s["sensor_key"])] = s.get("name") or s["sensor_key"]
                v = s.get("value")
                if _kind(s) == "temp" and v is not None and d["temp"] is None and "cabinet" in s["sensor_key"]:
                    d["temp"] = round(v, 1)
                if _kind(s) == "humidity" and v is not None and d["humidity"] is None:
                    d["humidity"] = round(v)
        if d["temp"] is None:      # 함내 온도가 없으면 첫 온도 센서
            d["temp"] = next((round(s["value"], 1) for c in p["ccms"] for s in c.get("latest") or []
                              if _kind(s) == "temp" and s.get("value") is not None), None)
    return out


def _panel_model(p, lat) -> dict:
    pr = p.get("predict") or {}
    return {"panel": p["panel"], "panel_name": p["panel_name"], "site": p.get("site") or "",
            "status": p["status"], "word": _STATUS_WORD.get(p["status"], p["status"]), "why": p.get("why") or [],
            "temp": (lat or {}).get("temp"), "humidity": (lat or {}).get("humidity"),
            "fire": _WORD["fire"].get(pr.get("fire") or "", "—"), "contact": _WORD["contact"].get(pr.get("contact") or "", "—"),
            "dew": _WORD["dew"].get(pr.get("dew") or "", "—"), "vent_open": bool(pr.get("vent_open")),
            "life": _life(p.get("life")), "alarms": p.get("alarms"), "next_inspection": p.get("next_inspection"),
            "ccm_online": p.get("ccm_online"), "ccm_total": p.get("ccm_total"), "last_seen": p.get("last_seen")}


def _incident(storage, rows, lat, panels):
    """가장 최근 활성 위험 경보 → 감지 · 판넬이 한 일 · JCC가 한 일 · 알림."""
    keys = None if panels is None else set().union(*(lat.get(p, {}).get("keys", {p}) for p in panels)) if panels else set()
    act = [a for a in storage.list_active_alarms() if a.get("severity") == "crit" and (keys is None or a.get("device_id") in keys)]
    if not act:
        return None
    a = min(act, key=lambda x: (_INC_ORDER.get(x["kind"], 9), -x["raised_at"]))
    pid = next((pn for pn, d in lat.items() if a["device_id"] in d["keys"]), a["device_id"])
    pname = next((r["panel_name"] for r in rows if r["panel"] == pid), pid)
    names = (lat.get(pid) or {}).get("names", {})
    steps = []
    for e in reversed(storage.events_since(a["raised_at"] - 30, (lat.get(pid) or {}).get("keys", {a["device_id"]}), 200)):
        if e["etype"] in _SELF:
            steps.append({"ts": e["ts"], "text": f"판넬이 스스로 조치했습니다 — {e['detail']}", "done": True})
        elif e["etype"] == "escalate":
            steps.append({"ts": e["ts"], "text": "담당자에게 알림을 보냈습니다", "done": True})
    if a.get("acked_at"):
        steps.append({"ts": a["acked_at"], "text": f"JCC가 확인했습니다 · {a.get('acked_by') or ''}".rstrip(" ·"), "done": True})
    else:
        steps.append({"ts": None, "text": "JCC 관제실이 확인하는 중입니다", "done": False})
    steps.sort(key=lambda s: (s["ts"] is None, s["ts"] or 0))
    return {"id": a["id"], "panel": pid, "panel_name": pname,
            "sensor_name": names.get((a["device_id"], a.get("sensor_key") or ""), a.get("sensor_key") or "CCM"),
            "detail": a.get("detail") or a["kind"], "raised_at": a["raised_at"], "acked_at": a.get("acked_at"),
            "steps": steps}


def guard_view(storage, panels: set | None, site: str, now: float | None = None, customer: dict | None = None) -> dict:
    """customer = 고객사 행(담당 엔지니어·연락처). 정해 두지 않았으면 마지막 정기 점검자와 공통 관제실 번호."""
    from .fleet import fleet
    from .inspection import history as insp_history
    now = time.time() if now is None else now
    rows = fleet(storage, panels)
    lat = _latest(storage)
    model = [_panel_model(p, lat.get(p["panel"])) for p in rows]
    dues = [p["next_inspection"] for p in rows if p.get("next_inspection")]
    last = next(iter(insp_history(storage, panels, include_drafts=False, limit=1)), None)
    cu = customer or {}
    phone = (cu.get("engineer_phone") or "").strip() or os.environ.get("JCC_SUPPORT_PHONE", "").strip()
    return {
        "site": site, "now": now, "index": score(rows, now), "state": state_line(rows), "panels": model,
        "incident": _incident(storage, rows, lat, panels),
        "contact": {"phone": phone, "engineer": (cu.get("engineer") or "").strip() or (last or {}).get("by") or "",
                    "desk": "JCC 관제실", "assigned": bool((cu.get("engineer") or "").strip())},
        "next_inspection": min(dues) if dues else None,
        "month": month_view(storage, panels, datetime.fromtimestamp(now, KST).strftime("%Y-%m"), now),
    }


def month_view(storage, panels: set | None, period: str, now: float | None = None) -> dict:
    """그 달 지켜낸 것. 설치 첫 며칠처럼 기록이 적으면 building=True(0을 성과처럼 보이지 않게)."""
    from .monthly import _alarm_stats, _downtime, month_bounds
    now = time.time() if now is None else now
    start, end = month_bounds(period)
    until = min(end, now)
    devs = {d["device_id"]: d for d in storage.list_devices()
            if panels is None or (d.get("panel") or d["device_id"]) in panels}
    pids = {(d.get("panel") or k) for k, d in devs.items()}
    keys = set(devs) | pids
    first = min(((d.get("first_seen") or start) for d in devs.values()), default=None)
    span = max(0.0, until - max(start, first)) if first is not None else 0.0
    out = {"period": period, "range": [start, end], "building": span < 3 * 86400,
           "hours": 0, "patrols": {"count": 0, "per_day": PATROLS_PER_DAY},
           "precursors": {"total": 0, "fire": 0, "contact": 0, "dew": 0},
           "actions": {"total": 0, "vent": 0, "dew": 0}, "remote": 0, "ack_min_avg": None}
    if not devs or span <= 0:
        return out
    base = storage.build_report(devices=keys, since=start, until=until)
    pr, heal = base["summary"]["predict"], base["summary"]["heal"]
    down = sum(_downtime(storage, set(devs), start, until).values())
    frac = max(0.0, 1.0 - down / (span * len(devs)))
    vent = pr["fire"]["vent_auto"] + pr["fire"]["vent_edge"]
    dew_act = pr["dew"]["actuations"]
    out.update(
        hours=int(round(span / 3600 * frac)),
        patrols={"count": int(round(span / 86400 * PATROLS_PER_DAY)), "per_day": PATROLS_PER_DAY},
        precursors={"total": pr["fire"]["detected"] + pr["contact"]["detected"] + pr["dew"]["detected"],
                    "fire": pr["fire"]["detected"], "contact": pr["contact"]["detected"], "dew": pr["dew"]["detected"]},
        actions={"total": vent + dew_act, "vent": vent, "dew": dew_act},
        remote=heal["auto_fixed"], ack_min_avg=_alarm_stats(storage, keys, start, end)["ack_min_avg"])
    return out


def panel_detail(storage, panel: str, now: float | None = None) -> dict | None:
    from .fleet import fleet
    now = time.time() if now is None else now
    rows = fleet(storage, {panel})
    if not rows:
        return None
    lat = _latest(storage)
    keys = (lat.get(panel) or {}).get("keys", {panel})
    from .storage import Storage as _S
    alarm_kinds = set(_S._SEVERITY)
    tl = []
    for e in storage.events_since(now - 7 * 86400, keys, 200):
        et = e["etype"]
        if et in _SELF:
            who = "판넬이 스스로"
        elif et == "ack":
            who = "확인"
        elif et in ("inspection", "commission"):
            who = "JCC 점검"
        elif et == "escalate":
            who = "알림 발송"
        elif et in alarm_kinds:
            who = "감지"
        else:
            continue
        tl.append({"ts": e["ts"], "who": who, "text": e["detail"]})
    return {"panel": _panel_model(rows[0], lat.get(panel)), "timeline": tl[:20]}
