"""정기 점검 일지 — JCC 직원이 고객 요청·계약으로 현장을 점검한 기록.

흐름
  1. 시작: 판넬을 고르면 '이번에 볼 곳'을 데이터에서 뽑는다 — 지난 점검 이후 경보(오경보 표시 포함)·
     남은 여유·센서 이상(드리프트·고착·침묵)·출력 작동 실패·가스 센서 기능시험 필요 등
  2. 현장: 체크리스트(정상·조치함·조치 필요·해당 없음 + 메모), 사진, 특이사항·조치
  3. 완료: 고객 확인자 이름·서명 → 보고서 스냅숏(이후 바뀌지 않음), 다음 점검 예정일
점검 중 기록은 언제든 저장(초안)된다. 고객 계정은 완료된 보고서만 본다.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
RESULTS = ("ok", "fix", "bad", "na")          # 정상 · 조치함 · 조치 필요 · 해당 없음
INTERVALS = (30, 60, 90, 180, 365)
MAX_PHOTOS = 20
MAX_PHOTO_BYTES = 1_500_000                    # 화면에서 1280px JPEG로 줄여 올린다
MAX_SIGN_BYTES = 200_000
_GAS = ("h2", "voc", "co")

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, panel TEXT, status TEXT, created_at REAL, updated_at REAL,
    done_at REAL, by TEXT, data TEXT, report TEXT, next_due REAL);
CREATE INDEX IF NOT EXISTS idx_insp_panel ON inspections (panel, created_at);
CREATE TABLE IF NOT EXISTS inspection_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, inspection_id INTEGER, ts REAL, caption TEXT, mime TEXT, data BLOB, by TEXT);
"""

# 체크리스트(고정 서식 — 보고서끼리 비교되게). need: 그 판넬에 해당 센서·출력이 있어야 의미 있는 항목
CHECKLIST = [
    ("외관·청결", [
        ("clean", "판넬 내부 먼지·이물 청소", None),
        ("filter", "환기 팬 필터 상태(막힘·오염)", None),
        ("door", "도어 패킹·잠금·접지선", None),
    ]),
    ("전기", [
        ("terminal", "주요 단자 조임 상태(변색·풀림)", None),
        ("breaker", "차단기 외관·발열 흔적", None),
        ("current", "부하 전류가 화면 값과 맞는지(클램프 측정)", "current"),
    ]),
    ("센서", [
        ("gas_test", "가스 센서 기능시험(표준가스 또는 시험 버튼)", "gas"),
        ("temp_cmp", "온습도 센서 — 기준계와 비교", None),
        ("wiring", "센서 배선·고정·커넥터", None),
    ]),
    ("출력", [
        ("vent", "벤트 열림·닫힘 작동 시험", "vent"),
        ("heater_fan", "히터·팬 작동 시험", "dew"),
    ]),
    ("통신·전원", [
        ("ccm", "CCM 상태등·통신(화면에 실시간 값)", None),
        ("power", "CCM 전원·배선·방수", None),
    ]),
]


def ensure(storage) -> None:
    if getattr(storage, "_insp_ready", False):
        return
    with storage._lock:
        storage._conn.executescript(SCHEMA)
        storage._conn.commit()
    storage._insp_ready = True


def _kdate(ts) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d") if ts else ""


def _row(r) -> dict:
    d = dict(r)
    d["data"] = json.loads(d.get("data") or "{}")
    d["report"] = json.loads(d["report"]) if d.get("report") else None
    return d


def _panel(storage, panel: str):
    return next((p for p in storage.list_panels() if p["panel"] == panel), None)


def checklist_for(p) -> list:
    """이 판넬에 맞춘 체크리스트. 해당 센서·출력이 없으면 '해당 없음'이 기본."""
    kinds = {s.get("kind") for c in p["ccms"] for s in c.get("latest") or []}
    keys = {s.get("sensor_key", "") for c in p["ccms"] for s in c.get("latest") or []}
    has = {"current": "current" in kinds or any("current" in k for k in keys),
           "gas": bool(kinds & set(_GAS)) or any(k.startswith(("h2", "voc", "co_")) for k in keys),
           "vent": True, "dew": "humidity" in kinds or any("humid" in k for k in keys)}
    out = []
    for group, items in CHECKLIST:
        out.append({"group": group, "items": [{"key": k, "label": lb, "applies": need is None or has.get(need, True)}
                                              for k, lb, need in items]})
    return out


