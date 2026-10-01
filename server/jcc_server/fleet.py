"""현장 목록 — 판넬마다 '지금 손봐야 하나'를 한 줄로. 직원 화면의 첫 화면.

판넬 수가 늘면 노드 그래프로는 어디가 문제인지 한눈에 안 보인다. 판넬별로 상태(위험·주의·끊김·정상),
경보 수, 예지 판정, 가장 가까운 남은 여유, 마지막 설치 점검을 모아 위험한 것부터 정렬해 돌려준다.
"""
from __future__ import annotations

import time

_RANK = {"crit": 0, "offline": 1, "warn": 2, "ok": 3}
_FIRE_BAD = ("danger", "critical")
_FIRE_WATCH = ("watch", "warning")


def fleet(storage, panels: set | None = None) -> list:
    """panels=None이면 전부(JCC 관리자), 아니면 그 판넬만(고객 범위)."""
    from .inspection import due_map
    from .rul import panel_view
    due = due_map(storage)
    owner = storage.accounts.panel_owner_map()
    names = {c["id"]: c["name"] for c in storage.accounts.list_customers()}
    pred = {r.get("panel"): r for r in storage.predict_state()}
    active = storage.list_active_alarms()
    life = {v["panel"]: v["items"] for v in panel_view(storage, panels)}
    cms: dict = {}
    for r in storage.list_commission_reports(panels, 1000):    # 최신순 — 판넬별 첫 번째가 마지막 점검
        cms.setdefault(r["panel"], r)
    now = time.time()
    out = []
    for p in storage.list_panels():
        pid = p["panel"]
        if panels is not None and pid not in panels:
            continue
        keys = {pid} | {c["device_id"] for c in p["ccms"]}
        al = [a for a in active if a.get("device_id") in keys]
        crit = sum(1 for a in al if a.get("severity") == "crit")
        unacked = sum(1 for a in al if not a.get("acked_at"))
        on = sum(1 for c in p["ccms"] if c["online"])
        r = pred.get(pid) or {}
        f, c, d = r.get("fire") or {}, r.get("contact") or {}, r.get("dew") or {}
        near = next((i for i in life.get(pid, []) if i["status"] in ("reached", "ok")), None)
        why = []
        if crit:
            why.append(f"위험 경보 {crit}")
        if f.get("stage") in _FIRE_BAD:
            why.append(f"화재 징조(FRI {f.get('fri')})")
        if c.get("stage") == "danger":
            why.append("접점 발열 위험")
        if why:
            status = "crit"
        elif on == 0:
            status, why = "offline", ["CCM 연결 끊김"]
        else:
            if len(al) - crit:
                why.append(f"주의 경보 {len(al) - crit}")
            if on < len(p["ccms"]):
                why.append(f"CCM {len(p['ccms']) - on}대 끊김")
            if f.get("stage") in _FIRE_WATCH:
                why.append("화재 지켜보는 중")
            if c.get("stage") in ("watch", "warning"):
                why.append("접점 발열 지켜보는 중")
            if d.get("stage") == "danger":
                why.append("결로 위험")
            if near and (near["status"] == "reached" or (near["days"] is not None and near["days"] <= 14)):
                why.append("남은 여유 2주 이내")
            status = "warn" if why else "ok"
        if due.get(pid) and due[pid] < now:              # 점검 기한은 어떤 상태든 보이게(정상이면 주의로)
            why.append(f"정기 점검 기한 {int((now - due[pid]) // 86400)}일 지남")
            if status == "ok":
                status = "warn"
        cm = cms.get(pid)
        cid = owner.get(pid)
        out.append({
            "panel": pid, "panel_name": p["panel_name"], "site": p.get("site") or "",
            "customer_id": cid, "customer": names.get(cid, "") if cid is not None else "",
            "status": status, "why": why[:4],
            "ccm_online": on, "ccm_total": len(p["ccms"]),
            "last_seen": max((x.get("last_seen") or 0) for x in p["ccms"]) if p["ccms"] else None,
            "alarms": {"crit": crit, "warn": len(al) - crit, "unacked": unacked},
            "predict": {"fire": f.get("stage"), "fri": f.get("fri"), "contact": c.get("stage"), "dew": d.get("stage"),
                        "vent_open": bool((f.get("vent") or {}).get("open"))},
            "life": {"label": near["label"], "say": near["say"], "days": near["days"], "status": near["status"]} if near else None,
            "commission": {"overall": cm["overall"], "ts": cm["ts"]} if cm else None,
            "next_inspection": due.get(pid),
            "sensors": sum(len(x.get("latest") or []) for x in p["ccms"]),
            "age": None if not p["ccms"] else round(now - max((x.get("last_seen") or 0) for x in p["ccms"])),
        })
    out.sort(key=lambda x: (_RANK[x["status"]], -x["alarms"]["crit"], -x["alarms"]["unacked"], x["panel_name"]))
    return out
