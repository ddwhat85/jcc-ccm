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


def _events(storage, keys, since: float, until: float | None = None, limit: int = 300, newest: bool = False) -> list:
    """고객 화면용 활동 기록: [since, until] 구간, '벤트 열림 유지'(몇 초마다 쌓이는 상태 기록)는 빼고.
    newest=False면 오래된 것부터(사건 흐름), True면 최신부터(판넬 상세 최근 기록)."""
    q = "SELECT id, ts, device_id, sensor_key, etype, detail, source FROM events WHERE ts >= ? AND etype != 'vent_hold'"
    args: list = [since]
    if until is not None:
        q += " AND ts <= ?"
        args.append(until)
    if keys is not None:
        frag, fa = storage._in_devices(keys)
        q += " AND " + frag
        args.extend(fa)
    q += f" ORDER BY ts {'DESC' if newest else 'ASC'} LIMIT ?"
    args.append(limit)
    with storage._lock:
        return [dict(r) for r in storage._conn.execute(q, args).fetchall()]


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
    for e in _events(storage, (lat.get(pid) or {}).get("keys", {a["device_id"]}), a["raised_at"] - 30):
        if e["etype"] in _SELF:
            steps.append({"ts": e["ts"], "text": f"판넬이 스스로 조치했습니다 — {_plain(e['detail'])}", "done": True})
        elif e["etype"] == "escalate":
            steps.append({"ts": e["ts"], "text": "담당자에게 알림을 보냈습니다", "done": True})
    if a.get("acked_at"):
        steps.append({"ts": a["acked_at"], "text": f"JCC가 확인했습니다 · {a.get('acked_by') or ''}".rstrip(" ·"), "done": True})
    else:
        steps.append({"ts": None, "text": "JCC 관제실이 확인하는 중입니다", "done": False})
    steps.sort(key=lambda s: (s["ts"] is None, s["ts"] or 0))
    steps = [dict(x, kind="s") for x in steps]
    steps = [{k: v for k, v in x.items() if k not in ("kind", "n")} for x in _squash(steps)]
    return {"id": a["id"], "panel": pid, "panel_name": pname,
            "sensor_name": names.get((a["device_id"], a.get("sensor_key") or ""), a.get("sensor_key") or "CCM"),
            "detail": _plain(a.get("detail") or a["kind"]), "raised_at": a["raised_at"], "acked_at": a.get("acked_at"),
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
        "todo": todo(storage, model, panels, dues, now),
    }


_TODO_CACHE: dict = {}


