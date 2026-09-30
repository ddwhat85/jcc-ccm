"""현장 설치 점검(시운전) — 판넬 하나를 연결·센서·예지 역할·출력 시험 순서로 확인한다.

마법사는 점검·안내·기록까지 한다(설정 변경은 기사가 배선·CCM 설정 파일에서 — 사용자 결정).
항목 판정: pass · warn · fail · wait(관찰 중·시험 중) · skip(해당 없음). 실패·주의엔 '할 일'을 붙인다.

출력 시험(벤트·히터·팬)은 서버가 단계를 진행한다:
  on_sent → 현장 CCM이 켜짐·작동 확인(ok) → off_sent → 꺼짐·확인 → 자동 복귀 → pass
  작동 실패(fault) → fail, 확인 장치 없음 → 반영만 확인하고 warn, 시간 초과 → fail
  어떤 경우든 끝에는 자동 복귀 명령(설치 뒤 수동 상태로 남지 않게).
"""
from __future__ import annotations

import time

from .inputs import roles_from_kinds

TEST_TIMEOUT = 150.0
_WORD = {"vent": ("벤트", "개방", "닫힘"), "heater": ("히터", "가동", "정지"), "fan": ("팬", "가동", "정지")}
_ON = {"vent": "open", "heater": "on", "fan": "on"}
_OFF = {"vent": "close", "heater": "off", "fan": "off"}

# 진단 항목 → 설치 기사가 할 일
ADVICE = {
    "연결 상태": "CCM 전원·이더넷 케이블·서버 주소(config.toml [transport]) 확인",
    "센서 응답": "응답 없는 센서의 전원·RS485 배선(A/B 극성)·Modbus 주소 확인",
    "채널 상태": "꺼진 채널이 있음 — 편집 → 꺼진 센서 모두 켜기",
    "마지막 접속": "CCM이 한동안 보고하지 않음 — 네트워크·전원 확인",
    "데이터 수신": "센서 전원·RS485 배선(A/B 극성)·Modbus 주소·통신속도 확인",
    "값 유효성": "읽기 오류 — 배선·주소·통신속도(9600/19200) 확인",
    "측정 범위": "값이 경보 범위 밖 — 실제 이상인지 먼저 확인, 아니면 센서 위치·교정 확인",
    "변동성": "값이 멈춤 — 센서 동결·단선 의심, 센서 전원 재투입",
    "추세": "값이 한쪽으로 계속 이동 — 교정 필요 여부 확인",
    "장치 식별": "추정 장치 — 실제 모델명을 확인해 프로파일 확정",
}
_SKIP_CHECKS = ("이상탐지",)          # 설치 직후엔 평소값을 아직 모른다 — 시운전 판정에서 뺀다
_ROLE_WORD = {"h2": "수소", "voc": "VOC", "co": "CO", "current": "전류", "contact_temp": "접점온도",
              "ambient": "함내온도", "humidity": "습도", "smoke": "연기"}
_ALGOS = (("fire", "화재 징조 예지", None), ("contact", "접점 발열 예지", ("current", "contact_temp", "ambient")),
          ("dew", "결로 예지", ("ambient", "humidity")))


def _worst(items) -> str:
    st = [i["status"] for i in items]
    for s in ("fail", "wait", "warn"):
        if s in st:
            return s
    return "pass" if st and any(s == "pass" for s in st) else ("skip" if st else "pass")


def _item(target, status, detail, advice=""):
    return {"target": target, "status": status, "detail": detail, "advice": advice}


def _from_diag(rep, target) -> dict:
    checks = [c for c in rep.get("checks", []) if c["name"] not in _SKIP_CHECKS]
    bad = next((c for c in checks if c["status"] == "fail"), None) or next((c for c in checks if c["status"] == "warn"), None)
    if bad:
        return _item(target, bad["status"], f"{bad['name']}: {bad['detail']}", ADVICE.get(bad["name"], ""))
    return _item(target, "pass", " · ".join(c["detail"] for c in checks[:2]) or "정상")


def _panel(storage, panel):
    return next((p for p in storage.list_panels() if p["panel"] == panel), None)


def _edges(storage, panel) -> dict:
    return {dev: e for dev, e in storage.edge_state().items() if e.get("panel") == panel}


