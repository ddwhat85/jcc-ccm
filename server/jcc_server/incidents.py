"""경보 원인 묶기 — 한 사건이 낳은 여러 경보를 '원인 1건 + 관련 경보'로 묶는다.

현장에선 사건 하나가 경보를 줄줄이 만든다. 화재 징조 하나에 수소 경고·VOC 경고·
수소 이상·드리프트·온도 이상이 한꺼번에 뜨고, 운영자는 7줄을 읽어야 무슨 일인지 안다.
원인 기준으로 묶으면 '화재 징조 1건'이 되고, 알림도 원인당 한 번만 보낸다(알림 피로 감소).

규칙(위에서부터 먼저 잡은 쪽이 가져간다 — 경보 하나는 사건 하나에만 속한다):
  1) CCM 통신 두절   : 두절 이후 그 CCM에서 생긴 경보는 통신 문제의 결과(예지 원인은 제외)
  2) 화재 징조(fire) : 같은 판넬의 가스(H2·VOC·CO) 경보·이상·드리프트, 함내온도 이상, 연기 감지,
                       벤트 작동 실패(원인은 화재, 문제는 벤트 — 화재 사건 안에서 보여야 한다)
  3) 접점 발열       : 접점온도 센서의 경보·이상·드리프트
  4) 결로            : 같은 판넬의 습도 경보·이상·드리프트·고착(포화로 값이 멎음)
  5) 같은 센서의 여러 경보 : 가장 심각한 것이 대표
원인 경보 이전 PRE_WINDOW초 안에 뜬 관련 경보도 묶는다(징조가 먼저 보이는 경우).

순수 함수 — 저장소·네트워크 무관. web/demo-api.js가 같은 규칙을 미러한다(test_parity).
"""
from __future__ import annotations

PRE_WINDOW = 1800.0      # 원인 경보보다 이만큼 먼저 뜬 관련 경보까지 같은 사건으로
CCM_SLACK = 60.0         # CCM 두절 판정(수십 초 지연) 직전에 뜬 센서 침묵까지는 두절의 결과로
_SEV = {"crit": 2, "warn": 1}
_PRIO = {"fire": 0, "contact": 1, "dew": 2, "ccm": 3, "sensor": 4}   # 같은 심각도에서의 표시 순서

_FIRE_CHILD = ("alarm", "alarm_warn", "anomaly", "drift")
_CONTACT_CHILD = ("alarm", "alarm_warn", "anomaly", "drift")
_DEW_CHILD = ("alarm", "alarm_warn", "anomaly", "drift", "stuck")

_TITLE = {
    "ccm": "CCM {dev} 통신 두절 — 그 CCM의 경보는 통신 문제의 결과",
    "fire": "화재 징조 — 가스·온도 동반 이상",
    "contact": "접점 발열 — 접점온도 이상",
    "dew": "결로 위험 — 습도 이상",
}