def last_done(storage, panel: str):
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT * FROM inspections WHERE panel=? AND status='done' ORDER BY done_at DESC LIMIT 1",
                                  (panel,)).fetchone()
    return _row(r) if r else None


def focus(storage, panel: str, now: float | None = None) -> dict:
    """'이번 점검 때 볼 곳' — 지난 점검(없으면 90일) 이후 데이터에서. 중요한 것부터."""
    from .rul import panel_view
    now = time.time() if now is None else now
    p = _panel(storage, panel)
    if p is None:
        return {"items": [], "since": None}
    prev = last_done(storage, panel)
    since = prev["done_at"] if prev else now - 90 * 86400
    keys = {panel} | {c["device_id"] for c in p["ccms"]}
    items = []
    al = storage.alarms_since(since, keys, 500)
    real = [a for a in al if a.get("cause") not in ("false", "work")]
    falsey = len(al) - len(real)
    by: dict = {}
    for a in real:
        k = (a["device_id"], a.get("sensor_key") or "", a["kind"])
        by.setdefault(k, []).append(a)
    names = {(c["device_id"], s["sensor_key"]): s.get("name") or s["sensor_key"] for c in p["ccms"] for s in c.get("latest") or []}
    for (dev, key, kind), lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        crit = any(a.get("severity") == "crit" for a in lst)
        nm = names.get((dev, key)) or (f"CCM {dev}" if not key else key)
        if kind == "actuator_fault":
            items.append({"prio": 0, "text": f"출력 작동 실패 {len(lst)}회 — 구동기·배선·전원 확인", "check": "vent"})
        elif kind == "contact":
            items.append({"prio": 0, "text": f"접점 발열 위험 {len(lst)}회 — 단자 조임·변색 확인", "check": "terminal"})
        elif kind == "fire":
            items.append({"prio": 0, "text": f"화재 징조 {len(lst)}회 — 발생원(배터리·접점) 확인, 가스 센서 시험", "check": "gas_test"})
        elif kind in ("stuck", "drift"):
            items.append({"prio": 1, "text": f"{nm}: {'값 멈춤' if kind == 'stuck' else '값이 한쪽으로 이동'} {len(lst)}회 — 센서 점검·교정",
                          "check": "gas_test" if any(g in key for g in ("h2", "voc", "co")) else "wiring"})
        elif kind == "silent":
            items.append({"prio": 1, "text": f"{nm}: 데이터 끊김 {len(lst)}회 — 배선·전원·통신 확인", "check": "wiring" if key else "ccm"})
        else:
            items.append({"prio": 0 if crit else 2, "text": f"{nm}: {'위험' if crit else '주의'} 경보 {len(lst)}회 — 원인 확인",
                          "check": None})
    for pv in panel_view(storage, {panel}):
        for i in pv["items"]:
            if i["status"] == "reached" or (i["status"] == "ok" and i["days"] is not None and i["days"] <= 60):
                items.append({"prio": 0 if i["status"] == "reached" or i["days"] <= 14 else 1,
                              "text": f"{i['label']}: {i['say']} — {i['advice']}",
                              "check": "terminal" if i["kind"] == "contact" else "gas_test" if i["kind"] == "gas" else None})
    kinds = {s.get("kind") for c in p["ccms"] for s in c.get("latest") or []}
    if kinds & set(_GAS):
        days = (now - prev["done_at"]) / 86400 if prev else None
        if days is None or days >= 80:
            items.append({"prio": 2, "text": "가스 센서 정기 기능시험(제조사 권장 — 표준가스 또는 시험 버튼)", "check": "gas_test"})
    if falsey:
        items.append({"prio": 3, "text": f"오경보·시험 작업으로 기록된 경보 {falsey}건 — 반복되면 경보 기준 조정 검토", "check": None})
    items.sort(key=lambda x: x["prio"])
    seen, uniq = set(), []
    for it in items:
        if it["text"] not in seen:
            seen.add(it["text"])
            uniq.append(it)
    return {"items": uniq[:12], "since": since, "since_label": _kdate(since) + (" (지난 점검)" if prev else " (최근 90일)"),
            "alarms": len(al), "false_alarms": falsey}


def _get(storage, iid: int):
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT * FROM inspections WHERE id=?", (iid,)).fetchone()
    return _row(r) if r else None


def photos(storage, iid: int) -> list:
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT id, ts, caption, by FROM inspection_photos WHERE inspection_id=? ORDER BY id",
                                     (iid,)).fetchall()
    return [dict(r) for r in rows]


