"""안전 관리 확인서 — 분기·연간, JCC 직원이 검토해 발행한다(보험·감사·윗선 제출용).

무엇을 확인하나: '그 기간 동안 이 판넬들을 JCC GUARD로 감시했고, 기록은 이랬다'. 숫자는 전부 실제 기록에서
(월간 리포트와 같은 집계). 법정 안전 점검이나 보험 인수 기준의 적합 판정을 대신하지 않는다 — 문서에 그대로 적는다.

기간이 끝난 뒤에만 발행한다(진행 중인 분기를 확인서로 내면 숫자가 계속 바뀐다). 발행된 문서는 스냅숏 —
같은 기간을 다시 발행하면 개정 번호가 오른다(무엇을 고쳤는지 직원이 설명할 수 있게).
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from .monthly import KST, build_range

_P = re.compile(r"^(20[2-9][0-9])(?:-Q([1-4]))?$")


def valid_period(p) -> bool:
    """'YYYY-Qn'(분기) 또는 'YYYY'(연간)."""
    return bool(_P.match(str(p or "")))


def bounds(period: str) -> tuple:
    m = _P.match(period)
    y, q = int(m.group(1)), m.group(2)
    if q:
        m0 = 3 * (int(q) - 1) + 1
        start = datetime(y, m0, 1, tzinfo=KST)
        end = datetime(y + (m0 + 3 > 12), (m0 + 3 - 1) % 12 + 1, 1, tzinfo=KST)
    else:
        start, end = datetime(y, 1, 1, tzinfo=KST), datetime(y + 1, 1, 1, tzinfo=KST)
    return start.timestamp(), end.timestamp()


def label(period: str) -> str:
    m = _P.match(period)
    return f"{m.group(1)}년 {m.group(2)}분기" if m.group(2) else f"{m.group(1)}년 연간"


def _crit_list(storage, keys: set, start: float, end: float) -> list:
    """그 기간 위험 경보 — 언제·어디서·무엇을, 판넬이 스스로 한 조치까지 몇 초, 사람이 확인하기까지 몇 분."""
    from .guard import _SELF, _events, _latest, _panel_of, _plain
    if not keys:
        return []
    frag, fa = storage._in_devices(keys)
    with storage._lock:
        rows = storage._conn.execute(
            f"SELECT device_id, sensor_key, kind, detail, raised_at, acked_at, cause FROM alarms WHERE raised_at >= ? "
            f"AND raised_at < ? AND severity='crit' AND kind != 'silent' AND {frag} ORDER BY raised_at", (start, end, *fa)).fetchall()
    lat = _latest(storage)
    names = {p["panel"]: p["panel_name"] for p in storage.list_panels()}
    out = []
    for r in rows[:200]:
        pid = _panel_of(lat, r["device_id"])
        pkeys = (lat.get(pid) or {}).get("keys", {r["device_id"]})
        self_ts = next((e["ts"] for e in _events(storage, pkeys, r["raised_at"] - 30, r["raised_at"] + 900, limit=50)
                        if e["etype"] in _SELF), None)
        out.append({"at": r["raised_at"], "panel_name": names.get(pid, pid),
                    "sensor_name": (lat.get(pid) or {}).get("names", {}).get((r["device_id"], r["sensor_key"] or ""), r["sensor_key"] or ""),
                    "detail": _plain(r["detail"] or r["kind"]),
                    "self_sec": None if self_ts is None else max(0, round(self_ts - r["raised_at"])),
                    "ack_min": None if not r["acked_at"] else round((r["acked_at"] - r["raised_at"]) / 60.0, 1),
                    "cause": r["cause"] or ""})
    return out


def build(storage, customer_id: int, period: str, now: float | None = None) -> dict | None:
    """확인서 내용(스냅숏). 고객사가 없으면 None. 기간이 안 끝났으면 ValueError."""
    from .inspection import history as insp_history
    now = time.time() if now is None else now
    start, end = bounds(period)
    if end > now:
        raise ValueError("기간이 끝난 뒤에 발행할 수 있습니다")
    rep = build_range(storage, customer_id, period, start, end, now)
    if rep is None:
        return None
    owner = storage.accounts.panel_owner_map()
    panels = {p for p, c in owner.items() if c == customer_id}
    devs = [d for d in storage.list_devices() if (d.get("panel") or d["device_id"]) in panels]
    keys = {d["device_id"] for d in devs} | panels
    first = min((d.get("first_seen") or start for d in devs), default=None)
    rep.update({
        "kind": "certificate", "label": label(period),
        "watch_from": None if first is None else max(start, first),      # 기간 중간에 설치했으면 그날부터
        "crit": _crit_list(storage, keys, start, end),
        "inspections": [{"at": x["done_at"], "panel_name": x["panel_name"] or x["panel"], "overall": x["overall"],
                         "by": x["by"], "signer": x["signer"]}
                        for x in insp_history(storage, panels, include_drafts=False, limit=500)
                        if x["done_at"] and start <= x["done_at"] < end],
        "note": "이 확인서는 JCC GUARD가 기간 동안 남긴 감시 기록의 요약입니다. 법정 안전 점검이나 보험 인수 기준의 "
                "적합 판정을 대신하지 않습니다.",
    })
    return rep


def issue(storage, customer_id: int, period: str, by: str, now: float | None = None) -> dict | None:
    rep = build(storage, customer_id, period, now)
    if rep is None:
        return None
    rev = storage.save_certificate(customer_id, period, by, rep)
    storage.log_event("", "", "certificate", f"{rep['customer']} {rep['label']} 안전 관리 확인서 발행 ({by}, 개정 {rev})",
                      source="system")
    return storage.get_certificate(customer_id, period)
