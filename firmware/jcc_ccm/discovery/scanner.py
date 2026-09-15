"""자동 탐색 에이전트.

CCM에서 버스를 훑어 물려 있는 센서를 찾아내고, profiles로 정체를 식별해
'발견 인벤토리'를 만든다. 이것이 노드 모니터의 [AI 자동연결]이 부르는 실체다.

- 실기(하드웨어): scan_modbus()가 RS485 슬레이브 주소를 훑고, 각 응답 장비의
  모델 식별(0x2B/0x0E)을 읽어 지문을 만든다 + 내장 센서를 추가한다.
- 시뮬레이션: VirtualBus가 실제 배선을 흉내 낸 지문 목록을 돌려준다.

두 경로 모두 같은 identify_inventory()를 통과하므로, 실기에서는 버스만 갈아끼우면 된다.
"""
from __future__ import annotations

from . import profiles


# 지문 → 사람이 읽는 벤더/모델 이름 (source 라벨용)
_IDENT_LABEL = {
    "TURCK-CCM-AMBIENT": "내장 온습도",
    "TURCK-CCM-DOOR": "내장 거리센서",
    "BANNER-QM30VT": "Banner QM30VT",
    "BANNER-CT20A": "Banner CT20A",
    "BANNER-S15S-T": "Banner S15S-T",
    "INFRASENSING-H2": "InfraSensing H2",
    "ONOFF-HSD200": "온오프 HSD200",
}


def _source_label(raw: dict) -> str:
    ident = raw.get("ident", "")
    name = _IDENT_LABEL.get(ident, ident or "미상")
    if raw.get("source") == "builtin":
        return "내장 · " + name
    addr = raw.get("address")
    return f"Modbus #{addr} · {name}" if addr is not None else name


def identify_inventory(raw_devices: list[dict]) -> list[dict]:
    """버스에서 얻은 원시 지문 목록을 식별된 센서 목록으로 바꾼다.

    한 장비가 여러 채널을 낼 수 있으므로 결과 수 >= 입력 수일 수 있다.
    매칭되면 confidence='확정', 아니면 infer()로 '추정'.
    """
    sensors: list[dict] = []
    for raw in raw_devices:
        prof = profiles.identify(raw.get("ident", ""))
        src = _source_label(raw)
        if prof:
            # 프로파일 레벨 제품정보(브랜드·제품명·품번·매뉴얼)를 각 채널에 붙인다.
            meta = {"brand": prof["brand"], "product": prof["product"],
                    "part_no": prof["part_no"], "manual": prof["manual"]}
            for emit in prof["emits"]:
                sensors.append({**emit, **meta, "source": src, "confidence": "확정",
                                "address": raw.get("address")})
        else:
            guess = profiles.infer(raw)  # infer가 이미 brand/product/part_no/manual 포함
            sensors.append({**guess, "source": _source_label(raw), "confidence": "추정",
                            "address": raw.get("address")})
    return sensors


# ── 실기 경로 (하드웨어에서만 동작) ─────────────────────────
def scan_modbus(bus, addr_range=range(1, 33)) -> list[dict]:
    """RS485 슬레이브 주소를 훑어 응답하는 장비의 지문을 만든다.

    pymodbus의 장치 식별로 모델 문자열을 읽는다. 미구현/미지원 장비는 프로브
    레지스터 값만 담아 infer로 넘긴다. (실기 검증 전까지는 시뮬레이션을 쓴다.)
    """
    found: list[dict] = []
    for addr in addr_range:
        try:
            ident = _read_device_ident(bus, addr)      # 모델 문자열 또는 None
            probe = _read_probe(bus, addr)             # 대표 레지스터 값 또는 None
        except Exception:  # noqa: BLE001 - 응답 없는 주소는 건너뛴다
            continue
        if ident is None and probe is None:
            continue
        found.append({"source": "modbus", "address": addr,
                      "ident": ident or "UNKNOWN", "probe": probe})
    return found


