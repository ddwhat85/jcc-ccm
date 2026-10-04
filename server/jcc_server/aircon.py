"""에어컨 통신 연결(이더넷 · Modbus TCP) — 읽기만 한다. 설정온도를 바꾸는 쓰기는 하지 않는다(사람이 한다).

에어컨 한 대 = 그 판넬 CCM의 '직접 지정 센서' 몇 개(manual_sensors): 내부 온도·설정 온도·압축기 가동·알람·바깥 온도.
레지스터 번호는 기종마다 다르고 여기서 지어내지 않는다 — 운영자가 제조사 Modbus 설명서를 보고 '레지스터 표'(프로파일)를
한 번 입력해 두면, 같은 기종은 IP·유닛 ID만 넣어 계속 쓴다.

쓰는 곳
  - 고객·운영자 화면: 지금 가동 중인지, 설정 온도, 에어컨이 잰 온도, 알람
  - 가동률(압축기 가동의 시간 평균 = 시간별 평균, forecast.rollup이 같이 쌓는다)
  - 원인 분석: 더운 시간대에 거의 내내 돌았는데도 기준을 넘으면 → 냉각 용량 부족이 '실측으로' 확인
"""
from __future__ import annotations

import json
import re
import time

from . import manual_sensors as ms
from .sensor_spec import clean as spec_clean

ITEMS = [  # (항목, 이름, 단위, 꼭 필요한가)
    ("temp", "내부 온도", "C", True),
    ("setpoint", "설정 온도", "C", False),
    ("run", "압축기 가동", "", True),
    ("alarm", "알람", "", False),
    ("ambient", "바깥 온도", "C", False),
]
ITEM_WORD = {k: n for k, n, _, _ in ITEMS}
_SCHEMA = """CREATE TABLE IF NOT EXISTS aircon_profiles (name TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL, by TEXT);"""
_TAG = re.compile(r"^ac\d{1,2}$")


def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


# ── 레지스터 표(기종별) ────────────────────────────────────
def clean_profile(name: str, items: dict) -> dict:
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("레지스터 표 이름(기종)을 넣어 주세요")
    out = {}
    for k, word, _, need in ITEMS:
        x = (items or {}).get(k)
        if not x or x.get("register") in (None, ""):
            if need:
                raise ValueError(f"'{word}' 레지스터는 꼭 필요합니다")
            continue
        # 레지스터 규칙은 센서 직접 지정과 같은 검사(CCM도 같은 규칙으로 다시 본다)
        c = spec_clean({"key": "probe", "driver": "modbus_tcp", "host": "127.0.0.1", "slave": 1,
                        "register": x.get("register"), "type": x.get("type", "holding"), "datatype": x.get("datatype", "int16"),
                        "scale": x.get("scale", 1.0), "offset": x.get("offset", 0.0)})
        out[k] = {f: c[f] for f in ("register", "type", "datatype", "scale", "offset")}
    return {"name": name, "items": out}


def profiles(storage) -> list:
    ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT name, data, updated_at, by FROM aircon_profiles ORDER BY name").fetchall()
    return [dict(json.loads(r["data"]), updated_at=r["updated_at"], by=r["by"]) for r in rows]


def save_profile(storage, name: str, items: dict, by: str) -> dict:
    p = clean_profile(name, items)
    ensure(storage)
    with storage._lock:
        storage._conn.execute("INSERT OR REPLACE INTO aircon_profiles (name, data, updated_at, by) VALUES (?, ?, ?, ?)",
                              (p["name"], json.dumps(p, ensure_ascii=False), time.time(), by))
        storage._conn.commit()
    return p


def _profile(storage, name: str):
    return next((p for p in profiles(storage) if p["name"] == name), None)


# ── 연결 ─────────────────────────────────────────────────
def _keys(tag: str, prof: dict) -> list:
    return [f"{tag}_{k}" for k, *_ in ITEMS if k in prof["items"]]


