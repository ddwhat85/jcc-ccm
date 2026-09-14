"""설정 로딩과 검증.

현장마다 바뀌는 값은 전부 config.toml에 있고, 코드는 그대로 둔다.
잘못된 설정(빠진 필드, 잘못된 타입)은 장비에서 조용히 오작동하는 대신
시작하자마자 명확한 에러로 잡아낸다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

try:  # Python 3.11+ 표준 라이브러리. CCM의 Debian이 오래됐으면 tomli로 대체.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class ConfigError(Exception):
    """설정이 유효하지 않을 때. 메시지에 어느 항목이 문제인지 담는다."""


@dataclass
class SensorConfig:
    key: str
    name: str
    driver: str            # "ambient" | "distance" | "modbus"
    unit: str = ""
    enabled: bool = True
    # ambient 전용
    metric: str = ""       # "temp" | "hum" | "all"
    # modbus 전용
    modbus_slave: int = 0
    modbus_register: int = 0
    modbus_type: str = "input"      # "input" | "holding"
    modbus_datatype: str = "uint16"
    scale: float = 1.0
    offset: float = 0.0

    _VALID_DRIVERS = ("ambient", "distance", "modbus")

    def validate(self) -> None:
        if not self.key:
            raise ConfigError("센서에 key가 없습니다.")
        if self.driver not in self._VALID_DRIVERS:
            raise ConfigError(
                f"센서 '{self.key}': driver는 {self._VALID_DRIVERS} 중 하나여야 합니다 "
                f"(현재 '{self.driver}')."
            )
        if self.driver == "ambient" and self.metric not in ("temp", "hum", "all"):
            raise ConfigError(
                f"센서 '{self.key}': ambient 드라이버는 metric이 temp/hum/all 이어야 합니다."
            )
        if self.driver == "modbus":
            if self.modbus_type not in ("input", "holding"):
                raise ConfigError(
                    f"센서 '{self.key}': modbus_type은 input/holding 이어야 합니다."
                )
            if self.modbus_slave <= 0:
                raise ConfigError(
                    f"센서 '{self.key}': modbus_slave 주소를 1 이상으로 지정하세요."
                )


@dataclass
class MqttConfig:
    host: str = ""
    port: int = 8883
    tls: bool = True
    username: str = ""
    password: str = ""
    topic: str = "jcc/ccm/{device_id}/telemetry"
    qos: int = 1


@dataclass
class HttpConfig:
    url: str = ""
    api_key: str = ""
    timeout_seconds: float = 10.0


@dataclass
class ModbusBusConfig:
    port: str = "/dev/ttyS1"
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    timeout_seconds: float = 1.0


@dataclass
class Config:
    device_id: str
    site: str
    panel: str             # 이 CCM이 속한 판넬 ID (여러 CCM이 한 판넬을 나눠 감시)
    panel_name: str        # 판넬 표시 이름 (사람이 읽는 용도)
    interval_seconds: int
    publish_batch: bool
    transport_kind: str            # "mqtt" | "http"
    mqtt: MqttConfig
    http: HttpConfig
    modbus: ModbusBusConfig
    sensors: list[SensorConfig] = field(default_factory=list)
    log_level: str = "INFO"
    log_file: str = ""

    def validate(self) -> None:
        if self.interval_seconds <= 0:
            raise ConfigError("collection.interval_seconds는 1 이상이어야 합니다.")
        if self.transport_kind not in ("mqtt", "http"):
            raise ConfigError("transport.kind는 mqtt 또는 http 여야 합니다.")
        if self.transport_kind == "mqtt" and not self.mqtt.host:
            raise ConfigError("transport.mqtt.host가 비어 있습니다.")
        if self.transport_kind == "http" and not self.http.url:
            raise ConfigError("transport.http.url이 비어 있습니다.")
        enabled = [s for s in self.sensors if s.enabled]
        if not enabled:
            raise ConfigError("활성화된 센서가 하나도 없습니다. config에서 최소 1개는 enabled=true 로 두세요.")
        keys = [s.key for s in enabled]
        dups = {k for k in keys if keys.count(k) > 1}
        if dups:
            raise ConfigError(f"센서 key가 중복됩니다: {sorted(dups)}")
        for s in self.sensors:
            s.validate()


def _resolve_device_id(raw_id: str) -> str:
    """설정에 device.id가 비었으면 CCM 호스트명(ccm-<시리얼>)을 쓴다."""
    if raw_id:
        return raw_id
    host = os.uname().nodename if hasattr(os, "uname") else ""
    return host or "unknown-ccm"


def load(path: str) -> Config:
    """config.toml을 읽어 검증된 Config를 돌려준다. 문제가 있으면 ConfigError."""
    if not os.path.exists(path):
        raise ConfigError(f"설정 파일이 없습니다: {path}")
    with open(path, "rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)

    device = raw.get("device", {})
    collection = raw.get("collection", {})
    transport = raw.get("transport", {})
    mqtt_raw = transport.get("mqtt", {})
    http_raw = transport.get("http", {})
    modbus_raw = raw.get("modbus", {})
    logging_raw = raw.get("logging", {})

    sensors = [
        SensorConfig(
            key=s.get("key", ""),
            name=s.get("name", s.get("key", "")),
            driver=s.get("driver", ""),
            unit=s.get("unit", ""),
            enabled=bool(s.get("enabled", True)),
            metric=s.get("metric", ""),
            modbus_slave=int(s.get("modbus_slave", 0)),
            modbus_register=int(s.get("modbus_register", 0)),
            modbus_type=s.get("modbus_type", "input"),
            modbus_datatype=s.get("modbus_datatype", "uint16"),
            scale=float(s.get("scale", 1.0)),
            offset=float(s.get("offset", 0.0)),
        )
        for s in raw.get("sensors", [])
    ]

    cfg = Config(
        device_id=_resolve_device_id(device.get("id", "")),
        site=device.get("site", ""),
        panel=device.get("panel", ""),
        panel_name=device.get("panel_name", ""),
        interval_seconds=int(collection.get("interval_seconds", 10)),
        publish_batch=bool(collection.get("publish_batch", True)),
        transport_kind=transport.get("kind", "mqtt"),
        mqtt=MqttConfig(
            host=mqtt_raw.get("host", ""),
            port=int(mqtt_raw.get("port", 8883)),
            tls=bool(mqtt_raw.get("tls", True)),
            username=mqtt_raw.get("username", ""),
            password=mqtt_raw.get("password", ""),
            topic=mqtt_raw.get("topic", "jcc/ccm/{device_id}/telemetry"),
            qos=int(mqtt_raw.get("qos", 1)),
        ),
        http=HttpConfig(
            url=http_raw.get("url", ""),
            api_key=http_raw.get("api_key", ""),
            timeout_seconds=float(http_raw.get("timeout_seconds", 10.0)),
        ),
        modbus=ModbusBusConfig(
            port=modbus_raw.get("port", "/dev/ttyS1"),
            baudrate=int(modbus_raw.get("baudrate", 9600)),
            parity=modbus_raw.get("parity", "N"),
            stopbits=int(modbus_raw.get("stopbits", 1)),
            bytesize=int(modbus_raw.get("bytesize", 8)),
            timeout_seconds=float(modbus_raw.get("timeout_seconds", 1.0)),
        ),
        sensors=sensors,
        log_level=logging_raw.get("level", "INFO"),
        log_file=logging_raw.get("file", ""),
    )
    cfg.validate()
    return cfg
