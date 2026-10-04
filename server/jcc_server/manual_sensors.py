"""수동 센서 — 자동 탐색에 안 잡힌 센서를 JCC 직원이 '노드 추가'에서 직접 지정한다.

흐름
  1. 대시보드에서 CCM·연결 방식(RS485/Modbus TCP)·주소·레지스터를 지정 → 서버가 규격 검사(sensor_spec)
  2. CCM별 목록에 저장하고 버전을 올린다. 화면에는 바로 노드가 생긴다(값은 CCM 반영 전까지 대기)
  3. CCM이 다음 보고 때 버전이 다르면 응답에 전체 목록을 실어 보낸다 → CCM이 같은 규칙으로 다시 검사·적용·저장
  4. CCM이 보고한 결과(적용됨/거부 이유)를 기억해 화면에 보여 준다
다시 자동 탐색해도 수동 센서는 인벤토리에 남는다(storage.set_discovery가 inventory_insert를 부른다).
"""
from __future__ import annotations

import json
import time

from .sensor_spec import MAX_PER_CCM, clean

_META_KEYS = ("kind", "brand", "product", "part_no", "manual", "alarm_min", "alarm_warn", "alarm_max", "photo")


def _num_or_none(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def inventory_insert(cur, dev: str, now: float) -> None:
    """(storage 락 안에서) 이 CCM의 수동 센서를 discovered 표에 넣는다."""
    for r in cur.execute("SELECT key, spec, meta FROM manual_sensors WHERE device_id=?", (dev,)).fetchall():
        spec, meta = json.loads(r["spec"]), json.loads(r["meta"] or "{}")
        cur.execute("DELETE FROM discovered WHERE device_id=? AND sensor_key=?", (dev, r["key"]))
        if spec["driver"] == "modbus":    # 같은 주소를 값 범위로 '추정'만 했던 항목은 직접 지정이 대체한다
            cur.execute("DELETE FROM discovered WHERE device_id=? AND confidence='추정' AND address=?",
                        (dev, spec["slave"]))
        addr = spec["slave"] if spec["driver"] == "modbus" else f"{spec.get('host')}:{spec.get('port')}#{spec['slave']}"
        cur.execute(
            """INSERT INTO discovered (device_id, sensor_key, name, unit, kind, source, confidence, address,
                   discovered_at, brand, product, part_no, manual, alarm_min, alarm_max, alarm_warn, relays, photo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dev, r["key"], spec["name"], spec["unit"], meta.get("kind") or "",
             "modbus_tcp" if spec["driver"] == "modbus_tcp" else "modbus", "수동", addr, now,
             meta.get("brand", ""), meta.get("product", ""), meta.get("part_no", ""), meta.get("manual", ""),
             _num_or_none(meta.get("alarm_min")), _num_or_none(meta.get("alarm_max")),
             _num_or_none(meta.get("alarm_warn")), "[]", meta.get("photo", "")))


def _bump(cur, dev: str) -> int:
    r = cur.execute("SELECT version FROM manual_version WHERE device_id=?", (dev,)).fetchone()
    v = (r["version"] if r else 0) + 1
    cur.execute("INSERT INTO manual_version (device_id, version) VALUES (?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET version=excluded.version", (dev, v))
    return v


def version_of(storage, dev: str) -> int:
    with storage._lock:
        r = storage._conn.execute("SELECT version FROM manual_version WHERE device_id=?", (dev,)).fetchone()
    return r["version"] if r else 0


def specs_of(storage, dev: str) -> list:
    with storage._lock:
        rows = storage._conn.execute("SELECT spec FROM manual_sensors WHERE device_id=? ORDER BY created_at, key",
                                     (dev,)).fetchall()
    return [json.loads(r["spec"]) for r in rows]


def add(storage, dev: str, spec: dict, meta: dict | None, by: str, reuse_key: bool = False) -> dict:
    """검사 후 저장. 틀리면 ValueError(사람이 읽을 이유).
    reuse_key=True: 같은 장치를 다시 연결할 때 — 최근 값이 남은 키라도 그 장치의 것이니 그대로 쓴다(기록이 이어지게)."""
    spec = clean(spec)
    meta = {k: (meta or {}).get(k) for k in _META_KEYS if (meta or {}).get(k) not in (None, "")}
    now = time.time()
    with storage._lock:
        cur = storage._conn.cursor()
        if cur.execute("SELECT 1 FROM devices WHERE device_id=?", (dev,)).fetchone() is None:
            raise ValueError("없는 CCM입니다")
        mine = {r["key"] for r in cur.execute("SELECT key FROM manual_sensors WHERE device_id=?", (dev,))}
        if spec["key"] in mine:
            raise ValueError(f"이 CCM에 같은 키({spec['key']})의 수동 센서가 이미 있습니다")
        taken = {r["sensor_key"] for r in cur.execute("SELECT sensor_key FROM discovered WHERE device_id=?", (dev,))}
        if not reuse_key:
            taken |= {r["sensor_key"] for r in cur.execute("SELECT DISTINCT sensor_key FROM readings WHERE device_id=? "
                                                             "AND ts > ?", (dev, now - 86400))}
        if spec["key"] in taken:
            raise ValueError(f"이 CCM에 이미 '{spec['key']}' 센서가 있습니다 — 다른 키를 쓰세요")
        if len(mine) >= MAX_PER_CCM:
            raise ValueError(f"CCM 하나에 수동 센서는 {MAX_PER_CCM}개까지입니다")
        cur.execute("INSERT INTO manual_sensors (device_id, key, spec, meta, created_at, by) VALUES (?, ?, ?, ?, ?, ?)",
                    (dev, spec["key"], json.dumps(spec, ensure_ascii=False), json.dumps(meta, ensure_ascii=False),
                     now, by))
        v = _bump(cur, dev)
        inventory_insert(cur, dev, now)
        storage._conn.commit()
    how = (f"RS485 주소 {spec['slave']}" if spec["driver"] == "modbus"
           else f"Modbus TCP {spec['host']}:{spec['port']} 유닛 {spec['slave']}")
    storage.log_event(dev, spec["key"], "manual_sensor",
                      f"센서 직접 지정: {spec['name']} ({how}, 레지스터 {spec['register']}) — CCM 반영 대기 v{v} ({by})",
                      source="user")
    return dict(spec, version=v)


def remove(storage, dev: str, key: str, by: str) -> bool:
    with storage._lock:
        cur = storage._conn.cursor()
        n = cur.execute("DELETE FROM manual_sensors WHERE device_id=? AND key=?", (dev, key)).rowcount
        if not n:
            return False
        cur.execute("DELETE FROM discovered WHERE device_id=? AND sensor_key=?", (dev, key))
        v = _bump(cur, dev)
        storage._conn.commit()
    storage.log_event(dev, key, "manual_sensor", f"직접 지정한 센서 삭제: {key} — CCM 반영 대기 v{v} ({by})",
                      source="user")
    return True


def note_report(storage, dev: str, rep) -> None:
    """CCM 보고 {version, status, active, errors} 기억. 이 버전의 결과가 처음 들어온 순간만 기록."""
    if not dev or not isinstance(rep, dict):
        return
    prev = storage._manual_edge.get(dev) or {}
    cur = {"version": rep.get("version"), "status": str(rep.get("status") or ""),
           "active": [str(k) for k in (rep.get("active") or [])][:64],
           "errors": {str(k): str(v)[:200] for k, v in (rep.get("errors") or {}).items()} if isinstance(
               rep.get("errors"), dict) else {}, "ts": time.time()}
    storage._manual_edge[dev] = cur
    if (prev.get("version"), prev.get("status")) == (cur["version"], cur["status"]) or not cur["status"]:
        return
    if cur["version"] != version_of(storage, dev):
        return
    if cur["errors"]:
        bad = " · ".join(f"{k}: {v}" for k, v in list(cur["errors"].items())[:3])
        storage.log_event(dev, "", "manual_sensor", f"CCM이 직접 지정 센서 일부를 거부 — {bad}", source="edge")
    else:
        storage.log_event(dev, "", "manual_sensor", f"CCM이 직접 지정 센서 v{cur['version']} 적용 "
                                                    f"({len(cur['active'])}개)", source="edge")


def offer_for(storage, dev: str, rep):
    """이 CCM에 내려보낼 수동 센서 목록. 처음부터 없었거나 이미 같은 버전이면 None."""
    v = version_of(storage, dev)
    if v == 0:
        return None
    if isinstance(rep, dict) and rep.get("version") == v:
        return None
    return {"version": v, "sensors": specs_of(storage, dev)}


def status_list(storage, dev: str = "") -> list:
    """화면용: 수동 센서마다 CCM 반영 상태."""
    q = "SELECT device_id, key, spec, meta, created_at, by FROM manual_sensors"
    args: tuple = ()
    if dev:
        q += " WHERE device_id=?"
        args = (dev,)
    with storage._lock:
        rows = storage._conn.execute(q + " ORDER BY device_id, created_at", args).fetchall()
    out = []
    for r in rows:
        edge = storage._manual_edge.get(r["device_id"]) or {}
        want = version_of(storage, r["device_id"])
        if edge.get("version") == want and r["key"] in edge.get("active", []):
            st, why = "applied", ""
        elif edge.get("version") == want and r["key"] in edge.get("errors", {}):
            st, why = "rejected", edge["errors"][r["key"]]
        else:
            st, why = "pending", ""
        out.append({"device_id": r["device_id"], "key": r["key"], "spec": json.loads(r["spec"]),
                    "meta": json.loads(r["meta"] or "{}"), "created_at": r["created_at"], "by": r["by"],
                    "status": st, "reason": why})
    return out