def check_panel(storage, panel: str, now: float | None = None) -> dict | None:
    """판넬 점검 스냅숏. 판넬이 없으면 None."""
    now = time.time() if now is None else now
    p = _panel(storage, panel)
    if p is None:
        return None
    advance_tests(storage, panel, now)
    ccms, edges = p["ccms"], _edges(storage, panel)
    steps = []

    # 1) 연결
    items = [_from_diag(storage.diagnose(d["device_id"], "", _dev=d), f"CCM {d['device_id']}") for d in ccms]
    if edges:
        items.append(_item("현장 예지", "pass", f"{', '.join(sorted(edges))} — 서버·인터넷이 끊겨도 벤트 자율 작동"))
    else:
        items.append(_item("현장 예지", "warn", "현장 CCM 예지가 꺼져 있음 — 서버가 끊기면 벤트가 스스로 못 움직임",
                           "CCM config.toml의 [predict] enabled = true, 역할([predict.roles])·출력([[actuators]]) 설정"))
    steps.append({"key": "connect", "title": "연결", "status": _worst(items), "items": items})

    # 2) 센서 — 표본이 적으면 '관찰 중'(고착·범위를 아직 판단할 수 없다)
    items = []
    for d in ccms:
        for s in d.get("latest") or []:
            key = s["sensor_key"]
            target = f"{s.get('name') or key} ({d['device_id']})"
            it = _from_diag(storage.diagnose(d["device_id"], key, _dev=d), target)
            n = storage.history_stats(d["device_id"], key)["n"]
            if it["status"] != "fail" and n < 6 and s.get("enabled") is not False:
                it = _item(target, "wait", f"관찰 중 — 값 {n}/6개 수신(약 30초 기다림)")
            items.append(it)
    if not items:
        items.append(_item("센서", "fail", "발견된 센서가 없음", "도구 → AI 자동연결로 탐색하거나 CCM의 센서 배선 확인"))
    steps.append({"key": "sensors", "title": "센서", "status": _worst(items), "items": items})

    # 3) 예지 역할 — 서버가 센서 종류로 판단한 역할 vs CCM 설정 파일의 역할
    roles = roles_from_kinds(ccms)
    items = []
    gases = [r for r in ("h2", "voc", "co") if roles.get(r)]
    for algo, title, need in _ALGOS:
        if algo == "fire":
            ok, missing = bool(gases), ["가스 센서(H2·VOC·CO)"]
            have = "·".join(_ROLE_WORD[g] for g in gases)
        else:
            missing = [_ROLE_WORD[r] for r in need if not roles.get(r)]
            ok, have = not missing, "·".join(_ROLE_WORD[r] for r in need)
        items.append(_item(title, "pass" if ok else "warn", f"가능 — {have}" if ok else f"불가 — {', '.join(missing)} 없음",
                           "" if ok else "필요 센서를 추가하거나, 이 판넬엔 해당 예지를 쓰지 않는다고 고객에게 안내"))
    for dev, e in sorted(edges.items()):
        cr = e.get("roles") if isinstance(e.get("roles"), dict) else None
        if cr is None:
            continue
        diff = []
        for role, pair in roles.items():
            if not pair or pair[0] != dev:
                continue
            if cr.get(role) != pair[1]:
                diff.append(f"{_ROLE_WORD.get(role, role)}: CCM={cr.get(role) or '없음'} / 서버={pair[1]}")
        items.append(_item(f"CCM {dev} 역할 설정", "warn" if diff else "pass",
                           "; ".join(diff) if diff else "서버 판단과 같음",
                           "CCM config.toml [predict.roles]를 서버 판단대로 고치고 서비스 재시작" if diff else ""))
    steps.append({"key": "roles", "title": "예지 역할", "status": _worst(items), "items": items})

    # 4) 출력 시험
    items = []
    tests = storage.ctests.get(panel, {})
    for dev, e in sorted(edges.items()):
        for kind in sorted((e.get("actuators") or {}), key=lambda k: ("vent", "heater", "fan").index(k)
                           if k in ("vent", "heater", "fan") else 9):
            name = _WORD.get(kind, (kind,))[0]
            t = tests.get(kind)
            if t is None:
                items.append(_item(name, "wait", f"{dev} — 시험 전", "판넬 앞에서 [시험]을 누르세요 — 실제로 켜졌다 꺼집니다"))
            elif t["phase"] in ("on_sent", "off_sent"):
                items.append(_item(name, "wait", t["detail"]))
            else:
                items.append(_item(name, t["phase"], t["detail"], t.get("advice", "")))
    if not items:
        items.append(_item("출력", "skip", "현장 CCM이 쥔 출력 없음 — 벤트·히터·팬은 서버 판정·수동 조작만"))
    steps.append({"key": "outputs", "title": "출력 시험", "status": _worst(items), "items": items})

    sts = [s["status"] for s in steps]
    overall = "fail" if "fail" in sts else "wait" if "wait" in sts else "warn" if "warn" in sts else "pass"
    return {"panel": panel, "panel_name": p["panel_name"], "ts": now, "overall": overall, "steps": steps}


