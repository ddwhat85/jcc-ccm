"""HTTP(S) 전송.

MQTT 브로커를 두기 어려운 현장을 위한 대안. 표준 POST + JSON.
requests가 있으면 쓰고, 없으면 표준 라이브러리(urllib)로 떨어진다 —
CCM에 추가 패키지를 최소화하기 위해.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

from ..config import Config
from . import parse_commands, parse_sensor_config, parse_tuning


class HttpTransport:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._h = cfg.http
        self._cmds: list[dict] = []
        self._ota = None      # 서버가 제안한 새 펌웨어(manifest) — OtaManager가 검증·설치
        self._tuning = None   # 서버가 내려보낸 예지 기준 설정 — EdgePredictor가 검증·적용
        self._sensor_cfg = None   # 서버가 내려보낸 수동 센서 설정 — ManualSensors가 검증·적용

    def connect(self) -> None:
        # HTTP는 세션 유지가 필요 없다. 매 전송이 독립적.
        return None

    def send(self, payload: dict) -> bool:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._h.api_key:
            headers["Authorization"] = f"Bearer {self._h.api_key}"
        req = urllib.request.Request(self._h.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._h.timeout_seconds) as resp:
                ok = 200 <= resp.status < 300
                if ok:
                    # 응답에 실려 온 명령(대시보드 수동 조작) — 보고 주기마다 받아가는 하향 채널
                    try:
                        obj = json.loads(resp.read().decode("utf-8") or "{}")
                        self._cmds.extend(parse_commands(obj))
                        if isinstance(obj, dict) and isinstance(obj.get("ota"), dict):
                            self._ota = obj["ota"]
                        self._tuning = parse_tuning(obj) or self._tuning
                        self._sensor_cfg = parse_sensor_config(obj) or self._sensor_cfg
                    except (ValueError, UnicodeDecodeError):
                        pass
                return ok
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return False
        except Exception:  # noqa: BLE001
            return False

    def take_commands(self) -> list[dict]:
        out, self._cmds = self._cmds, []
        return out

    def take_ota(self):
        out, self._ota = self._ota, None
        return out

    def take_tuning(self):
        out, self._tuning = self._tuning, None
        return out

    def take_sensor_config(self):
        out, self._sensor_cfg = self._sensor_cfg, None
        return out

    def close(self) -> None:
        return None