def group_alarms(alarms: list, sensors: dict, panels: dict) -> list:
    """alarms  = [{id, device_id, sensor_key, kind, severity, raised_at, acked_at, detail}, ...]
       sensors = {(device_id, sensor_key): kind}   센서 종류(h2·voc·temp·humidity·smoke …)
       panels  = {device_id: panel}                 CCM이 속한 판넬
    반환: 사건 목록(심각도 높은 순, 같으면 최근 순)
      {key, cause, title, severity, primary, alarms:[id…], count, started_at, acked, device_id, sensor_key}
    """
    items = sorted((dict(a) for a in alarms), key=lambda a: (a.get("raised_at") or 0, a.get("id") or 0))
    used: set = set()
    incidents: list = []

    def kind_of(a):
        return sensors.get((a.get("device_id") or "", a.get("sensor_key") or ""), "")

    def panel_of(a):
        return panels.get(a.get("device_id") or "", a.get("device_id") or "")

    def make(cause, primary, members, title):
        ids = [m["id"] for m in members]
        used.update(ids)
        sev = max((m.get("severity") or "warn" for m in members), key=lambda s: _SEV.get(s, 0))
        incidents.append({
            "key": f"{cause}:{primary.get('device_id') or ''}:{primary.get('sensor_key') or ''}:{primary.get('id')}",
            "cause": cause, "title": title, "severity": sev, "primary": primary["id"],
            "alarms": ids, "count": len(ids),
            "started_at": min(m.get("raised_at") or 0 for m in members),
            "acked": all(m.get("acked_at") for m in members),
            "device_id": primary.get("device_id") or "", "sensor_key": primary.get("sensor_key") or "",
        })

    def attach(root, pred):
        t0 = (root.get("raised_at") or 0) - PRE_WINDOW
        return [root] + [a for a in items if a["id"] != root["id"] and a["id"] not in used
                         and (a.get("raised_at") or 0) >= t0 and pred(a)]

    # 1) CCM 통신 두절 — 두절 '이후'에 그 CCM에서 생긴 경보만 결과로 본다(CCM_SLACK초 여유).
    #    예지 원인(화재·접점·결로)은 절대 삼키지 않는다: 화재 징조 뒤 CCM이 끊겼다면 화재가
    #    CCM을 망가뜨렸을 수도 있다 — 그건 통신 문제가 아니라 가장 중요한 경보다.
    for a in items:
        if a["id"] in used or a.get("kind") != "silent" or a.get("sensor_key"):
            continue
        dev, t0 = a.get("device_id"), (a.get("raised_at") or 0) - CCM_SLACK
        members = [a] + [b for b in items if b["id"] != a["id"] and b["id"] not in used
                         and b.get("device_id") == dev and (b.get("raised_at") or 0) >= t0
                         and b.get("kind") not in ("fire", "contact", "dew")]
        make("ccm", a, members, _TITLE["ccm"].format(dev=dev))

    # 2~4) 예지 원인
    rules = (
        ("fire", lambda root, b: panel_of(b) == panel_of(root) and (
            (kind_of(b) in ("h2", "voc", "co") and b.get("kind") in _FIRE_CHILD)
            or (kind_of(b) == "temp" and "ncontact" not in (b.get("sensor_key") or "")
                and b.get("kind") in ("anomaly", "drift", "alarm_warn", "alarm"))
            or (kind_of(b) == "smoke" and b.get("kind") == "alarm")   # 연기 '감지'만(센서 고장은 별개)
            or (b.get("kind") == "actuator_fault" and b.get("sensor_key") == "vent"))),   # 화재 중 벤트 고장
        ("contact", lambda root, b: b.get("device_id") == root.get("device_id")
            and b.get("sensor_key") == root.get("sensor_key") and b.get("kind") in _CONTACT_CHILD),
        ("dew", lambda root, b: panel_of(b) == panel_of(root) and kind_of(b) == "humidity"
            and b.get("kind") in _DEW_CHILD),
    )
    for cause, pred in rules:
        for a in items:
            if a["id"] in used or a.get("kind") != cause:
                continue
            make(cause, a, attach(a, lambda b, a=a, pred=pred: pred(a, b)), _TITLE[cause])

    # 5) 같은 센서의 여러 경보
    by_sensor: dict = {}
    for a in items:
        if a["id"] not in used:
            by_sensor.setdefault((a.get("device_id") or "", a.get("sensor_key") or ""), []).append(a)
    for group in by_sensor.values():
        primary = max(group, key=lambda m: (_SEV.get(m.get("severity") or "warn", 0), -(m.get("raised_at") or 0)))
        make("sensor", primary, group, primary.get("detail") or primary.get("kind") or "경보")

    # 같은 심각도면 안전 사건(화재>접점>결로)이 장비 문제(CCM 두절·단일 센서)보다 앞, 그다음 최근 순
    incidents.sort(key=lambda i: (-_SEV.get(i["severity"], 0), _PRIO.get(i["cause"], 9), -i["started_at"]))
    return incidents


def incident_of(incidents: list, alarm_id) -> dict | None:
    for inc in incidents:
        if alarm_id in inc["alarms"]:
            return inc
    return None


def should_notify(alarm_id, incidents: list, sent_ids: set) -> bool:
    """이 경보로 문자·카톡을 보낼지. 같은 사건의 다른 경보로 이미 보냈으면 보내지 않는다
    (화재 징조로 이미 알렸는데 수소 위험선 경보로 또 알리지 않게 — 알림 피로 방지)."""
    inc = incident_of(incidents, alarm_id)
    if inc is None:
        return True
    return not any(x in sent_ids for x in inc["alarms"] if x != alarm_id)