def view(storage, iid: int):
    d = _get(storage, iid)
    if d:
        d["photos"] = photos(storage, iid)
    return d


def draft_for(storage, panel: str):
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT id FROM inspections WHERE panel=? AND status='draft' ORDER BY id DESC LIMIT 1",
                                  (panel,)).fetchone()
    return view(storage, r["id"]) if r else None


def start(storage, panel: str, by: str) -> dict:
    """초안이 있으면 그것을, 없으면 새로 만든다."""
    ensure(storage)
    p = _panel(storage, panel)
    if p is None:
        raise ValueError("없는 판넬입니다")
    d = draft_for(storage, panel)
    if d:
        return d
    now = time.time()
    data = {"checklist": checklist_for(p), "results": {}, "notes": {}, "summary": "", "interval": 90}
    with storage._lock:
        cur = storage._conn.execute("INSERT INTO inspections (panel, status, created_at, updated_at, by, data) "
                                    "VALUES (?, 'draft', ?, ?, ?, ?)", (panel, now, now, by, json.dumps(data, ensure_ascii=False)))
        storage._conn.commit()
        iid = cur.lastrowid
    storage.log_event(panel, "", "inspection", f"정기 점검 시작: {p['panel_name']} ({by})", source="user")
    return view(storage, iid)


def save(storage, iid: int, patch: dict) -> dict:
    """결과·메모·특이사항·주기를 덮어 저장(초안만)."""
    d = _get(storage, iid)
    if d is None:
        raise ValueError("없는 점검입니다")
    if d["status"] != "draft":
        raise ValueError("완료된 점검은 고칠 수 없습니다")
    data = d["data"]
    valid = {it["key"] for g in data["checklist"] for it in g["items"]}
    for f in ("results", "notes"):
        if patch.get(f) is not None and not isinstance(patch.get(f), dict):
            raise ValueError("잘못된 요청 형식입니다")
    for k, v in (patch.get("results") or {}).items():
        if k in valid and (v in RESULTS or v is None):
            if v is None:
                data["results"].pop(k, None)
            else:
                data["results"][k] = v
    for k, v in (patch.get("notes") or {}).items():
        if k in valid:
            data["notes"][k] = str(v or "")[:300]
    if "summary" in patch:
        data["summary"] = str(patch.get("summary") or "")[:3000]
    if patch.get("interval") in INTERVALS:
        data["interval"] = patch["interval"]
    with storage._lock:
        storage._conn.execute("UPDATE inspections SET data=?, updated_at=? WHERE id=?",
                              (json.dumps(data, ensure_ascii=False), time.time(), iid))
        storage._conn.commit()
    return view(storage, iid)


def _decode_data_url(s: str, kinds: tuple, limit: int) -> tuple:
    if not isinstance(s, str) or "," not in s or not s.startswith("data:"):
        raise ValueError("이미지 형식이 아닙니다")
    head, b64 = s.split(",", 1)
    mime = head[5:].split(";")[0]
    if mime not in kinds:
        raise ValueError(f"{', '.join(kinds)}만 올릴 수 있습니다")
    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError:
        raise ValueError("이미지 데이터가 깨졌습니다")
    if len(raw) > limit:
        raise ValueError(f"이미지가 너무 큽니다({limit // 1000}KB 이하)")
    sig = {"image/jpeg": b"\xff\xd8", "image/png": b"\x89PNG"}
    if not raw.startswith(sig.get(mime, b"")):
        raise ValueError("이미지 내용이 형식과 맞지 않습니다")
    return mime, raw


def add_photo(storage, iid: int, data_url: str, caption: str, by: str) -> dict:
    d = _get(storage, iid)
    if d is None or d["status"] != "draft":
        raise ValueError("진행 중인 점검에만 사진을 올릴 수 있습니다")
    if len(photos(storage, iid)) >= MAX_PHOTOS:
        raise ValueError(f"사진은 점검 하나에 {MAX_PHOTOS}장까지입니다")
    mime, raw = _decode_data_url(data_url, ("image/jpeg", "image/png"), MAX_PHOTO_BYTES)
    with storage._lock:
        cur = storage._conn.execute("INSERT INTO inspection_photos (inspection_id, ts, caption, mime, data, by) "
                                    "VALUES (?, ?, ?, ?, ?, ?)", (iid, time.time(), str(caption or "")[:120], mime, raw, by))
        storage._conn.commit()
    return {"id": cur.lastrowid}


