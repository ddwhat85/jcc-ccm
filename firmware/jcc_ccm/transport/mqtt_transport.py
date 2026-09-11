"""MQTT 전송 (paho-mqtt).

MQTT는 IoT 텔레메트리의 사실상 표준이다. 경량이고, TLS를 지원하며,
브로커가 재연결·QoS를 처리해줘 불안정한 현장 회선에 강하다.
"""
from __future__ import annotations

import json

from ..config import Config


class MqttTransport:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._m = cfg.mqtt
        self._topic = self._m.topic.replace("{device_id}", cfg.device_id)
        self._client = None
        self._connected = False

    def connect(self) -> None:
        from paho.mqtt import client as mqtt

        self._client = mqtt.Client(
            client_id=f"jcc-ccm-{self._cfg.device_id}",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        if self._m.username:
            self._client.username_pw_set(self._m.username, self._m.password)
        if self._m.tls:
            self._client.tls_set()  # 시스템 CA 사용

        def _on_connect(client, userdata, flags, rc):  # noqa: ANN001
            self._connected = (rc == 0)

        def _on_disconnect(client, userdata, rc):  # noqa: ANN001
            self._connected = False

        self._client.on_connect = _on_connect
        self._client.on_disconnect = _on_disconnect
        self._client.connect(self._m.host, self._m.port, keepalive=60)
        self._client.loop_start()  # 백그라운드 스레드가 재연결·핑 처리

    def send(self, payload: dict) -> bool:
        if self._client is None:
            return False
        try:
            info = self._client.publish(
                self._topic,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                qos=self._m.qos,
            )
            info.wait_for_publish(timeout=10)
            return info.is_published()
        except Exception:  # noqa: BLE001 - 실패는 False로, 재시도는 agent가
            return False

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            finally:
                self._client = None