# ── 출력 시험 상태기계 ─────────────────────────────────────
def start_output_test(storage, panel: str, kind: str, by: str, now: float | None = None):
    """시험 시작(켜기 명령). 오류면 문자열."""
    now = time.time() if now is None else now
    if kind not in _ON:
        return "벤트·히터·팬만 시험할 수 있습니다"
    owner = next((dev for dev, e in _edges(storage, panel).items() if kind in (e.get("actuators") or {})), None)
    if owner is None:
        return "이 판넬엔 그 출력을 쥔 현장 CCM이 없습니다"
    cur = storage.ctests.get(panel, {}).get(kind)
    if cur and cur["phase"] in ("on_sent", "off_sent"):
        return "이미 시험 중입니다"
    storage.set_actuator(panel, kind, _ON[kind], by=f"{by} · 설치 시험")
    name, on_w, _ = _WORD[kind]
    storage.ctests.setdefault(panel, {})[kind] = {
        "phase": "on_sent", "dev": owner, "t0": now, "by": by, "no_confirm": False,
        "detail": f"{name} {on_w} 명령을 보냄 — 현장 CCM 확인 기다리는 중"}
    return None


def _finish(storage, panel, kind, t, phase, detail, advice=""):
    t.update(phase=phase, detail=detail, advice=advice)
    storage.set_actuator(panel, kind, "auto", by=f"{t['by']} · 설치 시험 끝")      # 항상 자동 복귀
    storage.log_event(t["dev"], kind, "commission", f"설치 시험 {_WORD[kind][0]}: {detail}", source="user")


def advance_tests(storage, panel: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    tests = storage.ctests.get(panel) or {}
    edges = _edges(storage, panel)
    for kind, t in tests.items():
        if t["phase"] not in ("on_sent", "off_sent"):
            continue
        name, on_w, off_w = _WORD[kind]
        a = ((edges.get(t["dev"]) or {}).get("actuators") or {}).get(kind) or {}
        conf = a.get("confirm")
        if conf == "fault":
            _finish(storage, panel, kind, t, "fail", f"작동 실패 — {a.get('fault') or '명령과 실제가 다름'}",
                    "구동기 걸림·배선·전원·릴레이 모듈 확인 후 다시 시험")
            continue
        if now - t["t0"] > TEST_TIMEOUT:
            _finish(storage, panel, kind, t, "fail", "CCM 응답 없음 — 명령이 반영되지 않았습니다",
                    "CCM 온라인·[[actuators]] 설정·릴레이 모듈 주소 확인")
            continue
        want = t["phase"] == "on_sent"
        if a.get("on") is not want or a.get("mode") != "manual":
            continue                                     # 아직 반영 전
        if conf in (None, "", "off", "unknown"):
            t["no_confirm"] = True                        # 확인 장치 없음 — 명령 반영만 본다
        elif conf != "ok":
            continue                                     # 작동 중(moving)
        if want:
            storage.set_actuator(panel, kind, _OFF[kind], by=f"{t['by']} · 설치 시험")
            t.update(phase="off_sent", detail=f"{name} {on_w} 확인 — {off_w} 명령을 보냄")
        elif t["no_confirm"]:
            _finish(storage, panel, kind, t, "warn", f"{on_w}·{off_w} 명령 반영됨(릴레이만 확인)",
                    "위치 스위치가 없어 실제 움직임은 모름 — 눈으로 확인하고, 보조접점 달린 구동기 권장")
        else:
            _finish(storage, panel, kind, t, "pass", f"{on_w}·{off_w} 모두 실제 작동 확인")
