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
    def close(self) -> None: ...


def build_transport(cfg: Config) -> Transport:
    if cfg.transport_kind == "mqtt":
        from .mqtt_transport import MqttTransport
        return MqttTransport(cfg)
    from .http_transport import HttpTransport
    return HttpTransport(cfg)