def del_photo(storage, iid: int, pid: int) -> bool:
    d = _get(storage, iid)
    if d is None or d["status"] != "draft":
        return False
    with storage._lock:
        n = storage._conn.execute("DELETE FROM inspection_photos WHERE id=? AND inspection_id=?", (pid, iid)).rowcount
        storage._conn.commit()
    return n > 0


def photo_bytes(storage, pid: int):
    """(mime, bytes, panel, status) 또는 None."""
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT p.mime, p.data, i.panel, i.status FROM inspection_photos p "
                                  "JOIN inspections i ON i.id=p.inspection_id WHERE p.id=?", (pid,)).fetchone()
    return (r["mime"], bytes(r["data"]), r["panel"], r["status"]) if r else None


def complete(storage, iid: int, signer: str, signature: str, by: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    d = _get(storage, iid)
    if d is None:
        raise ValueError("없는 점검입니다")
    if d["status"] != "draft":
        raise ValueError("이미 완료된 점검입니다")
    data = d["data"]
    missing = [it["label"] for g in data["checklist"] for it in g["items"]
               if it["applies"] and it["key"] not in data["results"]]
    if missing:
        raise ValueError(f"체크리스트 {len(missing)}개가 남았습니다 — 예: {missing[0]}")
    signer = str(signer or "").strip()[:40]
    if not signer:
        raise ValueError("고객 확인자 이름을 적어 주세요")
    _decode_data_url(signature, ("image/png",), MAX_SIGN_BYTES)
    p = _panel(storage, d["panel"]) or {"panel_name": d["panel"], "site": ""}
    owner = storage.accounts.panel_owner_map().get(d["panel"])
    cust = next((c["name"] for c in storage.accounts.list_customers() if c["id"] == owner), "") if owner is not None else ""
    res = data["results"]
    counts = {k: sum(1 for v in res.values() if v == k) for k in RESULTS}
    overall = "bad" if counts["bad"] else "fix" if counts["fix"] else "ok"
    next_due = now + data["interval"] * 86400
    report = {
        "panel": d["panel"], "panel_name": p["panel_name"], "site": p.get("site") or "", "customer": cust,
        "started_at": d["created_at"], "done_at": now, "by": by, "signer": signer, "signature": signature,
        "focus": focus(storage, d["panel"], now), "checklist": data["checklist"], "results": res, "notes": data["notes"],
        "summary": data["summary"], "counts": counts, "overall": overall, "interval": data["interval"],
        "next_due": next_due, "photos": photos(storage, iid),
    }
    with storage._lock:
        storage._conn.execute("UPDATE inspections SET status='done', done_at=?, updated_at=?, report=?, next_due=? WHERE id=?",
                              (now, now, json.dumps(report, ensure_ascii=False), next_due, iid))
        storage._conn.commit()
    word = {"ok": "이상 없음", "fix": "현장 조치 완료", "bad": "조치 필요 항목 있음"}[overall]
    storage.log_event(d["panel"], "", "inspection",
                      f"정기 점검 완료: {p['panel_name']} — {word} (확인 {signer}, 다음 점검 {_kdate(next_due)}, {by})",
                      source="user")
    return view(storage, iid)


def history(storage, panels: set | None = None, include_drafts: bool = True, limit: int = 100) -> list:
    ensure(storage)
    q = "SELECT id, panel, status, created_at, done_at, by, next_due, report FROM inspections"
    cond, args = [], []
    if not include_drafts:
        cond.append("status='done'")
    if panels is not None:
        ps = sorted(panels)
        if not ps:
            return []
        cond.append("panel IN (" + ",".join("?" * len(ps)) + ")")
        args += ps
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY COALESCE(done_at, created_at) DESC LIMIT ?"
    with storage._lock:
        rows = storage._conn.execute(q, (*args, limit)).fetchall()
    out = []
    for r in rows:
        rep = json.loads(r["report"]) if r["report"] else None
        out.append({"id": r["id"], "panel": r["panel"], "status": r["status"], "created_at": r["created_at"],
                    "done_at": r["done_at"], "by": r["by"], "next_due": r["next_due"],
                    "overall": rep["overall"] if rep else None, "panel_name": rep["panel_name"] if rep else None,
                    "signer": rep["signer"] if rep else None})
    return out


def due_map(storage) -> dict:
    """판넬 → 다음 정기 점검 예정 시각(마지막 완료 점검 기준)."""
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT panel, MAX(done_at) AS d, next_due FROM inspections WHERE status='done' "
                                     "GROUP BY panel").fetchall()
    return {r["panel"]: r["next_due"] for r in rows}