def todo(storage, model: list, panels: set | None, dues: list, now: float) -> list:
    """'제가 할 일이 있나요?' — 고객이 오랜만에 열어도 바로 알게, 지금 볼 것만 짧게(제목 한 줄 + 자세히에 나올 이유).
    판넬 상태(주의·위험·끊김)·원인 분석 '조치 필요'·공조 필터 청소 시기·정기 점검 기한. 원인 분석은 무거워 1분 동안 재사용."""
    out = []
    for p in model:
        if p["status"] in ("crit", "warn", "offline"):
            out.append({"panel": p["panel"], "panel_name": p["panel_name"], "level": "act" if p["status"] == "crit" else "watch",
                        "title": {"crit": "위험", "warn": "지켜보는 중", "offline": "감시 장치 연결 끊김"}[p["status"]],
                        "why": (p.get("why") or [""])[0] or ("감시 장치와 연결이 끊겼습니다 — 판넬은 현장에서 스스로 지킵니다"
                                                             if p["status"] == "offline" else ""), "tab": "now"})
    key = (tuple(sorted(panels)) if panels is not None else None)
    hit = _TODO_CACHE.get(key)
    if hit and now - hit[0] < 60:
        extra = hit[1]
    else:
        extra = []
        from .thermal import panel_thermal
        from .equipment import get as eq_get, view as eq_view
        for p in model:
            try:
                t = panel_thermal(storage, p["panel"], now)
            except Exception:  # noqa: BLE001 - 할 일 목록 하나 때문에 화면 전체가 멈추면 안 된다
                t = None
            if t and not t.get("building") and t.get("level") == "act":
                top = (t.get("actions") or [{}])[0]
                extra.append({"panel": p["panel"], "panel_name": p["panel_name"], "level": "act", "title": "온도가 자주 기준을 넘음",
                              "why": (top.get("title") or "") + (" — " + top["why"] if top.get("why") else ""), "tab": "tk"})
            v = eq_view(eq_get(storage, p["panel"]), now)
            for u in (v or {}).get("units", []):
                if u.get("service_due"):
                    extra.append({"panel": p["panel"], "panel_name": p["panel_name"], "level": "info",
                                  "title": f"{u['word']} 필터 청소 시기 지남", "why": "JCC가 청소 일정을 잡아 연락드립니다.", "tab": "eq"})
        _TODO_CACHE[key] = (now, extra)
    seen = {(x["panel"], x["tab"]) for x in out}
    out += [x for x in extra if (x["panel"], x["tab"]) not in seen]
    if dues and min(dues) < now:
        out.append({"panel": "", "panel_name": "", "level": "info", "title": "정기 점검 기한 지남",
                    "why": "JCC가 점검 일정을 잡아 연락드립니다.", "tab": ""})
    rank = {"act": 0, "watch": 1, "info": 2}
    return sorted(out, key=lambda x: rank.get(x["level"], 3))


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
           "actions": {"total": 0, "vent": 0, "dew": 0}, "remote": 0, "ack_min_avg": None,
           "uptime": None, "down_min": 0, "devices": len(devs)}
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
        remote=heal["auto_fixed"], ack_min_avg=_alarm_stats(storage, keys, start, end)["ack_min_avg"],
        # 감시 가동률: 감시 장치(CCM)가 서버에 값을 보낸 시간 / 지난 시간. 끊긴 시간 = 'CCM 침묵' 경보 구간의 합(장치별)
        uptime=round(frac * 100, 1), down_min=int(round(down / 60)))
    if down > 0:                       # 끊긴 시간을 원인별로 — '그동안 판넬이 스스로 지켰다'는 말은 통신만 끊겼을 때만 맞다
        from .outages import unprotected_minutes
        out["down_kinds"] = unprotected_minutes(storage, set(devs), start, until, now)
    return out


def outages_view(storage, panels: set | None, period: str, now: float | None = None) -> dict:
    """그 달 감시 끊김 기록(고객 범위만)."""
    from .monthly import month_bounds
    from .outages import list_range
    now = time.time() if now is None else now
    start, end = month_bounds(period)
    keys = None if panels is None else ({d["device_id"] for d in storage.list_devices()
                                         if (d.get("panel") or d["device_id"]) in panels} | set(panels))
    return {"period": period, "items": list_range(storage, keys, start, min(end, now + 1), now)}


def alarm_view(storage, alarm_id: int):
    """알림 링크(?alarm=ID)로 들어온 경보 한 건 — 판넬·센서 이름과 지금 상태(진행 중/해제)."""
    a = storage.get_alarm(alarm_id)
    if not a:
        return None
    lat = _latest(storage)
    pid = next((pn for pn, d in lat.items() if a["device_id"] in d["keys"]), a["device_id"])
    pname = next((p["panel_name"] for p in storage.list_panels() if p["panel"] == pid), pid)
    names = (lat.get(pid) or {}).get("names", {})
    return {"id": a["id"], "panel": pid, "panel_name": pname, "device_id": a["device_id"], "sensor_key": a.get("sensor_key") or "", "kind": a["kind"],
            "sensor_name": names.get((a["device_id"], a.get("sensor_key") or ""), a.get("sensor_key") or "CCM"),
            "detail": a.get("detail") or a["kind"], "severity": a.get("severity"), "raised_at": a["raised_at"],
            "acked_at": a.get("acked_at"), "acked_by": a.get("acked_by"), "cleared_at": a.get("cleared_at"),
            "cause": a.get("cause"), "note": a.get("ack_note")}


# 사건 보고서 — 경보 한 건을 고객이 윗선에 그대로 올릴 수 있게 한 장으로(모든 숫자는 실제 기록)
_CAUSE_SAY = {"real": "실제 이상 — 조치했습니다", "false": "센서 오작동(오경보)으로 확인했습니다",
              "work": "현장 작업·시험 중에 난 경보로 확인했습니다", "other": "그 밖의 원인"}
_SEV_WORD = {"crit": "위험", "warn": "주의"}


