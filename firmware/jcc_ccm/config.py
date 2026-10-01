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
    # modbus_tcp 전용(이더넷 Modbus 장비)
    modbus_host: str = ""
    modbus_port: int = 502

    _VALID_DRIVERS = ("ambient", "distance", "modbus", "modbus_tcp")

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
        if self.driver == "modbus_tcp":
            if not self.modbus_host:
                raise ConfigError(f"센서 '{self.key}': modbus_tcp는 modbus_host(IP)가 필요합니다.")
            if self.modbus_type not in ("input", "holding"):
                raise ConfigError(f"센서 '{self.key}': modbus_type은 input/holding 이어야 합니다.")
            if not 1 <= int(self.modbus_port) <= 65535:
                raise ConfigError(f"센서 '{self.key}': modbus_port는 1~65535여야 합니다.")


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


_ROLES = ("h2", "voc", "co", "current", "contact_temp", "ambient", "humidity", "smoke")  # predict/inputs.ROLES


@dataclass
class PredictConfig:
    """엣지 예지(화재·접점발열·결로). 서버 없이 CCM이 직접 판정해 벤트·히터·팬을 몬다."""
    enabled: bool = False
    # 역할 → 이 CCM 센서 key. 이 CCM에 없는 역할은 비워둔다(그 알고리즘은 '센서 없음'으로
    # 쉬고, 판넬 전체 판정은 서버가 CCM 여러 대의 데이터를 모아 한다).
    roles: dict = field(default_factory=dict)
    autovent: bool = True               # False면 판정·보고만 하고 벤트는 자동으로 안 연다
    failsafe_after_seconds: float = 60.0  # 가스 입력이 이만큼 끊기면 벤트를 fail-safe 상태로
    # 학습 상태(접점 발열 기준) 저장 파일 — 재부팅해도 기준 유지. 비우면 저장 안 함(메모리만)
    state_file: str = "/opt/jcc-ccm/predict_state.json"


@dataclass
class OtaConfig:
    """원격 업데이트. 켜면 서버가 제안한 새 버전을 JCC 공개키로 서명 확인 후 설치한다."""
    enabled: bool = False
    public_key: str = ""                # JCC OTA 공개키(64자리 hex) — tools/ota_keygen.py가 출력
    base_dir: str = "/opt/jcc-ccm"      # releases/·current·ota/ 가 놓이는 곳(런처와 같아야 함)


