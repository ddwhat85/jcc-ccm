"""예지 입력 조립 — 센서 이력에서 세 알고리즘(화재·접점발열·결로)의 입력을 만들고,
지금 판정해도 되는지(준비 상태)를 가린다.

서버(storage, DB 이력)와 엣지(CCM 펌웨어, 메모리 링버퍼)가 **같은 함수**를 쓴다.
이력 접근은 콜백 하나로 추상화한다:

    series(dev, key, n) -> 최근 n개 '읽기' 중 유효 표본만 [(ts, value)] (오래된→최신)

현장 데이터 방어 규칙 — 전부 검증 중 실제로 부딪힌 결함에서 나왔다:
  · 현재값 = 최근 유효 3표본 중앙값 → 단발 글리치(1표본 튐)로 벤트가 열리지 않게
  · 기울기용 시계열 = 3점 이동중앙값 → 튄 값 하나가 최소제곱 기울기를 비틀지 않게
  · 기준선('평소') = 최근 30표본 이전 이력 → 지금 사건이 '평소'를 오염시키지 않게
  · 센서 간 이력은 위치가 아닌 시각으로 짝지음(as-of) → 읽기 실패가 짝을 어긋내지 않게
  · 준비 판정: 6표본·20초 창 미만 = 학습 중 / 보고주기×4(최소 15초) 무소식 = 데이터 끊김
    보류된 알고리즘은 경보·액추에이터 상태를 건드리지 않는다(predict.assess_panel).

순수 함수 — 저장소·네트워크에 의존하지 않는다.
⚠ firmware/jcc_ccm/predict/ 에 같은 파일이 복사돼 있다(tools/sync_edge_core.py).
"""
from __future__ import annotations

ROLES = ("h2", "voc", "current", "contact_temp", "ambient", "humidity", "smoke")

WARMUP = 6          # 판정에 필요한 최소 유효 표본 수
STALE_MIN = 15.0    # '끊김' 한계의 하한(초) — 실제 한계는 max(이 값, 보고간격 중앙값×4)
MIN_SPAN = 20.0     # 기울기 창이 덮어야 할 최소 시간(초). 창이 30표본이라 최단 주기 1초면
                    # 29초까지만 덮으므로 반드시 그보다 작아야 1초 CCM도 판정된다.
SLOPE_N = 30        # 기울기 창(표본 수)


def despike(pts: list) -> list:
    """인과 3점 이동중앙값. 창이 3개로 꽉 찬 지점부터만 낸다(앞 두 점은 튄 값이 샌다)."""
    if len(pts) < 3:
        return pts
    return [(pts[i][0], sorted((pts[i - 2][1], pts[i - 1][1], pts[i][1]))[1])
            for i in range(2, len(pts))]


def asof(base: list, other: list, tol: float = 30.0) -> list:
    """base 각 시각에, 그 시각 이하에서 가장 최근의 other 값(tol초 이내)을 붙인다."""
    out, j = [], 0
    for ts, _ in base:
        while j + 1 < len(other) and other[j + 1][0] <= ts:
            j += 1
        ok = other and other[j][0] <= ts and ts - other[j][0] <= tol
        out.append(other[j][1] if ok else None)
    return out