def _plain(text: str) -> str:
    """기록 문구를 고객 말로: 내부 지수(FRI)와 '…/분' 상승 속도처럼 직원용 숫자 조각은 뺀다(나머지 실제 값은 그대로)."""
    import re
    t = re.sub(r"\s*\(FRI [\d.]+\)", "", str(text or ""))
    parts = [x for x in t.split(" — ") if "/분" not in x]
    return " — ".join(parts).strip() or t.strip()


def _squash(tl: list) -> list:
    """같은 사람이 같은 일을 연달아 기록했으면 한 줄로(… 2회)."""
    out = []
    for x in tl:
        if out and out[-1]["kind"] == x["kind"] and out[-1]["text"] == x["text"]:
            out[-1]["n"] = out[-1].get("n", 1) + 1
            continue
        out.append(dict(x))
    for x in out:
        if x.get("n", 1) > 1:
            x["text"] += f" ({x['n']}회)"
    return out


def _panel_of(lat, dev):
    return next((pn for pn, d in lat.items() if dev in d["keys"]), dev)


def incidents(storage, panels: set | None, period: str, now: float | None = None) -> dict:
    """그 달의 경보(위험·주의) 목록 — 사건 보고서 고르기용. 감시 장치 끊김(침묵)은 가동률에서 따로 보인다."""
    from .monthly import month_bounds
    now = time.time() if now is None else now
    start, end = month_bounds(period)
    lat = _latest(storage)
    names = {p["panel"]: p["panel_name"] for p in storage.list_panels()}
    keys = None if panels is None else set().union(*(lat.get(p, {}).get("keys", {p}) for p in panels)) if panels else set()
    if keys is not None and not keys:
        return {"period": period, "items": []}
    q = ("SELECT id, device_id, sensor_key, kind, detail, severity, raised_at, acked_at, cleared_at FROM alarms "
         "WHERE raised_at >= ? AND raised_at < ? AND kind != 'silent' AND severity IN ('crit', 'warn')")
    args: list = [start, min(end, now + 1)]
    if keys is not None:
        frag, fa = storage._in_devices(keys)
        q += " AND " + frag
        args.extend(fa)
    with storage._lock:
        rows = storage._conn.execute(q + " ORDER BY raised_at DESC LIMIT 100", args).fetchall()
    items = []
    for r in rows:
        pid = _panel_of(lat, r["device_id"])
        sn = (lat.get(pid) or {}).get("names", {}).get((r["device_id"], r["sensor_key"] or ""), r["sensor_key"] or "감시 장치")
        items.append({"id": r["id"], "panel": pid, "panel_name": names.get(pid, pid), "sensor_name": sn,
                      "severity": r["severity"], "word": _SEV_WORD.get(r["severity"], ""), "detail": r["detail"] or r["kind"],
                      "raised_at": r["raised_at"], "acked_at": r["acked_at"], "cleared_at": r["cleared_at"]})
    return {"period": period, "items": items}


def _value_at(storage, dev, key, since, until, peak=False):
    if not key:
        return None
    with storage._lock:
        if peak:
            r = storage._conn.execute("SELECT MAX(value) AS v FROM readings WHERE device_id=? AND sensor_key=? AND ok=1 "
                                      "AND ts >= ? AND ts <= ?", (dev, key, since, until)).fetchone()
        else:
            r = storage._conn.execute("SELECT value AS v FROM readings WHERE device_id=? AND sensor_key=? AND ok=1 "
                                      "AND ts >= ? AND ts <= ? ORDER BY ts LIMIT 1", (dev, key, since, until)).fetchone()
        u = storage._conn.execute("SELECT unit FROM readings WHERE device_id=? AND sensor_key=? ORDER BY ts DESC LIMIT 1",
                                  (dev, key)).fetchone()
    if not r or r["v"] is None:
        return None
    return {"value": round(r["v"], 2), "unit": (u["unit"] if u else "") or ""}