def _read_device_ident(bus, addr):  # pragma: no cover - 하드웨어 전용
    """Modbus Read Device Identification(0x2B/0x0E)로 모델 문자열을 읽는다."""
    try:
        from pymodbus.mei_message import ReadDeviceInformationRequest
        client = bus._ensure_client()
        rr = client.execute(ReadDeviceInformationRequest(slave=addr))
        if rr and getattr(rr, "information", None):
            # 보통 0=Vendor,1=Product,2=Version. 프로젝트 식별 규약에 맞게 매핑.
            info = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in rr.information.items()}
            return info.get(1) or info.get(0)
    except Exception:  # noqa: BLE001
        return None
    return None


def _read_probe(bus, addr):  # pragma: no cover - 하드웨어 전용
    try:
        regs = bus.read_registers(slave=addr, address=0, count=1, kind="input")
        return float(regs[0]) if regs else None
    except Exception:  # noqa: BLE001
        return None


# ── 시뮬레이션 경로 (하드웨어 없이 전 과정 시연) ─────────────
class VirtualBus:
    """실제 배선을 흉내 낸 가상 CCM. 내장 센서 + RS485에 물린 장비 지문을 돌려준다."""

    def __init__(self, raw_devices: list[dict]):
        self._raw = raw_devices

    def scan(self) -> list[dict]:
        return list(self._raw)


# 시뮬레이션 시나리오: 스마트 판넬 하나를 CCM 4대가 나눠 감시.
#
# 한 판넬에 CCM이 여러 대 붙는 이유: CCM 1대는 윗면에 RS485/CAN 버스 1개 + 이더넷
# 1개만 있어 센서를 소수만 수용한다. 그래서 센서가 늘면 CCM을 추가하고, 같은 판넬에
# 속한 CCM끼리는 panel 값을 공유해 화면에서 판넬 1호 아래로 모인다.
# CCM을 더 붙이면 여기에 한 줄 추가하면 되고, 화면·집계는 자동으로 따라간다.
_SIM_PANEL = {
    "panel": "panel-01",
    "panel_name": "스마트 판넬",
    "site": "인터배터리 데모",
    "ccms": [
        # 가스·전류 계통
        {"device_id": "ccm-2661", "bus": [
            {"source": "modbus",  "ident": "INFRASENSING-H2",   "address": 1},
            {"source": "modbus",  "ident": "BANNER-CT20A",      "address": 2},
            {"source": "modbus",  "ident": "UNKNOWN", "address": 5, "probe": 41.5},  # 추정 시연
        ]},
        # 함내 환경·도어
        {"device_id": "ccm-2662", "bus": [
            {"source": "builtin", "ident": "TURCK-CCM-AMBIENT", "address": None},
            {"source": "builtin", "ident": "TURCK-CCM-DOOR",    "address": None},
        ]},
        # 진동(회전체)
        {"device_id": "ccm-2663", "bus": [
            {"source": "builtin", "ident": "TURCK-CCM-AMBIENT", "address": None},
            {"source": "modbus",  "ident": "BANNER-QM30VT",     "address": 3},
        ]},
        # 화재·접점 발열
        {"device_id": "ccm-2664", "bus": [
            {"source": "modbus",  "ident": "ONOFF-HSD200",      "address": 4},
            {"source": "modbus",  "ident": "BANNER-S15S-T",     "address": 6},
        ]},
    ],
}


def discover_sim() -> dict:
    """시뮬레이션 자동 탐색. 실기의 CCM들이 보고할 '식별된 인벤토리'와 같은 형태."""
    ccms = []
    for c in _SIM_PANEL["ccms"]:
        raw = VirtualBus(c["bus"]).scan()
        ccms.append({"device_id": c["device_id"], "sensors": identify_inventory(raw)})
    return {
        "panel": _SIM_PANEL["panel"],
        "panel_name": _SIM_PANEL["panel_name"],
        "site": _SIM_PANEL["site"],
        "ccms": ccms,
    }
