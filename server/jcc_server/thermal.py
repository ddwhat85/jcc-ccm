"""판넬 온도가 왜 오르는지와 무엇을 하면 되는지 — 기록(시간별 평균)만으로, 근거 숫자와 함께.

원인은 잰 만큼만 말한다. 인버터 부하·바깥 온도는 아직 재지 않으므로 '그 때문'이라고 쓰지 않고, 기록의 모양으로 가른다:
  - 평일 가동 시간에만 오르고 주말엔 덜 오른다      → 판넬 안 기기 발열(가동 부하) 영향
  - 주말에도 낮에 오르거나 밤 온도까지 높아진다      → 주변·바깥 온도 영향
  - 몇 주째 최고 온도만 오르고 밤 온도는 그대로     → 냉각 성능 저하(필터 막힘·공조 노후) 의심
조치는 제안만 한다(설정 변경·교체는 사람이). 공조 설정온도를 낮추자고 할 때는 판넬 안 이슬점 + 3℃ 아래로는 권하지 않는다
(그보다 차가우면 판넬 안에 결로 — 누전 위험).

고객이 '냉각 점검 요청'을 누르면 service_requests에 남고, 운영자 화면 '공조 점검 후보'에 함께 보인다(영업 기회).
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from .dewpoint import dew_point

KST = timezone(timedelta(hours=9))
H, D = 3600, 86400
MIN_DAYS = 14          # 평일·주말을 두 번씩은 봐야 가른다
WINDOW = 28            # 지난 4주로 본다
LOAD_DIFF = 1.0        # 평일 낮 상승이 주말보다 이만큼(℃) 크면 '가동 부하'
AMBIENT_RISE = 1.5     # 주말 낮에도 이만큼 오르면 '주변 온도'
DEGRADE_PER_WEEK = 0.3 # 최고 온도가 밤 온도보다 주마다 이만큼 더 오르면 '냉각 저하 의심'
DEW_MARGIN = 3.0       # 공조 설정 하한 = 이슬점 + 이만큼

_SCHEMA = """CREATE TABLE IF NOT EXISTS service_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, panel TEXT NOT NULL, customer_id INTEGER, username TEXT,
    kind TEXT NOT NULL, note TEXT, created_at REAL, status TEXT NOT NULL DEFAULT 'open', done_at REAL, done_by TEXT);"""
KINDS = {"cooling_review": "냉각 점검 요청"}


def _kst(t):
    return datetime.fromtimestamp(t, KST)


def _mean(xs):
    return statistics.mean(xs) if xs else None


def _hours_label(hs):
    return f"{min(hs)}~{max(hs) + 1}시" if hs else ""


def analyze(temp: dict, hum: dict, now: float, warn=None) -> dict:
    """temp·hum = {정시 타임스탬프: 시간 평균}. 화면·운영자 목록이 같이 쓰는 순수 계산."""
    out = {"building": True, "level": "ok", "causes": [], "actions": [], "warn": warn}
    if not temp:
        out["days"] = 0
        return out
    since = now - WINDOW * D
    pts = {t: v for t, v in temp.items() if t >= since}
    days = (now - min(temp)) / D
    out["days"] = round(days, 1)
    if days < MIN_DAYS or not pts:
        return out
    out["building"] = False
    wk = {h: [] for h in range(24)}
    we = {h: [] for h in range(24)}
    for t, v in pts.items():
        k = _kst(t)
        (wk if k.weekday() < 5 else we)[k.hour].append(v)
    pwk = {h: _mean(v) for h, v in wk.items() if v}
    pwe = {h: _mean(v) for h, v in we.items() if v}
    night = lambda p: _mean([p[h] for h in range(0, 6) if h in p])          # noqa: E731
    nwk, nwe = night(pwk), night(pwe)
    day_h = [h for h in range(8, 20)]
    rise_wk = max((pwk[h] - nwk for h in day_h if h in pwk), default=0) if nwk is not None else 0
    rise_we = max((pwe[h] - nwe for h in day_h if h in pwe), default=0) if nwe is not None else 0
    peak_h = max((h for h in day_h if h in pwk), key=lambda h: pwk[h], default=None)
    hot = sorted(h for h in day_h if h in pwk and nwk is not None and pwk[h] - nwk >= 0.5 * rise_wk) if rise_wk > 0.5 else []
    out.update(weekday_rise=round(rise_wk, 1), weekend_rise=round(rise_we, 1), hot_hours=_hours_label(hot),
               peak_hour=peak_h, weekday_peak=round(pwk[peak_h], 1) if peak_h is not None else None,
               night=round(nwk, 1) if nwk is not None else None)

    # 지난 14일 중 주의 기준을 넘은 날
    daily = {}
    for t, v in pts.items():
        if t >= now - 14 * D:
            dk = _kst(t).date()
            daily[dk] = max(daily.get(dk, v), v)
    over_days = sum(1 for v in daily.values() if warn is not None and v >= warn)
    out["over_days"] = over_days
    out["max14"] = round(max(daily.values()), 1) if daily else None

    # 주별 추세: 평일 하루 최고 vs 밤(0~5시) — 둘 다 오르면 계절, 최고만 오르면 냉각 저하
    weeks = []
    for w in range(WINDOW // 7):
        a, b = now - (w + 1) * 7 * D, now - w * 7 * D
        seg = [(t, v) for t, v in pts.items() if a <= t < b]
        mx = {}
        for t, v in seg:
            k = _kst(t)
            if k.weekday() < 5:
                mx[k.date()] = max(mx.get(k.date(), v), v)
        nt = [v for t, v in seg if _kst(t).hour < 6]
        if mx and nt:
            weeks.append((-w, _mean(list(mx.values())), _mean(nt)))
    slope_pk = slope_nt = 0.0
    if len(weeks) >= 3:
        xs = [w[0] for w in weeks]
        mx_ = _mean(xs)
        den = sum((x - mx_) ** 2 for x in xs) or 1
        slope_pk = sum((x - mx_) * (w[1] - _mean([z[1] for z in weeks])) for x, w in zip(xs, weeks)) / den
        slope_nt = sum((x - mx_) * (w[2] - _mean([z[2] for z in weeks])) for x, w in zip(xs, weeks)) / den
    out.update(peak_trend=round(slope_pk, 2), night_trend=round(slope_nt, 2), weeks=len(weeks))

    # 원인(근거 숫자와 함께)
    if rise_wk - rise_we >= LOAD_DIFF:
        out["causes"].append({"kind": "load", "title": "평일 가동 시간의 판넬 안 기기 발열",
                              "why": f"평일 {_hours_label(hot) or '낮'}에 밤보다 평균 {rise_wk:.1f}℃ 오르고, 주말 같은 시간엔 {rise_we:.1f}℃만 오릅니다."
                                     " 인버터·드라이브처럼 가동할 때 열이 나는 기기의 영향일 가능성이 큽니다."})
    if rise_we >= AMBIENT_RISE or slope_nt >= DEGRADE_PER_WEEK:
        why = f"기계를 덜 돌리는 주말에도 낮에 {rise_we:.1f}℃ 오릅니다." if rise_we >= AMBIENT_RISE else ""
        if slope_nt >= DEGRADE_PER_WEEK:
            why += f" 밤 온도도 주마다 {slope_nt:.1f}℃씩 오르고 있습니다."
        out["causes"].append({"kind": "ambient", "title": "판넬 주변·바깥 온도", "why": why.strip() + " 주변 온도(햇빛·계절·근처 열원)의 영향일 가능성이 큽니다."})
    degrade = len(weeks) >= 3 and slope_pk - slope_nt >= DEGRADE_PER_WEEK
    if degrade:
        out["causes"].append({"kind": "degrade", "title": "냉각 성능 저하 의심",
                              "why": f"최근 {len(weeks)}주 동안 평일 최고 온도가 주마다 {slope_pk:.1f}℃씩 오르는데 밤 온도는 그만큼 오르지 않습니다."
                                     " 필터 막힘이나 공조(에어컨·팬) 성능 저하일 수 있습니다."})

    # 수준
    near = warn is not None and out["weekday_peak"] is not None and out["weekday_peak"] >= warn - 3
    if over_days >= 2 or degrade:
        out["level"] = "act"
    elif over_days == 1 or near:
        out["level"] = "watch"

    # 공조 설정 하한(이슬점) — 지난 14일 판넬 안 이슬점의 높은 쪽(95%) + 3℃
    dps = []
    for t, rh in hum.items():
        if t >= now - 14 * D and t in temp:
            dp = dew_point(temp[t], rh)
            if dp is not None:
                dps.append(dp)
    if dps:
        dps.sort()
        dp95 = dps[min(len(dps) - 1, int(len(dps) * 0.95))]
        out["dew_point"] = round(dp95, 1)
        out["setpoint_floor"] = int(-(-(dp95 + DEW_MARGIN) // 1))          # 올림

    # 할 일(우선순위대로) — 제안만. 누가 하는지 밝힌다
    acts = out["actions"]
    kinds = {c["kind"] for c in out["causes"]}
    if out["level"] != "ok":
        acts.append({"who": "현장", "title": "필터·환기구 청소, 판넬 문 닫힘 확인",
                     "why": "열이 빠져나가는 길이 막히거나 문이 열려 있으면 가장 먼저 온도가 오릅니다."})
        fl = out.get("setpoint_floor")
        acts.append({"who": "JCC 확인 후", "title": "공조(에어컨)가 달려 있다면 설정온도 점검",
                     "why": ("출고 설정 그대로라면 낮출 여지가 있습니다. "
                             + (f"다만 {fl}℃ 아래로는 내리지 마세요 — 판넬 안 이슬점이 {out['dew_point']}℃라 더 차가우면 결로(누전 위험)가 생깁니다."
                                if fl is not None else "습도 기록이 없어 결로 하한은 JCC가 현장에서 확인합니다."))})
    if "ambient" in kinds and out["level"] != "ok":
        acts.append({"who": "현장", "title": "판넬 위치 점검 — 직사광선·근처 열원",
                     "why": "주변 온도 영향이 클 때는 차광이나 열원과의 거리만으로도 몇 도가 내려갑니다."})
    if degrade:
        acts.append({"who": "JCC 방문", "title": "냉각 성능 점검", "why": "필터·응축기 청소나 공조 점검으로 원래 성능을 되찾을 수 있습니다."})
    if out["level"] == "act":
        acts.append({"who": "JCC", "title": "냉각 용량 검토", "request": "cooling_review",
                     "why": "판넬 안 발열량을 계산해 지금 공조로 충분한지 보고, 모자라면 맞는 용량을 제안합니다."})
    return out


# ── 저장소 ────────────────────────────────────────────────
def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


def _humidity_key(storage, dev):
    with storage._lock:
        r = storage._conn.execute("SELECT sensor_key FROM hourly WHERE device_id=? AND (sensor_key LIKE '%humid%') "
                                  "GROUP BY sensor_key ORDER BY COUNT(*) DESC LIMIT 1", (dev,)).fetchone()
    return r["sensor_key"] if r else None


def panel_thermal(storage, panel: str, now: float | None = None) -> dict | None:
    from .forecast import ensure as f_ensure, panel_forecast_source, series
    now = time.time() if now is None else now
    src = panel_forecast_source(storage, panel)
    if src is None:
        return None
    dev, key, name, warn = src
    f_ensure(storage)
    temp = series(storage, dev, key, now - (WINDOW + 1) * D)
    first = None
    with storage._lock:
        r = storage._conn.execute("SELECT MIN(hour) AS h FROM hourly WHERE device_id=? AND sensor_key=?", (dev, key)).fetchone()
        first = r["h"] if r else None
    hk = _humidity_key(storage, dev)
    hum = series(storage, dev, hk, now - 15 * D) if hk else {}
    out = analyze(temp, hum, now, warn)
    if first is not None:                       # 화면에 보일 쌓인 날수는 4주 창이 아니라 처음부터
        out["days"] = round((now - first) / D, 1)
    out.update(panel=panel, sensor_name=name, open_request=open_request(storage, panel))
    _with_cooling(storage, panel, out)
    _with_aircon(storage, panel, out)
    return out


def _with_aircon(storage, panel: str, out: dict) -> None:
    """통신으로 연결된 에어컨이 있으면 실측 가동률로 원인을 굳힌다(읽기만 — 설정은 사람이)."""
    from .aircon import panel_status
    units = panel_status(storage, panel)
    out["aircons"] = [{k: u.get(k) for k in ("name", "state", "running", "alarm", "setpoint", "temp", "duty_hot", "duty_24h")} for u in units]
    if not units or out.get("building"):
        return
    if any(u.get("alarm") for u in units):
        out["actions"].insert(0, {"who": "JCC", "title": "에어컨 알람 확인", "why": "에어컨이 스스로 알람을 내고 있습니다 — 필터·응축기·냉매·팬 상태를 JCC가 확인합니다."})
    duties = [u["duty_hot"] for u in units if u.get("duty_hot") is not None]
    if not duties or out["level"] != "act":
        return
    d = sum(duties) / len(duties)
    if d >= 0.9:
        out["causes"].insert(0, {"kind": "measured_short", "title": "냉각 용량 부족 — 실측으로 확인",
                                 "why": f"평일 더운 시간(12~18시)에 에어컨이 {round(d * 100)}% 내내 돌았는데도 주의 기준을 넘었습니다. "
                                        "지금 공조로는 열을 다 빼내지 못합니다."})
        for a in out["actions"]:
            if a.get("request") == "cooling_review":
                a["why"] = f"에어컨 가동률 {round(d * 100)}%(더운 시간) — 실측으로도 모자랍니다. " + a["why"]
    elif d < 0.6:
        out["causes"].insert(0, {"kind": "underused", "title": "에어컨이 덜 돌고 있음",
                                 "why": f"더운 시간에도 에어컨이 {round(d * 100)}%만 돌았는데 판넬은 기준을 넘었습니다. "
                                        "설정 온도가 높거나, 에어컨이 판넬 안 온도를 다른 자리에서 재고 있을 수 있습니다."})


def _with_cooling(storage, panel: str, out: dict) -> None:
    """등록된 판넬 설비가 있으면 냉각 용량 계산을 붙이고, '냉각 용량 검토' 할 일의 근거를 계산으로 바꾼다."""
    from .equipment import get as eq_get, view as eq_view
    v = eq_view(eq_get(storage, panel))
    out["cooling"] = None if v is None else {"status": v["calc"]["status"], "advice": v["advice"], "summary": v["summary"]}
    if v is None or out.get("building"):
        return
    st = v["calc"]["status"]
    for a in out["actions"]:
        if a.get("request") == "cooling_review":
            a["why"] = ("계산으로도 모자랍니다 — " if st == "short" else "") + v["advice"]
    if st in ("ok", "passive") and out["level"] == "act":
        # 용량은 맞는데 기준을 넘는다 → 용량보다 필터·설정·고장 쪽이 먼저
        out["causes"].append({"kind": "cooling_ok", "title": "공조 용량은 계산상 충분",
                              "why": v["advice"] + " 그런데도 기준을 넘으니 필터 막힘·설정온도·공조 고장 쪽을 먼저 보는 편이 맞습니다."})


def open_request(storage, panel: str):
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT id, kind, created_at, username FROM service_requests WHERE panel=? AND status='open' "
                                  "ORDER BY id DESC LIMIT 1", (panel,)).fetchone()
    return dict(r) if r else None


def request(storage, panel: str, kind: str, username: str, customer_id, note: str = "") -> dict:
    """고객의 점검 요청. 같은 판넬에 열린 요청이 있으면 새로 만들지 않는다."""
    if kind not in KINDS:
        raise ValueError("요청 종류를 확인하세요")
    ensure(storage)
    have = open_request(storage, panel)
    if have:
        return dict(have, existing=True)
    with storage._lock:
        cur = storage._conn.execute("INSERT INTO service_requests (panel, customer_id, username, kind, note, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?)", (panel, customer_id, username, kind, str(note or "")[:300], time.time()))
        storage._conn.commit()
        rid = cur.lastrowid
    storage.log_event(panel, "", "service_request", f"{KINDS[kind]} — {storage.panel_label(panel) or panel} ({username})", source="user")
    return {"id": rid, "kind": kind, "existing": False}


def close_request(storage, rid: int, by: str) -> bool:
    ensure(storage)
    with storage._lock:
        n = storage._conn.execute("UPDATE service_requests SET status='done', done_at=?, done_by=? WHERE id=? AND status='open'",
                                  (time.time(), by, rid)).rowcount
        storage._conn.commit()
    return bool(n)


def candidates(storage, now: float | None = None) -> list:
    """운영자 화면 '공조 점검 후보': 조치 필요·지켜볼 판넬과 열린 요청, 급한 순."""
    now = time.time() if now is None else now
    owner = storage.accounts.panel_owner_map()
    custs = {c["id"]: c["name"] for c in storage.accounts.list_customers()}
    out = []
    for p in storage.list_panels():
        t = panel_thermal(storage, p["panel"], now)
        if t is None or (t["level"] == "ok" and not t.get("open_request")):
            continue
        cid = owner.get(p["panel"])
        out.append({"panel": p["panel"], "panel_name": p["panel_name"], "customer": custs.get(cid, "배정 없음"),
                    "cooling": (t.get("cooling") or {}).get("status", "unregistered"),
                    "level": t["level"], "causes": [c["title"] for c in t["causes"]], "over_days": t.get("over_days", 0),
                    "max14": t.get("max14"), "warn": t.get("warn"), "weekday_peak": t.get("weekday_peak"),
                    "hot_hours": t.get("hot_hours", ""), "request": t.get("open_request")})
    rank = {"act": 0, "watch": 1, "ok": 2}
    out.sort(key=lambda x: (x["request"] is None, rank.get(x["level"], 3), -(x["over_days"] or 0)))
    return out