def incident_report(storage, alarm_id: int, now: float | None = None):
    """경보 한 건의 사건 보고서: 무엇을·언제 감지 → 판넬이 스스로 한 일 → 알림 → JCC 확인 → 정상 회복."""
    now = time.time() if now is None else now
    a = alarm_view(storage, alarm_id)
    if a is None:
        return None
    lat = _latest(storage)
    keys = (lat.get(a["panel"]) or {}).get("keys", {a["device_id"]})
    t0 = a["raised_at"]
    t1 = a["cleared_at"] or now
    tl = []
    clear_types = {a["kind"] + "_clear"} | ({"alarm_clear"} if a["kind"] in ("alarm", "alarm_warn") else set())
    for e in _events(storage, keys, t0 - 600, t1 + 300, 400):    # '열림 유지'는 새 조치가 아니라 DB에서 이미 뺐다
        et = e["etype"]
        if et in _SELF:
            tl.append({"ts": e["ts"], "who": "판넬이 스스로", "text": _plain(e["detail"]), "kind": "self"})
        elif et == "escalate":
            tl.append({"ts": e["ts"], "who": "알림 발송", "text": "담당자에게 경보 알림을 보냈습니다", "kind": "notify"})
        elif et == "ack":
            tl.append({"ts": e["ts"], "who": "JCC 확인", "text": _plain(e["detail"]), "kind": "ack"})
        elif et in clear_types and (e.get("sensor_key") or "") == (a.get("sensor_key") or ""):   # 이 경보의 회복만
            tl.append({"ts": e["ts"], "who": "정상 회복", "text": _plain(e["detail"]), "kind": "clear"})
    # 경보 발생은 맨 앞에 늘 넣는다(감지 기록이 events에 없어도)
    tl.insert(0, {"ts": t0, "who": "감지", "text": f"{a['sensor_name']} — {_plain(a['detail'])}", "kind": "detect"})
    tl.sort(key=lambda x: x["ts"])
    tl = _squash(tl)
    n_self = sum(x.get("n", 1) for x in tl if x["kind"] == "self")
    ack_min = round((a["acked_at"] - t0) / 60, 1) if a.get("acked_at") else None
    clear_min = round((a["cleared_at"] - t0) / 60, 1) if a.get("cleared_at") else None
    first = _value_at(storage, a["device_id"], a.get("sensor_key"), t0 - 120, t0 + 120)
    peak = _value_at(storage, a["device_id"], a.get("sensor_key"), t0 - 120, t1, peak=True)
    parts = [f"{a['panel_name']}의 {a['sensor_name']}에서 이상을 감지했습니다"]
    if n_self:
        parts.append(f"판넬이 스스로 {n_self}번 조치했습니다")
    if ack_min is not None:
        parts.append(f"JCC가 {_mins(ack_min)} 만에 확인했습니다")
    parts.append(f"{_mins(clear_min)} 뒤 정상으로 돌아왔습니다" if clear_min is not None else "아직 지켜보고 있습니다")
    a = dict(a, detail=_plain(a["detail"]))
    return dict(a, severity_word=_SEV_WORD.get(a.get("severity"), ""), timeline=tl, self_actions=n_self,
                ack_min=ack_min, clear_min=clear_min, first=first, peak=peak,
                cause_say=_CAUSE_SAY.get(a.get("cause") or "", ""), summary=" · ".join(parts) + ".",
                issued_at=now, open=not a.get("cleared_at"))


def _mins(m: float) -> str:
    if m < 1:
        return f"{max(1, int(round(m * 60)))}초"
    if m < 120:
        return f"{m:g}분" if m < 10 else f"{int(round(m))}분"
    return f"{m / 60:.1f}시간"


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
    for e in _events(storage, keys, now - 7 * 86400, None, 200, newest=True):
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
        tl.append({"ts": e["ts"], "who": who, "text": _plain(e["detail"])})
    from .equipment import get as eq_get, view as eq_view
    from .aircon import panel_status
    eq = eq_view(eq_get(storage, panel))
    if eq:
        live = {u["index"]: u for u in panel_status(storage, panel, now)}
        for u in eq["units"]:          # 통신으로 연결된 에어컨: 지금 가동·설정·알람·가동률(고객에겐 IP·레지스터 같은 것은 빼고)
            a = live.get(u["i"])
            if a:
                u["live"] = {k: a.get(k) for k in ("state", "running", "alarm", "setpoint", "temp", "duty_24h", "duty_hot")}
    return {"panel": _panel_model(rows[0], lat.get(panel)), "timeline": tl[:20], "equipment": eq}
