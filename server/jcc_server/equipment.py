"""판넬 설비 — 크기·설치 방식·발열 기기·공조 장치를 등록하고, 냉각 용량이 맞는지 계산한다.

필요 냉각량(W) = 판넬 안 발열량 − 판넬 겉면으로 빠지는 열
              = Qv − k · A · (Ti − Tu)
  Qv : 발열 기기 손실의 합(인버터 300W × 4 …)
  k  : 외함 열관류율(W/m²K) — 도장 강판 5.5, 스테인리스 4.5, 알루미늄 12, 플라스틱 3.5
  A  : 열을 내보내는 겉면적(m²) — IEC 60890 설치 방식별 식(단독/벽 붙임 × 단독·열반 끝·열반 가운데)
  Ti : 목표 내부 온도, Tu : 설계 주변 최고 온도. Tu > Ti면 겉면으로 열이 '들어와' 필요량이 커진다.
설치 냉각 = 에어컨·열교환기 용량 합 + 팬필터(바깥이 안보다 찰 때만: 풍량 × ΔT / 3.1).
여유 10%를 두고 '충분 / 빠듯 / 부족'. 숫자는 등록한 값으로만 — 모르는 칸이 있으면 '정보 부족'이라고 말한다.

설정 변경·교체는 사람이 한다. 이 모듈은 계산과 제안까지만.
"""
from __future__ import annotations

import json
import math
import time

K_MATERIAL = {"steel": 5.5, "stainless": 4.5, "aluminum": 12.0, "plastic": 3.5}
MATERIAL_WORD = {"steel": "도장 강판", "stainless": "스테인리스", "aluminum": "알루미늄", "plastic": "플라스틱"}
KIND_WORD = {"aircon": "에어컨", "heat_exchanger": "열교환기", "fan_filter": "팬필터"}
POS_WORD = {"side": "측면", "door": "도어", "roof": "상부"}
MARGIN = 0.10

_SCHEMA = """CREATE TABLE IF NOT EXISTS panel_spec (panel TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL, by TEXT);"""


def ensure(storage) -> None:
    with storage._lock:
        storage._conn.execute(_SCHEMA)
        storage._conn.commit()


# ── 계산(순수) ─────────────────────────────────────────────
def _area_one(w, h, d, wall: bool, pos: str) -> float:
    """IEC 60890 유효 겉면적(m²) — 외함 하나. pos: single | end | mid."""
    if pos == "single":
        return 1.4 * w * (h + d) + 1.8 * d * h if wall else 1.8 * h * (w + d) + 1.4 * w * d
    if pos == "end":
        return 1.4 * h * (w + d) + 1.4 * w * d if wall else 1.4 * d * (h + w) + 1.8 * w * h
    return 1.4 * w * (h + d) + d * h if wall else 1.8 * w * h + 1.4 * w * d + d * h


def area(spec: dict) -> float | None:
    try:
        w, h, d = (float(spec[k]) / 1000.0 for k in ("width", "height", "depth"))
    except (KeyError, TypeError, ValueError):
        return None
    if min(w, h, d) <= 0:
        return None
    n, wall = max(1, int(spec.get("bays") or 1)), spec.get("mount") == "wall"
    if n == 1:
        return _area_one(w, h, d, wall, "single")
    return 2 * _area_one(w, h, d, wall, "end") + (n - 2) * _area_one(w, h, d, wall, "mid")


