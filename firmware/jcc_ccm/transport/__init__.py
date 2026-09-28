"""데이터 전송.

수집한 텔레메트리를 JCC 클라우드로 보낸다. MQTT와 HTTP 두 경로를 같은
Transport 인터페이스로 감싸, agent는 어느 쪽인지 몰라도 되게 한다.
"""
from __future__ import annotations

from typing import Protocol

from ..config import Config


class Transport(Protocol):
    def connect(self) -> None: ...
    def send(self, payload: dict) -> bool:
        """전송 성공 시 True. 실패는 예외 대신 False로 알려 재시도를 맡긴다."""
        ...
    def take_commands(self) -> list[dict]:
        """서버가 내려보낸 명령(대시보드 수동 조작 등)을 꺼낸다. 꺼내면 비워진다."""
        ...
    def close(self) -> None: ...


def parse_commands(obj) -> list[dict]:
    """서버 응답/MQTT 메시지에서 출력 명령만 골라낸다.

    형식: {"commands": [{"actuator": "vent"|"heater"|"fan", "action": "..."}]}
    (단일 명령 객체도 허용). 모양이 틀린 항목은 조용히 버린다 — 현장 장비가
    이상한 응답 하나로 죽거나 오동작하면 안 된다.
    """
    if isinstance(obj, dict) and "commands" in obj:
        obj = obj.get("commands")
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list):
        return []
    out = []
    for c in obj:
        if (isinstance(c, dict) and isinstance(c.get("actuator"), str)
                and isinstance(c.get("action"), str)):
            out.append({"actuator": c["actuator"], "action": c["action"]})
    return out


def build_transport(cfg: Config) -> Transport:
    if cfg.transport_kind == "mqtt":
        from .mqtt_transport import MqttTransport
        return MqttTransport(cfg)
    from .http_transport import HttpTransport
    return HttpTransport(cfg)
