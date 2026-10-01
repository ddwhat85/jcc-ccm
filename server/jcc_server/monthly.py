"""고객용 월간 리포트 — 고객사별 지난달 성과를 발행 시점 스냅숏으로 남긴다.

결과 언어로 쓴다(장비 용어보다 '무엇을 막았나'). 발행된 문서는 바뀌지 않는다 — 데이터 보존 정리가
있어도 계약 갱신 때 그대로 보여 줄 수 있게. 기간은 한국 시간(UTC+9, 서머타임 없음) 기준 달.

알림은 실제 문자가 나가므로 고객사별로 JCC가 켠 경우만(기본 꺼짐).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def month_bounds(period: str) -> tuple:
    """'YYYY-MM' → (시작 ts, 끝 ts) 한국 시간 그 달 1일 0시 ~ 다음 달 1일 0시."""
    y, m = (int(x) for x in period.split("-"))
    start = datetime(y, m, 1, tzinfo=KST)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=KST)
    return start.timestamp(), end.timestamp()


def prev_period(now: float | None = None) -> str:
    d = datetime.fromtimestamp(time.time() if now is None else now, KST).replace(day=1) - timedelta(days=1)
    return f"{d.year:04d}-{d.month:02d}"


def valid_period(p) -> bool:
    try:
        y, m = (int(x) for x in str(p).split("-"))
        return 2020 <= y <= 2100 and 1 <= m <= 12 and len(str(p)) == 7
    except ValueError:
        return False


def _downtime(storage, devs: set, start: float, end: float) -> dict:
    """CCM 침묵 경보 구간(기간에 겹친 만큼)의 합 — 기기별 초."""
    if not devs:
        return {}
    frag, fa = storage._in_devices(devs)
    with storage._lock:
        rows = storage._conn.execute(
            f"SELECT device_id, raised_at, cleared_at FROM alarms WHERE kind='silent' AND sensor_key='' "
            f"AND raised_at < ? AND (cleared_at IS NULL OR cleared_at > ?) AND {frag}", (end, start, *fa)).fetchall()
    out: dict = {}
    for r in rows:
        a, b = max(start, r["raised_at"]), min(end, r["cleared_at"] or end)
        out[r["device_id"]] = out.get(r["device_id"], 0.0) + max(0.0, b - a)
    return out


def _alarm_stats(storage, keys: set, start: float, end: float) -> dict:
    if not keys:
        return {"crit": 0, "warn": 0, "ack_min_avg": None, "faults": {}, "by_dev": {}}
    frag, fa = storage._in_devices(keys)
    with storage._lock:
        rows = storage._conn.execute(
            f"SELECT device_id, sensor_key, kind, severity, raised_at, acked_at FROM alarms "
            f"WHERE raised_at >= ? AND raised_at < ? AND {frag}", (start, end, *fa)).fetchall()
    crit = [r for r in rows if r["severity"] == "crit"]
    acks = [(r["acked_at"] - r["raised_at"]) / 60.0 for r in crit if r["acked_at"]]
    by_dev: dict = {}
    for r in rows:
        d = by_dev.setdefault(r["device_id"], {"crit": 0, "warn": 0, "fault": 0})
        d["crit" if r["severity"] == "crit" else "warn"] += 1
        if r["kind"] == "actuator_fault":
            d["fault"] += 1
    return {"crit": len(crit), "warn": len(rows) - len(crit),
            "ack_min_avg": round(sum(acks) / len(acks), 1) if acks else None, "by_dev": by_dev}


def build_monthly(storage, customer_id: int, period: str, now: float | None = None) -> dict | None:
    """고객사 한 곳의 그 달 리포트(스냅숏). 고객사가 없으면 None."""
    now = time.time() if now is None else now
    cust = next((c for c in storage.accounts.list_customers() if c["id"] == customer_id), None)
    if cust is None:
        return None
    start, end = month_bounds(period)
    owner = storage.accounts.panel_owner_map()
    panels = sorted(p for p, c in owner.items() if c == customer_id)
    all_panels = {p["panel"]: p for p in storage.list_panels()}
    devs = {d["device_id"]: d for d in storage.list_devices() if (d.get("panel") or d["device_id"]) in panels}
    keys = set(devs) | set(panels)
    base = storage.build_report(devices=keys, since=start, until=min(end, now))
    down = _downtime(storage, set(devs), start, end)
    al = _alarm_stats(storage, keys, start, end)
    cms = storage.list_commission_reports(set(panels))
    last_cm = {}
    for r in cms:                                    # 최신순 — 판넬별 첫 번째가 최근
        last_cm.setdefault(r["panel"], r)

    def uptime(dev_ids):
        tot = up = 0.0
        for d in dev_ids:
            first = (devs[d].get("first_seen") or start)
            span = max(0.0, min(end, now) - max(start, first))     # 설치 전 기간은 빼고
            tot += span
            up += max(0.0, span - down.get(d, 0.0))
        return None if tot <= 0 else round(100.0 * up / tot, 2)

    rows = []
    for p in panels:
        pdevs = [d for d in devs if (devs[d].get("panel") or d) == p]
        bd = [al["by_dev"].get(k, {}) for k in pdevs + [p]]
        cm = last_cm.get(p)
        rows.append({"panel": p, "panel_name": (all_panels.get(p) or {}).get("panel_name") or p,
                     "ccms": len(pdevs), "uptime": uptime(pdevs),
                     "crit": sum(x.get("crit", 0) for x in bd), "warn": sum(x.get("warn", 0) for x in bd),
                     "faults": sum(x.get("fault", 0) for x in bd),
                     "commission": cm["overall"] if cm else None})
    pr = base["summary"]["predict"]
    heal = base["summary"]["heal"]
    rep = {
        "customer": cust["name"], "customer_id": customer_id, "period": period,
        "range": [start, end], "generated_at": now, "panels": rows,
        "summary": {
            "panels": len(panels), "uptime": uptime(list(devs)),
            "precursors": pr["fire"]["detected"] + pr["contact"]["detected"] + pr["dew"]["detected"],
            "fire": pr["fire"], "contact": pr["contact"]["detected"], "dew": pr["dew"],
            "vent_auto": pr["fire"]["vent_auto"] + pr["fire"]["vent_edge"],
            "alarms_crit": al["crit"], "alarms_warn": al["warn"], "ack_min_avg": al["ack_min_avg"],
            "self_heal": heal["auto_fixed"], "self_heal_rate": heal["success_rate"],
            "faults": sum(r["faults"] for r in rows),
        },
        "problems": base["problems"][:6],
    }
    rep["advice"] = _advice(rep, base, cms)
    return rep


def _advice(rep, base, cms) -> list:
    out = []
    for pb in base["problems"]:
        d = pb.get("detail") or ""
        if "드리프트" in d:
            out.append(f"{pb['name']}: 값이 한쪽으로 계속 이동했습니다 — 센서 교정을 권합니다")
        elif "고착" in d:
            out.append(f"{pb['name']}: 값이 멈춘 적이 있습니다 — 센서 상태 점검을 권합니다")
        elif "침묵" in d and not pb.get("sensor_key"):
            out.append(f"{pb['name']}: 통신이 끊긴 적이 있습니다 — 네트워크·전원 점검을 권합니다")
    if rep["summary"]["faults"]:
        out.append(f"벤트·히터·팬 작동 실패 {rep['summary']['faults']}건 — 구동기·배선 점검을 권합니다")
    if any("릴레이만" in (it.get("detail") or "") for r in cms for st in r["report"].get("steps", [])
           for it in st.get("items", [])):
        out.append("위치 스위치가 없는 출력이 있습니다 — 실제 열림까지 확인되는 보조접점 구동기를 권합니다")
    if rep["summary"]["ack_min_avg"] and rep["summary"]["ack_min_avg"] > 30:
        out.append(f"위험 경보 확인까지 평균 {rep['summary']['ack_min_avg']}분 — 알림 받는 사람을 늘리는 것을 권합니다")
    seen, uniq = set(), []
    for a in out:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq[:6] or ["특이사항 없음 — 지금 상태를 유지하세요"]


def issue(storage, customer_id: int, period: str, by: str, now: float | None = None) -> dict | None:
    rep = build_monthly(storage, customer_id, period, now)
    if rep is None:
        return None
    storage.save_monthly(customer_id, period, by, rep)
    storage.log_event("", "", "monthly", f"{rep['customer']} {period} 월간 리포트 발행 ({by})", source="system")
    return rep


def generate_due(storage, now: float | None = None, send=None) -> int:
    """달이 바뀌었으면 고객사마다 지난달 리포트를 한 번만 발행. 알림 켠 고객사엔 문자 한 통."""
    now = time.time() if now is None else now
    period = prev_period(now)
    n = 0
    for c in storage.accounts.list_customers():
        if storage.has_monthly(c["id"], period):
            continue
        rep = issue(storage, c["id"], period, "자동 발행", now)
        n += 1
        if rep and c.get("monthly_notify"):
            nums = storage.accounts.receivers_for(c["id"])
            if nums:
                from .notify import send_sms
                text = f"[JCC] {c['name']} {int(period[5:])}월 판넬 리포트가 준비됐습니다. 대시보드 → 도구 → 월간 리포트"
                (send or send_sms)(text, nums)
    return n