def calc(spec: dict | None) -> dict:
    """등록 정보 → {status, need_w, installed_w, deficit_w, area, k, heat_w, …}."""
    spec = spec or {}
    out = {"status": "unknown", "missing": []}
    a = area(spec)
    if a is None:
        out["missing"].append("판넬 크기")
    heat = spec.get("heat") or []
    qv = sum(max(0.0, float(x.get("loss_w") or 0)) * max(1, int(x.get("qty") or 1)) for x in heat)
    if not heat:
        out["missing"].append("발열 기기")
    ti, tu = spec.get("target_c"), spec.get("ambient_c")
    if ti is None or tu is None:
        out["missing"].append("목표·주변 온도")
    k = K_MATERIAL.get(spec.get("material") or "steel", 5.5)
    eq = spec.get("equipment") or []
    out.update(heat_w=round(qv), k=k, units=sum(max(1, int(e.get("qty") or 1)) for e in eq if e.get("kind") in KIND_WORD))
    if out["missing"]:
        return out
    ti, tu = float(ti), float(tu)
    loss = k * a * (ti - tu)                      # 겉면으로 빠지는 열(음수면 들어옴)
    need = qv - loss
    inst = 0.0
    for e in eq:
        q = max(1, int(e.get("qty") or 1))
        if e.get("kind") in ("aircon", "heat_exchanger"):
            if e.get("kind") == "heat_exchanger" and tu >= ti:
                continue                          # 열교환기는 바깥이 안보다 찰 때만 식힌다
            inst += max(0.0, float(e.get("capacity_w") or 0)) * q
        elif e.get("kind") == "fan_filter" and ti > tu:
            inst += max(0.0, float(e.get("airflow_m3h") or 0)) * (ti - tu) / 3.1 * q
    out.update(area=round(a, 2), passive_w=round(loss), need_w=round(need), installed_w=round(inst),
               need_margin_w=round(max(0.0, need) * (1 + MARGIN)), dt=round(ti - tu, 1))
    if need <= 0:
        out["status"] = "passive"                 # 겉면 방열만으로 충분(공조 없이도)
    elif inst >= need * (1 + MARGIN):
        out["status"] = "ok"
    elif inst >= need:
        out["status"] = "tight"
    else:
        out["status"] = "short"
        out["deficit_w"] = int(math.ceil((need * (1 + MARGIN) - inst) / 100.0) * 100)
    return out


def advice(spec: dict, c: dict) -> str:
    """계산 결과를 한 문장으로(운영자·고객 공통). 기종 품번은 말하지 않는다 — 현장 확인 후 사람이 고른다."""
    st = c.get("status")
    if st == "unknown":
        return "냉각 용량을 계산하려면 " + "·".join(c["missing"]) + " 정보가 필요합니다."
    if st == "passive":
        return f"판넬 겉면으로 {abs(c['passive_w'])} W를 내보낼 수 있어 발열 {c['heat_w']} W는 공조 없이도 감당되는 계산입니다."
    base = f"필요 냉각 {c['need_w']} W(여유 10% 포함 {c['need_margin_w']} W), 설치된 냉각 {c['installed_w']} W"
    if st == "ok":
        return base + " — 충분합니다."
    if st == "tight":
        return base + " — 여유가 거의 없습니다. 여름 최고 온도나 기기 추가에 대비해 두는 편이 좋습니다."
    n = max(1, int(spec.get("bays") or 1))
    place = " 열반이면 끝단 측면에 큰 유닛, 가운데 칸은 도어 유닛으로 나누는 배치를 권합니다." if n >= 3 else ""
    return base + f" — 약 {c['deficit_w']} W가 모자랍니다. 그만큼 냉각을 더하거나 더 큰 공조로 바꾸는 것을 검토하세요." + place