def _free_tag(storage, dev: str) -> str:
    """이 CCM에서 한 번도 안 쓴 번호 — 예전 에어컨의 기록(값·가동률)과 섞이지 않게."""
    from .forecast import ensure as f_ensure
    f_ensure(storage)
    with storage._lock:
        used = {r["key"].split("_")[0] for r in storage._conn.execute("SELECT key FROM manual_sensors WHERE device_id=?", (dev,))}
        for q in ("SELECT DISTINCT sensor_key AS k FROM readings WHERE device_id=? AND sensor_key GLOB 'ac[0-9]*_*'",
                  "SELECT DISTINCT sensor_key AS k FROM hourly WHERE device_id=? AND sensor_key GLOB 'ac[0-9]*_*'",
                  "SELECT sensor_key AS k FROM discovered WHERE device_id=? AND sensor_key GLOB 'ac[0-9]*_*'"):
            used |= {r["k"].split("_")[0] for r in storage._conn.execute(q, (dev,))}
    return next(f"ac{n}" for n in range(1, 100) if f"ac{n}" not in used)


def link(storage, panel: str, index: int, dev: str, host: str, port, slave, profile: str, by: str) -> dict:
    """판넬 설비의 공조 장치 index번을 CCM dev가 Modbus TCP로 읽게 한다. 이미 연결돼 있으면 바꿔 끼운다."""
    from . import equipment as eqm
    sp = eqm.get(storage, panel)
    if not sp or not (0 <= index < len(sp.get("equipment") or [])):
        raise ValueError("먼저 판넬 설정 → 공조 장치에 이 장치를 저장하세요")
    unit = sp["equipment"][index]
    if unit.get("kind") not in ("aircon", "heat_exchanger"):
        raise ValueError("통신 연결은 에어컨·열교환기만 됩니다")
    ccms = next((p["ccms"] for p in storage.list_panels() if p["panel"] == panel), [])
    if dev not in {c["device_id"] for c in ccms}:
        raise ValueError("이 판넬의 CCM을 고르세요 — 에어컨과 같은 이더넷에 있는 CCM")
    prof = _profile(storage, profile)
    if prof is None:
        raise ValueError("레지스터 표를 고르세요(없으면 먼저 기종별로 입력)")
    old = unit.get("link")
    if old:
        unlink(storage, panel, index, by, save=False, sp=sp)
    reuse = bool(old and old.get("device_id") == dev)      # 같은 에어컨·같은 CCM이면 번호를 그대로(가동률 기록이 이어지게)
    tag = old["tag"] if reuse else _free_tag(storage, dev)
    label = f"{unit.get('name') or '에어컨'} ({tag})"
    made = []
    try:
        for k, word, unit_s, _ in ITEMS:
            reg = prof["items"].get(k)
            if not reg:
                continue
            meta = {"kind": f"aircon_{k}", "product": prof["name"]}
            if k == "alarm":
                meta["alarm_max"] = 0.5          # 0이 아니면 에어컨 알람 — 위험 경보로
            ms.add(storage, dev, dict(reg, key=f"{tag}_{k}", name=f"{label} {word}", unit=unit_s,
                                      driver="modbus_tcp", host=host, port=port, slave=slave), meta, by, reuse_key=reuse)
            made.append(f"{tag}_{k}")
    except ValueError:
        for key in made:                          # 반쯤 만든 것은 되돌린다
            ms.remove(storage, dev, key, by)
        raise
    unit["link"] = {"device_id": dev, "host": str(host), "port": int(port or 502), "slave": int(slave or 0),
                    "profile": prof["name"], "tag": tag}
    eqm.save(storage, panel, sp, by)
    storage.log_event(dev, "", "aircon_link", f"에어컨 통신 연결: {unit.get('name') or '에어컨'} ← {host}:{port} 유닛 {slave} · {prof['name']} ({by})",
                      source="user")
    return unit["link"]


