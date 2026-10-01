"""수동 센서 — 자동 탐색에 안 잡혀 대시보드에서 사람이 직접 지정한 센서를 서버에서 받아 적용한다.

서버는 이 CCM의 수동 센서 전체 목록을 버전과 함께 내려보낸다({version, sensors:[...]}).
CCM은 서버와 같은 규칙(predict/sensor_spec.py)으로 다시 검사하고, 통과한 것만 드라이버로 만들어
다음 주기부터 읽는다. 목록은 파일에 저장해 재부팅해도 유지하고, 결과를 보고에 실어 서버가
'적용됨/거부 이유'를 화면에 보여 준다. 설정 파일(config.toml)의 센서는 건드리지 않는다.
"""
from __future__ import annotations

import json
import logging
import os

from .config import Config, ConfigError, SensorConfig
from .predict.sensor_spec import clean_list

log = logging.getLogger("jcc_ccm.manual")


def manual_path(cfg: Config) -> str:
    """학습 상태 파일 옆(기본 /opt/jcc-ccm/manual_sensors.json)."""
    base = os.path.dirname(cfg.predict.state_file or "") or "."
    return os.path.join(base, "manual_sensors.json")


def to_config(spec: dict) -> SensorConfig:
    sc = SensorConfig(key=spec["key"], name=spec["name"], unit=spec["unit"], driver=spec["driver"],
                      modbus_slave=spec["slave"], modbus_register=spec["register"], modbus_type=spec["type"],
                      modbus_datatype=spec["datatype"], scale=spec["scale"], offset=spec["offset"],
                      modbus_host=spec.get("host", ""), modbus_port=spec.get("port", 502))
    sc.validate()
    return sc


class ManualSensors:
    def __init__(self, path: str, base_keys=()):
        self.path = path
        self.base_keys = set(base_keys)
        self.version = 0
        self.specs: list = []
        self.status = ""            # ok | partial | error | "" (받은 적 없음)
        self.errors: dict = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            ok, _ = clean_list(d.get("sensors"))
            self.version = int(d.get("version") or 0)
            self.specs = [s for s in ok if s["key"] not in self.base_keys]
            self.status = "ok"
        except (OSError, ValueError, TypeError) as exc:
            log.warning("수동 센서 파일을 읽지 못함(무시하고 시작): %s", exc)

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": self.version, "sensors": self.specs}, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("수동 센서 저장 실패(메모리엔 적용됨): %s", exc)

    def configs(self) -> list:
        out = []
        for s in self.specs:
            try:
                out.append(to_config(s))
            except (ConfigError, KeyError, ValueError) as exc:
                log.warning("수동 센서 %s 건너뜀: %s", s.get("key"), exc)
        return out

    def apply(self, msg) -> bool:
        """서버 설정을 적용한다. 바뀌었으면 True(드라이버를 다시 만들어야 함)."""
        if not isinstance(msg, dict) or not isinstance(msg.get("version"), int):
            return False
        if msg["version"] == self.version:
            return False
        ok, errors = clean_list(msg.get("sensors"))
        good = []
        for s in ok:
            if s["key"] in self.base_keys:
                errors[s["key"]] = "CCM 설정 파일의 센서와 키가 겹칩니다"
                continue
            try:
                to_config(s)
            except (ConfigError, ValueError) as exc:
                errors[s["key"]] = str(exc)
                continue
            good.append(s)
        self.version, self.specs, self.errors = msg["version"], good, errors
        self.status = "ok" if not errors else ("partial" if good else "error")
        self._save()
        log.info("수동 센서 v%d 적용: %d개%s", self.version, len(good),
                 f", 거부 {len(errors)}개" if errors else "")
        return True

    def report(self) -> dict:
        return {"version": self.version, "status": self.status, "active": [s["key"] for s in self.specs],
                "errors": dict(self.errors)}
