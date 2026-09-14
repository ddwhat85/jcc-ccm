"""HTTP 앱 — 라우팅과 엔드포인트.

표준 라이브러리 http.server 위에 얇은 라우터를 얹었다. ThreadingHTTPServer로
여러 CCM이 동시에 POST해도 처리한다.

엔드포인트
  POST /v1/telemetry             펌웨어가 보내는 수집 데이터 (http_transport와 짝)
  GET  /api/devices              CCM 목록 + 각 센서 최신값
  GET  /api/panels               판넬 단위로 묶은 목록 (판넬 1개 = CCM 여러 대)
  GET  /api/devices/{id}/history?sensor=KEY&limit=N   센서 이력
  GET  /health                   상태 확인
  GET  /                         대시보드 (static/index.html)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .storage import Storage

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# 자동 탐색은 펌웨어(jcc_ccm.discovery)의 실제 코드를 재사용한다. 같은 저장소의
# firmware 패키지를 경로에 얹어, 실기 CCM이 돌릴 코드와 동일한 것을 시뮬레이션한다.
_FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "firmware")


def _run_discovery() -> dict:
    if _FIRMWARE_DIR not in sys.path:
        sys.path.insert(0, _FIRMWARE_DIR)
    from jcc_ccm.discovery import discover_sim
    return discover_sim()

# 선택적 인증: 환경변수 JCC_API_KEY가 설정되면 POST에 Bearer 토큰을 요구한다.
_API_KEY = os.environ.get("JCC_API_KEY", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "JCC-CCM/0.1"
    storage: Storage  # 서버 생성 시 주입

    # ── 공통 응답 헬퍼 ──────────────────────────────────────
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: ANN001 - 조용한 접근 로그
        pass

    # ── GET ────────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._serve_dashboard()
        if path == "/health":
            return self._json({"ok": True, "ts": time.time()})
        if path == "/api/devices":
            return self._json({"devices": self.storage.list_devices()})
        if path == "/api/panels":
            return self._json({"panels": self.storage.list_panels()})

        m = re.fullmatch(r"/api/devices/([^/]+)/history", path)
        if m:
            device_id = m.group(1)
            q = parse_qs(parsed.query)
            sensor = (q.get("sensor") or [""])[0]
            if not sensor:
                return self._json({"error": "sensor 파라미터가 필요합니다"}, 400)
            try:
                limit = int((q.get("limit") or ["200"])[0])
            except ValueError:
                return self._json({"error": "limit은 정수여야 합니다"}, 400)
            return self._json({
                "device_id": device_id,
                "sensor": sensor,
                "points": self.storage.history(device_id, sensor, limit),
            })

        self._json({"error": "not found"}, 404)

    # ── POST ───────────────────────────────────────────────
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/discover":
            return self._discover()
        if parsed.path == "/api/channel":
            return self._channel()
        if parsed.path == "/api/setting":
            return self._setting()
        if parsed.path == "/api/channels/enable-all":
            n = self.storage.enable_all_channels()
            return self._json({"ok": True, "enabled": n})
        if parsed.path != "/v1/telemetry":
            return self._json({"error": "not found"}, 404)

        if _API_KEY:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {_API_KEY}":
                return self._json({"error": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 1_000_000:
            return self._json({"error": "빈 요청이거나 너무 큼"}, 400)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        if not isinstance(payload, dict) or "device_id" not in payload:
            return self._json({"error": "device_id가 필요합니다"}, 400)

        try:
            n = self.storage.ingest(payload)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"저장 실패: {exc}"}, 500)
        return self._json({"ok": True, "stored": n})

    # ── 자동 탐색 (AI 자동연결) ──────────────────────────────
    def _discover(self) -> None:
        """펌웨어의 탐색 로직을 실행해 토폴로지를 자동 구성한다.

        실기에서는 각 CCM이 자기 버스를 스캔해 결과를 올린다. 여기(시뮬레이션)에서는
        서버가 같은 펌웨어 코드(jcc_ccm.discovery)를 VirtualBus로 실행한다.
        """
        try:
            result = _run_discovery()
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"탐색 실패: {exc}"}, 500)
        try:
            self.storage.set_discovery(result)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"저장 실패: {exc}"}, 500)

        ccms = result.get("ccms") or []
        sensors = [s for c in ccms for s in (c.get("sensors") or [])]
        inferred = sum(1 for s in sensors if s.get("confidence") == "추정")
        return self._json({
            "ok": True,
            "panel_name": result.get("panel_name", ""),
            "ccms": len(ccms),
            "sensors": len(sensors),
            "identified": len(sensors) - inferred,
            "inferred": inferred,
        })

    # ── 센서 채널 활성/비활성 (CCM에 보내는 명령) ────────────
    def _channel(self) -> None:
        """{device_id, sensor_key, enabled} — 센서 채널을 켜고/끈다.

        실기에서는 CCM에 명령이 전달돼 그 채널의 폴링을 멈춘다. 시뮬레이션에서는
        서버가 상태를 기록하고 그 채널의 텔레메트리를 버려, SW와 HW를 일치시킨다.
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        dev = str(body.get("device_id", ""))
        key = str(body.get("sensor_key", ""))
        enabled = bool(body.get("enabled", True))
        if not dev or not key:
            return self._json({"error": "device_id와 sensor_key가 필요합니다"}, 400)
        ok = self.storage.set_channel(dev, key, enabled)
        return self._json({"ok": ok, "device_id": dev, "sensor_key": key, "enabled": enabled})

    # ── 센서 설정 (셋팅값·알람 상/하한) ──────────────────────
    def _setting(self) -> None:
        """{device_id, sensor_key, setpoint?, alarm_min?, alarm_max?} — 사용자 설정 저장.

        넘어온 필드만 갱신한다(누락=변경없음, ""=기본값으로 해제). 재탐색해도 유지된다.
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100_000:
            return self._json({"error": "빈 요청"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "잘못된 JSON"}, 400)
        dev = str(body.get("device_id", ""))
        key = str(body.get("sensor_key", ""))
        if not dev or not key:
            return self._json({"error": "device_id와 sensor_key가 필요합니다"}, 400)
        new = self.storage.set_setting(
            dev, key,
            setpoint=body.get("setpoint"),
            alarm_min=body.get("alarm_min"),
            alarm_max=body.get("alarm_max"),
        )
        return self._json({"ok": True, "device_id": dev, "sensor_key": key, **new})

    # ── 대시보드 ────────────────────────────────────────────
    def _serve_dashboard(self) -> None:
        index = os.path.join(_STATIC_DIR, "index.html")
        try:
            with open(index, "r", encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            return self._text("대시보드 파일(static/index.html)이 없습니다.", 500)
        self._text(html, 200, "text/html; charset=utf-8")


def make_server(host: str, port: int, storage: Storage) -> ThreadingHTTPServer:
    # 핸들러 클래스에 storage를 붙여 요청마다 공유하게 한다.
    handler = type("BoundHandler", (Handler,), {"storage": storage})
    return ThreadingHTTPServer((host, port), handler)