def unlink(storage, panel: str, index: int, by: str, save: bool = True, sp=None) -> bool:
    from . import equipment as eqm
    sp = sp or eqm.get(storage, panel)
    if not sp or not (0 <= index < len(sp.get("equipment") or [])):
        return False
    lk = sp["equipment"][index].get("link")
    if not lk:
        return False
    drop_link(storage, lk, by)
    sp["equipment"][index]["link"] = None
    if save:
        eqm.save(storage, panel, sp, by)
    return True


def drop_link(storage, lk: dict, by: str) -> None:
    """연결 하나의 직접 지정 센서를 CCM에서 걷어낸다(장치를 지우거나 연결을 끊을 때)."""
    if not lk or not _TAG.match(str(lk.get("tag", ""))):
        return
    with storage._lock:
        keys = [r["key"] for r in storage._conn.execute("SELECT key FROM manual_sensors WHERE device_id=?", (lk["device_id"],))]
    for k in keys:
        if k.split("_")[0] == lk["tag"]:
            ms.remove(storage, lk["device_id"], k, by)


# ── 지금 상태 · 가동률 ────────────────────────────────────
def unit_status(storage, lk: dict, now: float | None = None) -> dict:
    """연결된 에어컨 한 대: CCM 반영 상태, 지금 값, 최근 24시간·더운 시간대(평일 12~18시, 14일) 가동률."""
    from .forecast import KST, ensure as f_ensure
    from datetime import datetime
    now = time.time() if now is None else now
    dev, tag = lk["device_id"], lk["tag"]
    st = {s["key"]: s for s in ms.status_list(storage, dev) if s["key"].split("_")[0] == tag}
    states = {s["status"] for s in st.values()}
    rej = next((f"{k}: {s['reason']}" for k, s in st.items() if s["status"] == "rejected"), "")
    vals, fresh = {}, False
    for p in storage.list_panels():
        for c in p["ccms"]:
            if c["device_id"] != dev:
                continue
            for x in c.get("latest") or []:
                if x["sensor_key"].split("_")[0] == tag and x.get("value") is not None:
                    vals[x["sensor_key"][len(tag) + 1:]] = x["value"]
                    fresh = fresh or bool(x.get("ok")) and (now - (x.get("ts") or 0) < 300)
    f_ensure(storage)
    with storage._lock:
        rows = storage._conn.execute("SELECT hour, avg FROM hourly WHERE device_id=? AND sensor_key=? AND hour >= ?",
                                     (dev, f"{tag}_run", now - 14 * 86400)).fetchall()
    day = [r["avg"] for r in rows if r["hour"] >= now - 86400 and r["avg"] is not None]
    hot = []
    for r in rows:
        k = datetime.fromtimestamp(r["hour"], KST)
        if k.weekday() < 5 and 12 <= k.hour < 18 and r["avg"] is not None:
            hot.append(r["avg"])
    link_state = ("rejected" if "rejected" in states else "pending" if "pending" in states or not st
                  else "live" if fresh else "silent")
    return {"tag": tag, "device_id": dev, "host": lk.get("host"), "profile": lk.get("profile"), "state": link_state, "reason": rej,
            "temp": vals.get("temp"), "setpoint": vals.get("setpoint"), "ambient": vals.get("ambient"),
            "running": None if vals.get("run") is None else vals["run"] >= 0.5,
            "alarm": None if vals.get("alarm") is None else vals["alarm"] >= 0.5,
            "duty_24h": round(sum(day) / len(day), 2) if day else None,
            "duty_hot": round(sum(hot) / len(hot), 2) if len(hot) >= 6 else None, "hot_hours": len(hot)}


def panel_status(storage, panel: str, now: float | None = None) -> list:
    from . import equipment as eqm
    sp = eqm.get(storage, panel) or {}
    out = []
    for i, e in enumerate(sp.get("equipment") or []):
        if e.get("link"):
            out.append(dict(unit_status(storage, e["link"], now), index=i, name=e.get("name") or "에어컨"))
    return out