def current_value(series, dev: str, key: str):
    """최근 '유효' 3표본의 중앙값(읽기 실패가 끼어도 3개를 채우도록 8개를 읽는다).
    유효가 2개뿐이면 낮은 쪽 — 2개의 '중앙값'은 튄 값이 뽑힐 수 있다."""
    vs = sorted(v for _, v in series(dev, key, 8)[-3:])
    if not vs:
        return None
    return vs[0] if len(vs) == 2 else vs[len(vs) // 2]


def readiness(series, pairs: list, now: float):
    """입력 센서들이 판정할 만큼 쌓였고 살아 있는가. None=준비됨, 문자열=보류 사유."""
    ns, spans = [], []
    for dev, key in pairs:
        pts = series(dev, key, SLOPE_N)
        gaps = sorted(b[0] - a[0] for a, b in zip(pts, pts[1:]))
        limit = max(STALE_MIN, 4 * gaps[len(gaps) // 2]) if gaps else STALE_MIN
        if not pts or now - pts[-1][0] > limit:
            return "데이터 끊김 — 판정 보류(상태 유지)"
        ns.append(len(pts))
        spans.append(pts[-1][0] - pts[0][0])
    if not ns:
        return "센서 없음"
    if min(ns) < WARMUP:
        return f"학습 중 {min(ns)}/{WARMUP}표본"
    if min(spans) < MIN_SPAN:
        return f"학습 중 {int(min(spans))}/{int(MIN_SPAN)}초"
    return None


def roles_from_kinds(ccms: list) -> dict:
    """판넬의 CCM 목록(서버 list_devices 형식)에서 역할별 센서를 kind/key로 찾는다.
    엣지는 config.toml의 [predict]에 역할을 명시하므로 이 함수를 쓰지 않는다."""
    def find(pred):
        for d in ccms:
            for s in (d.get("latest") or []):
                if s.get("enabled", True) and pred(s):
                    return (d["device_id"], s["sensor_key"])
        return None

    def key(s):
        return s.get("sensor_key") or ""

    return {
        "h2": find(lambda s: s.get("kind") == "h2"),
        "voc": find(lambda s: s.get("kind") == "voc"),
        "current": find(lambda s: s.get("kind") == "current"),
        "contact_temp": find(lambda s: "ncontact" in key(s)),
        "ambient": find(lambda s: s.get("kind") == "temp" and "ncontact" not in key(s)),
        "humidity": find(lambda s: s.get("kind") == "humidity"),
        "smoke": find(lambda s: s.get("kind") == "smoke"),
    }


def build_panel_inputs(roles: dict, series, now: float) -> dict:
    """역할→(dev,key) 매핑과 이력 콜백으로 assess_panel 입력·보류 사유·대표 노드를 만든다.

    반환 {"inputs": {...}, "pending": {알고리즘: 사유}, "reps": {알고리즘: (dev,key)}}
    """
    r = {k: roles.get(k) for k in ROLES}
    memo: dict = {}

    def val(role):
        if not r[role]:
            return None
        if role not in memo:
            memo[role] = current_value(series, *r[role])
        return memo[role]

    def ser(role, n):
        return despike(series(*r[role], n))

    def base(role):   # 기준선 = 최근 30표본(지금 사건 구간)보다 이전 이력
        return [v for _, v in ser(role, 150)[:-SLOPE_N]]

    inputs: dict = {}
    for gas in ("h2", "voc"):
        if r[gas]:
            inputs[gas] = {"value": val(gas), "series": ser(gas, SLOPE_N), "baseline": base(gas)}
    if r["ambient"]:
        inputs["temp"] = {"value": val("ambient"), "series": ser("ambient", SLOPE_N)}
    inputs["smoke"] = bool(r["smoke"] and (val("smoke") or 0) > 0)

    # 접점 발열: 접점온도 표본 시각을 기준으로 전류·함내온도를 시각 맞춤(as-of)
    if r["current"] and r["contact_temp"] and r["ambient"]:
        ct = ser("contact_temp", 24)
        cur_a = asof(ct, ser("current", 30))
        amb_a = asof(ct, ser("ambient", 30))
        hist = [(ts, i_, t_, a_) for (ts, t_), i_, a_ in zip(ct, cur_a, amb_a)
                if i_ is not None and a_ is not None]
        inputs["contact"] = {"current": val("current"), "temp": val("contact_temp"),
                             "ambient": val("ambient"), "history": hist}

    # 결로: 함내 온·습도 + 표면(최냉점 = 함내온도와 접점온도 중 낮은 값), 습도 시각 기준
    if r["ambient"] and r["humidity"]:
        surf = val("ambient")
        if val("contact_temp") is not None and (surf is None or val("contact_temp") < surf):
            surf = val("contact_temp")
        hum = ser("humidity", 24)
        t_a = asof(hum, ser("ambient", 30))
        dhist = [(ts, t_, rh, t_) for (ts, rh), t_ in zip(hum, t_a) if t_ is not None]
        inputs["dew"] = {"temp": val("ambient"), "rh": val("humidity"), "surface": surf, "history": dhist}

    pending: dict = {}
    for algo, need in (("fire", ("h2", "voc")),
                       ("contact", ("current", "contact_temp", "ambient")),
                       ("dew", ("ambient", "humidity"))):
        pairs = [r[x] for x in need if r[x]]
        if algo != "fire" and len(pairs) < len(need):
            pending[algo] = "센서 없음"          # 이 판넬/CCM엔 필수 입력이 다 있지 않다
            continue
        why = readiness(series, pairs, now)
        if why:
            pending[algo] = why

    # 함내온도는 화재에선 보조항(급상승 확증) — 끊겼으면 그 항만 빼고 가스로 판정한다
    if "temp" in inputs and readiness(series, [r["ambient"]], now) is not None:
        inputs.pop("temp")

    reps = {"fire": r["h2"] or r["voc"], "contact": r["contact_temp"], "dew": r["humidity"]}
    return {"inputs": inputs, "pending": pending, "reps": reps}