@dataclass
class ActuatorConfig:
    """출력(릴레이). 벤트·히터·팬 각 1개."""
    kind: str                     # "vent" | "heater" | "fan"
    driver: str = "log"           # "modbus_coil" | "log"(실기 없이 로그만 — 시운전·시뮬레이션)
    modbus_slave: int = 0         # modbus_coil: 릴레이 모듈 슬레이브 주소
    modbus_coil: int = 0          # modbus_coil: 코일 번호
    invert: bool = False          # 켜짐=코일 OFF 로 배선했을 때(NC 접점 등)
    failsafe: str = ""            # 가스 입력 장시간 끊김 시: vent="open"(기본)/"hold"
    # 작동 확인(피드백) — 명령대로 됐는지 되읽는다. ""=드라이버 기본(modbus_coil→coil, log→none)
    feedback: str = ""            # "none" | "coil"(릴레이 되읽기) | "switch"(위치 스위치 = 실제 열림 확인)
    feedback_slave: int = -1     # switch: 스위치가 물린 모듈 슬레이브(-1 = 릴레이와 같은 모듈)
    feedback_input: int = -1     # switch: 입력(discrete input) 번호
    feedback_invert: bool = False  # switch: 열림일 때 입력이 꺼지는 배선
    travel_seconds: float = 0.0  # 명령 후 이 시간 안에 맞아야 함(0 = 벤트 30초, 히터·팬 3초)
    retries: int = 2              # 안 맞으면 다시 써 보는 횟수 → 그래도 안 되면 '작동 실패'
    check_every: float = 30.0     # 평소 되읽기 주기(초) — 모듈 재부팅으로 풀린 릴레이 감지

    _KINDS = ("vent", "heater", "fan")
    _DRIVERS = ("modbus_coil", "log")
    _FEEDBACK = ("", "none", "coil", "switch")

    def feedback_mode(self) -> str:
        if self.feedback:
            return self.feedback
        return "coil" if self.driver == "modbus_coil" else "none"

    def travel(self) -> float:
        return float(self.travel_seconds) if self.travel_seconds > 0 else (30.0 if self.kind == "vent" else 3.0)

    def validate(self) -> None:
        if self.kind not in self._KINDS:
            raise ConfigError(f"actuators: kind는 {self._KINDS} 중 하나여야 합니다 (현재 '{self.kind}').")
        if self.driver not in self._DRIVERS:
            raise ConfigError(f"actuator '{self.kind}': driver는 {self._DRIVERS} 중 하나여야 합니다.")
        if self.driver == "modbus_coil" and self.modbus_slave <= 0:
            raise ConfigError(f"actuator '{self.kind}': modbus_slave 주소를 1 이상으로 지정하세요.")
        if self.kind == "vent":
            if self.failsafe == "":
                self.failsafe = "open"            # 가스를 못 보면 환기하는 쪽이 안전측
            if self.failsafe not in ("open", "hold"):
                raise ConfigError("actuator 'vent': failsafe는 open 또는 hold 여야 합니다.")
        elif self.failsafe not in ("", "hold"):
            raise ConfigError(f"actuator '{self.kind}': failsafe는 hold만 지원합니다(비워두면 hold).")
        if self.feedback not in self._FEEDBACK:
            raise ConfigError(f"actuator '{self.kind}': feedback은 none·coil·switch 중 하나여야 합니다.")
        if self.feedback == "switch" and self.driver == "modbus_coil" and self.feedback_input < 0:
            raise ConfigError(f"actuator '{self.kind}': feedback switch면 feedback_input(입력 번호)을 지정하세요.")
        if not 0 <= self.travel_seconds <= 600:
            raise ConfigError(f"actuator '{self.kind}': travel_seconds는 0~600초여야 합니다.")
        if not 0 <= self.retries <= 5:
            raise ConfigError(f"actuator '{self.kind}': retries는 0~5회여야 합니다.")
        if not 5 <= self.check_every <= 600:
            raise ConfigError(f"actuator '{self.kind}': check_every는 5~600초여야 합니다.")


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
    predict: PredictConfig = field(default_factory=PredictConfig)
    actuators: list[ActuatorConfig] = field(default_factory=list)
    ota: OtaConfig = field(default_factory=OtaConfig)

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
        # 엣지 예지: 역할은 켜진 센서를 가리켜야 하고, 출력은 종류별 1개.
        # 꺼져 있으면(배선 전 예시 등) 역할은 검사하지 않는다 — 안 쓰는 기능이 부팅을 막으면 안 된다.
        for role, key in (self.predict.roles.items() if self.predict.enabled else ()):
            if role not in _ROLES:
                raise ConfigError(f"predict.roles: 알 수 없는 역할 '{role}' (가능: {', '.join(_ROLES)})")
            if key not in keys:
                raise ConfigError(f"predict.roles.{role} = '{key}': 켜진 센서 중에 그 key가 없습니다.")
        if self.predict.failsafe_after_seconds <= 0:
            raise ConfigError("predict.failsafe_after_seconds는 0보다 커야 합니다.")
        kinds = [a.kind for a in self.actuators]
        dup_k = {k for k in kinds if kinds.count(k) > 1}
        if dup_k:
            raise ConfigError(f"actuators: 같은 종류가 중복됩니다: {sorted(dup_k)}")
        for a in self.actuators:
            a.validate()
        if self.ota.enabled:
            try:
                ok = len(bytes.fromhex(self.ota.public_key)) == 32
            except ValueError:
                ok = False
            if not ok:
                raise ConfigError("ota.public_key는 64자리 hex(Ed25519 공개키)여야 합니다 — tools/ota_keygen.py 출력값")
            if self.transport_kind != "http":
                raise ConfigError("OTA는 현재 HTTP 전송에서만 동작합니다(transport.kind = \"http\").")
        if self.actuators and not self.predict.enabled:
            raise ConfigError("actuators가 있는데 predict.enabled=false 입니다 — 출력을 쓰려면 predict를 켜세요.")


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
    predict_raw = raw.get("predict", {})
    ota_raw = raw.get("ota", {})

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
            modbus_host=str(s.get("modbus_host", "")),
            modbus_port=int(s.get("modbus_port", 502)),
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
        predict=PredictConfig(
            enabled=bool(predict_raw.get("enabled", False)),
            roles={str(k): str(v) for k, v in (predict_raw.get("roles") or {}).items() if v},
            autovent=bool(predict_raw.get("autovent", True)),
            failsafe_after_seconds=float(predict_raw.get("failsafe_after_seconds", 60.0)),
            state_file=str(predict_raw.get("state_file", "/opt/jcc-ccm/predict_state.json")),
        ),
        actuators=[
            ActuatorConfig(
                kind=a.get("kind", ""),
                driver=a.get("driver", "log"),
                modbus_slave=int(a.get("modbus_slave", 0)),
                modbus_coil=int(a.get("modbus_coil", 0)),
                invert=bool(a.get("invert", False)),
                failsafe=str(a.get("failsafe", "")),
                feedback=str(a.get("feedback", "")),
                feedback_slave=int(a.get("feedback_slave", -1)),
                feedback_input=int(a.get("feedback_input", -1)),
                feedback_invert=bool(a.get("feedback_invert", False)),
                travel_seconds=float(a.get("travel_seconds", 0)),
                retries=int(a.get("retries", 2)),
                check_every=float(a.get("check_every", 30)),
            )
            for a in raw.get("actuators", [])
        ],
        ota=OtaConfig(
            enabled=bool(ota_raw.get("enabled", False)),
            public_key=str(ota_raw.get("public_key", "")).strip(),
            base_dir=str(ota_raw.get("base_dir", "/opt/jcc-ccm")),
        ),
    )
    cfg.validate()
    return cfg
