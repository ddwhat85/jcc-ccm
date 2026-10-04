"""주간 안전 요약 — 월요일 아침, 지난 7일을 문자 한 통으로("이번 주도 이상 없었습니다").

아무 일 없을 때도 JCC가 지키고 있다는 걸 느끼게 하는 장치. 실제 발송이라 비용이 들므로
고객사별 스위치(weekly_notify, 기본 꺼짐)를 켠 곳만, 그 고객사 알림 번호로만 보낸다.
숫자는 모두 실제 기록(경보·예지·자동 조치·감시 끊김)에서 만든다.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
SEND_HOUR = 9          # 월요일 이 시각(한국) 이후 첫 점검 때 보낸다

_SCHEMA = """CREATE TABLE IF NOT EXISTS weekly_sent (
    customer_id INTEGER NOT NULL, week TEXT NOT NULL, ts REAL, PRIMARY KEY (customer_id, week));"""


def _ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


def week_key(now: float) -> str:
    y, w, _ = datetime.fromtimestamp(now, KST).isocalendar()
    return f"{y}-W{w:02d}"


def _week_range(now: float):
    """지난 7일(지난주 월요일 0시 ~ 이번 주 월요일 0시, 한국 시간)."""
    d = datetime.fromtimestamp(now, KST)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return (monday - timedelta(days=7)).timestamp(), monday.timestamp()


def build(storage, customer_id: int, now: float | None = None, so_far: bool = False) -> dict | None:
    """고객사 한 곳의 지난주 요약(문구 포함). 판넬이 없으면 None.
    so_far=True: 운영자 미리 보기용 — 이번 주 월요일부터 지금까지(다음 월요일에 나갈 문자의 모양)."""
    from .monthly import _downtime
    now = time.time() if now is None else now
    cust = next((c for c in storage.accounts.list_customers() if c["id"] == customer_id), None)
    if cust is None:
        return None
    owner = storage.accounts.panel_owner_map()
    panels = {p for p, c in owner.items() if c == customer_id}
    rows = [d for d in storage.list_devices() if (d.get("panel") or d["device_id"]) in panels]
    devs = {d["device_id"] for d in rows}
    if not panels or not devs:
        return None
    start, end = _week_range(now)
    if so_far:
        start, end = end, now
    first = min((d.get("first_seen") or start) for d in rows)
    if first >= end:                        # 지난주엔 아직 설치 전
        return None
    since = max(start, first)               # 설치 전 시간은 가동률에 넣지 않는다
    keys = devs | panels
    base = storage.build_report(devices=keys, since=start, until=end)
    pr = base["summary"]["predict"]
    # 경보 건수는 고객 화면 사건 목록과 같은 기준 — 감시 장치 끊김(침묵)은 '위험 경보'가 아니라 가동률로 보인다
    frag, fa = storage._in_devices(keys)
    with storage._lock:
        rows = storage._conn.execute(f"SELECT severity FROM alarms WHERE raised_at >= ? AND raised_at < ? AND kind != 'silent' "
                                     f"AND {frag}", (since, end, *fa)).fetchall()
    al = {"crit": sum(1 for r in rows if r["severity"] == "crit"), "warn": sum(1 for r in rows if r["severity"] == "warn")}
    down = sum(_downtime(storage, devs, start, end).values())
    uptime = round(max(0.0, 1.0 - down / max(1.0, (end - since) * len(devs))) * 100, 1)
    if 99.95 <= uptime < 100:
        uptime = 99.9                       # 끊김이 있었는데 100%로 보이지 않게
    prec = pr["fire"]["detected"] + pr["contact"]["detected"] + pr["dew"]["detected"]
    acts = pr["fire"]["vent_auto"] + pr["fire"]["vent_edge"] + pr["dew"]["actuations"]
    a, b = datetime.fromtimestamp(start, KST), datetime.fromtimestamp(end - 1, KST)
    span = f"{a.month}/{a.day}~{b.month}/{b.day}" + (" 지금까지" if so_far else "")
    quiet = not (al["crit"] or al["warn"] or prec)
    head = f"[JCC GUARD] {cust['name']} 주간 안전 요약 ({span})"
    lines = [f"판넬 {len(panels)}면 — " + ("한 주 동안 이상 없었습니다." if quiet else "아래 일이 있었고 모두 기록했습니다.")]
    if not quiet:
        lines.append(f"위험 경보 {al['crit']}건 · 주의 경보 {al['warn']}건 · 미리 잡은 징조 {prec}건")
    if acts:
        lines.append(f"판넬이 스스로 한 조치 {acts}회")
    lines.append(f"감시 가동률 {uptime:g}%")
    url = os.environ.get("JCC_PUBLIC_URL", "").strip().rstrip("/")
    if url:
        lines.append(f"자세히 보기: {url}/")
    return {"customer": cust["name"], "week": week_key(now), "range": [start, end], "quiet": quiet,
            "crit": al["crit"], "warn": al["warn"], "precursors": prec, "actions": acts, "uptime": uptime,
            "text": head + "\n" + "\n".join(lines)}


def generate_due(storage, now: float | None = None, send=None) -> int:
    """월요일 오전(한국)이면, 켜 둔 고객사마다 이번 주 한 번만 보낸다. 보낸 수를 돌려준다."""
    now = time.time() if now is None else now
    d = datetime.fromtimestamp(now, KST)
    if d.weekday() != 0 or d.hour < SEND_HOUR:
        return 0
    _ensure(storage)
    wk, n = week_key(now), 0
    for c in storage.accounts.list_customers():
        if not c.get("weekly_notify"):
            continue
        with storage._lock:
            done = storage._conn.execute("SELECT 1 FROM weekly_sent WHERE customer_id=? AND week=?", (c["id"], wk)).fetchone()
        if done:
            continue
        nums = storage.accounts.receivers_for(c["id"], "report")
        rep = build(storage, c["id"], now) if nums else None
        if rep is None:                     # 번호·판넬이 없으면 이번 주는 넘어간 것으로(매시간 다시 보지 않게)
            _mark(storage, c["id"], wk, now)
            continue
        from .notify import send_sms
        res = send(rep["text"], nums) if send else send_sms(rep["text"], nums, title="JCC GUARD 주간 안전 요약")
        res = res if isinstance(res, dict) else {"ok": True}
        if res.get("skipped"):              # 문자 설정(알리고 키) 없음 — 보낸 척하지 않는다
            _mark(storage, c["id"], wk, now)
            storage.log_event("", "", "weekly", f"{c['name']} 주간 안전 요약 못 보냄 — 문자 설정 없음", source="system")
            continue
        if not res.get("ok"):               # 일시 오류면 표시하지 않고 다음 점검(1시간 뒤)에 다시 — 월요일이 지나면 그만
            storage.log_event("", "", "weekly", f"{c['name']} 주간 안전 요약 발송 실패 — 다시 시도", source="system")
            continue
        _mark(storage, c["id"], wk, now)
        storage.log_event("", "", "weekly", f"{c['name']} 주간 안전 요약 발송 ({len(nums)}명)", source="system")
        n += 1
    return n


def _mark(storage, cid: int, wk: str, now: float) -> None:
    with storage._lock:
        storage._conn.execute("INSERT OR IGNORE INTO weekly_sent (customer_id, week, ts) VALUES (?, ?, ?)", (cid, wk, now))
        storage._conn.commit()