# ── 저장 ────────────────────────────────────────────────
def _clean(spec: dict) -> dict:
    def num(v, lo, hi):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return x if lo <= x <= hi else None
    out = {"width": num(spec.get("width"), 100, 10000), "height": num(spec.get("height"), 100, 4000),
           "depth": num(spec.get("depth"), 100, 2000), "bays": int(num(spec.get("bays"), 1, 30) or 1),
           "mount": "wall" if spec.get("mount") == "wall" else "free",
           "material": spec.get("material") if spec.get("material") in K_MATERIAL else "steel",
           "target_c": num(spec.get("target_c"), 10, 60), "ambient_c": num(spec.get("ambient_c"), -20, 60)}
    out["heat"] = [{"name": str(x.get("name") or "")[:40], "loss_w": num(x.get("loss_w"), 0, 100000) or 0,
                    "qty": int(num(x.get("qty"), 1, 999) or 1)}
                   for x in (spec.get("heat") or [])[:40] if isinstance(x, dict)]
    eq = []
    for e in (spec.get("equipment") or [])[:20]:
        if not isinstance(e, dict) or e.get("kind") not in KIND_WORD:
            continue
        eq.append({"kind": e["kind"], "name": str(e.get("name") or "")[:60], "qty": int(num(e.get("qty"), 1, 20) or 1),
                   "capacity_w": num(e.get("capacity_w"), 0, 50000) or 0, "airflow_m3h": num(e.get("airflow_m3h"), 0, 5000) or 0,
                   "position": e.get("position") if e.get("position") in POS_WORD else "",
                   "filter_days": int(num(e.get("filter_days"), 0, 730) or 0), "last_service": num(e.get("last_service"), 0, 4e9)})
    out["equipment"] = eq
    return out


def get(storage, panel: str) -> dict | None:
    ensure(storage)
    with storage._lock:
        r = storage._conn.execute("SELECT data, updated_at, by FROM panel_spec WHERE panel=?", (panel,)).fetchone()
    if not r:
        return None
    d = json.loads(r["data"])
    d.update(updated_at=r["updated_at"], updated_by=r["by"])
    return d


def save(storage, panel: str, spec: dict, by: str) -> dict:
    ensure(storage)
    clean = _clean(spec)
    with storage._lock:
        storage._conn.execute("INSERT OR REPLACE INTO panel_spec (panel, data, updated_at, by) VALUES (?, ?, ?, ?)",
                              (panel, json.dumps(clean, ensure_ascii=False), time.time(), by))
        storage._conn.commit()
    return clean


def mark_serviced(storage, panel: str, index: int, by: str, now: float | None = None) -> bool:
    """공조 장치 하나 '필터 청소·점검 함' — 다음 청소일이 여기서부터 다시 센다."""
    sp = get(storage, panel)
    if not sp or not (0 <= index < len(sp.get("equipment") or [])):
        return False
    sp["equipment"][index]["last_service"] = time.time() if now is None else now
    save(storage, panel, sp, by)
    return True


def view(spec: dict | None, now: float | None = None) -> dict | None:
    """화면용(고객·운영자): 장치 목록 말, 다음 필터 청소, 용량 계산, 한 문장 제안."""
    if not spec:
        return None
    now = time.time() if now is None else now
    c = calc(spec)
    units = []
    for i, e in enumerate(spec.get("equipment") or []):
        nxt = None
        if e.get("filter_days") and e.get("last_service"):
            nxt = e["last_service"] + e["filter_days"] * 86400
        cap = (f"{e['capacity_w']:g} W" if e["kind"] != "fan_filter" else f"{e['airflow_m3h']:g} m³/h")
        units.append({"i": i, "kind": e["kind"], "word": KIND_WORD[e["kind"]], "name": e.get("name") or "", "qty": e.get("qty", 1),
                      "cap": cap, "position": POS_WORD.get(e.get("position") or "", ""), "next_service": nxt,
                      "service_due": bool(nxt and nxt <= now)})
    return {"units": units, "calc": c, "advice": advice(spec, c),
            "summary": " · ".join(f"{u['word']} {u['qty']}대" for u in units) or "공조 장치 등록 없음",
            "size": (f"{spec['width']:g}×{spec['height']:g}×{spec['depth']:g}" if spec.get("width") and spec.get("height") and spec.get("depth") else "")
            + (f" · {spec['bays']}면 열반" if (spec.get("bays") or 1) > 1 else ""),
            "material": MATERIAL_WORD.get(spec.get("material") or "steel", "")}
