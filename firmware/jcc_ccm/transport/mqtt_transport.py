"""MQTT 전송 (paho-mqtt).

MQTT는 IoT 텔레메트리의 사실상 표준이다. 경량이고, TLS를 지원하며,
브로커가 재연결·QoS를 처리해줘 불안정한 현장 회선에 강하다.
"""
from __future__ import annotations

import json
import threading

from ..config import Config
from . import parse_commands, parse_sensor_config, parse_tuning

# 하향 명령 토픽(대시보드 수동 조작 → 이 CCM). 브로커 쪽에서 서버가 발행한다.
CMD_TOPIC = "jcc/ccm/{device_id}/cmd"


class MqttTransport:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._m = cfg.mqtt
        self._topic = self._m.topic.replace("{device_id}", cfg.device_id)
        self._cmd_topic = CMD_TOPIC.replace("{device_id}", cfg.device_id)
        self._client = None
        self._connected = False
        self._cmds: list[dict] = []
        self._cmd_lock = threading.Lock()   # paho 콜백 스레드 ↔ 에이전트 루프
        self._tuning = None                 # 하향 예지 기준 설정(같은 cmd 토픽)
        self._sensor_cfg = None             # 하향 수동 센서 설정(같은 cmd 토픽)

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
            if self._connected:   # 재연결 때마다 다시 구독(clean_session)
                client.subscribe(self._cmd_topic, qos=1)

        def _on_disconnect(client, userdata, rc):  # noqa: ANN001
            self._connected = False

        def _on_message(client, userdata, msg):  # noqa: ANN001
            try:
                obj = json.loads(msg.payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            cmds, tun, scfg = parse_commands(obj), parse_tuning(obj), parse_sensor_config(obj)
            with self._cmd_lock:
                self._cmds.extend(cmds)
                if tun:
                    self._tuning = tun
                if scfg:
                    self._sensor_cfg = scfg

        self._client.on_connect = _on_connect
        self._client.on_disconnect = _on_disconnect
        self._client.on_message = _on_message
        self._client.connect(self._m.host, self._m.port, keepalive=60)
        self._client.loop_start()  # 백그라운드 스레드가 재연결·핑 처리

    def send(self, payload: dict) -> bool:
        # 아직 브로커에 붙지 못했으면 10초씩 기다리지 말고 바로 실패 반환한다.
        # (agent가 큐에 쌓아두고 다음 주기에 재시도한다.)
        if self._client is None or not self._connected:
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

    def take_commands(self) -> list[dict]:
        with self._cmd_lock:
            out, self._cmds = self._cmds, []
        return out

    def take_tuning(self):
        with self._cmd_lock:
            out, self._tuning = self._tuning, None
        return out

    def take_sensor_config(self):
        with self._cmd_lock:
            out, self._sensor_cfg = self._sensor_cfg, None
        return out

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            finally:
                self._client = None
