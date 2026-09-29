"""튜닝 시나리오 라이브러리 — 기준값을 시험하는 '시험지'.

사고 6종(놓치면 적용 불가)과 함정 5종(내면 오경보)을 결정적(시드 고정) 시계열로 만든다.
값은 물리적으로 그럴듯하게 정했고, 알고리즘에 맞춰 쉽게/어렵게 고치지 않는다 —
기본값이 어떤 시나리오를 놓치면 그 자체가 발견이다(설계 7절).

⚠ 대시보드에서 편집할 수 없다. 시험지를 고쳐 관문을 통과시키는 일을 막기 위해 코드로만 바꾼다.
실센서 기록이 생기면 같은 형식(build 반환값)으로 추가한다.

형식: build(name) -> {
  name, label, kind("accident"|"trap"), algo("fire"|"contact"|"dew"),
  step(초), t0, t_end, t_ref(사고 기준 시각, 함정은 None), t_start(사건 시작),
  roles: {역할: [[ts, 값], ...]}   (유효 표본만. 끊김 = 표본 없음)
  expect: {"need": [...]} 또는 {"forbid": [...]},  ref_label
}
"""
from __future__ import annotations

import math
import random
import zlib

T0 = 1_760_000_000          # 가상 시작 시각(에포크 초) — 실제 시각 규모로 부동소수 문제도 같이 시험
_A, _B = 17.62, 243.12      # Magnus 계수(dewpoint.py와 동일)


def _rng(name: str) -> random.Random:
    return random.Random(zlib.crc32(name.encode("utf-8")))


def _r2(v: float) -> float:
    return round(v, 2)


def _dew_point(t: float, rh: float) -> float:
    g = math.log(rh / 100.0) + _A * t / (_B + t)
    return _B * g / (_A - g)


def _rh_from(t: float, td: float) -> float:
    """기온 t에서 이슬점 td일 때 상대습도(%) — 절대습도 보존(밤에 식을 때)."""
    return 100.0 * math.exp(_A * td / (_B + td) - _A * t / (_B + t))


def _smooth(x: float) -> float:
    """0→1 부드러운 계단(smoothstep)."""
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _grid(dur: float, step: float):
    n = int(dur // step) + 1
    return [T0 + i * step for i in range(n)]


def _first(ts_list, pred):
    for ts in ts_list:
        if pred(ts):
            return ts
    return None


def _gases_background(rng, ts_list, roles, skip=()):
    """평상시 가스 잡음(센서 기본 출력)."""
    for role, mu, sd in (("h2", 0.5, 0.15), ("voc", 60.0, 8.0), ("co", 3.0, 0.8)):
        if role not in skip:
            roles[role] = [[t, _r2(max(0.0, rng.gauss(mu, sd)))] for t in ts_list]


# ── 사고 ────────────────────────────────────────────────────
def _cable_overheat():
    """케이블 과열 — 피복 열분해로 H2가 먼저, 이어 VOC, 마지막에 온도가 오른다(20분 뒤 시작)."""
    rng, step = _rng("cable_overheat"), 5
    ts_list = _grid(3600, step)
    ts0 = T0 + 1200

    def h2(t):      # 15분에 걸쳐 가속 상승해 35분 무렵 25%LEL(기본 경보치)
        x = (t - ts0) / 900.0
        return 0.5 + (24.5 * (math.exp(3 * x) - 1) / (math.exp(3) - 1) if x > 0 else 0.0)

    def voc(t):     # 4분 늦게, 같은 모양으로 1000ppm까지
        x = (t - ts0 - 240) / 900.0
        return 60.0 + (940.0 * (math.exp(3 * x) - 1) / (math.exp(3) - 1) if x > 0 else 0.0)

    def temp(t):    # 10분 늦게 서서히 → 가팔라짐
        m = (t - ts0 - 600) / 60.0
        return 28.0 + (0.02 * m * m if m > 0 else 0.0)

    roles = {}
    _gases_background(rng, ts_list, roles, skip=("h2", "voc"))
    roles["h2"] = [[t, _r2(max(0.0, h2(t) + rng.gauss(0, 0.15)))] for t in ts_list]
    roles["voc"] = [[t, _r2(max(0.0, voc(t) + rng.gauss(0, 8)))] for t in ts_list]
    roles["ambient"] = [[t, _r2(temp(t) + rng.gauss(0, 0.05))] for t in ts_list]
    t_ref = _first(ts_list, lambda t: h2(t) >= 25.0)
    return dict(label="케이블 과열", kind="accident", algo="fire", step=step, t_start=ts0,
                t_ref=t_ref, ref_label="H2 기본 경보치(25%LEL) 도달",
                roles=roles, expect={"need": ["vent_open"]})


def _co_char():
    """절연물 탄화 — CO만 60분에 걸쳐 서서히 200ppm까지(H2·VOC는 평상)."""
    rng, step = _rng("co_char"), 5
    ts_list = _grid(5400, step)
    ts0 = T0 + 1200

    def co(t):
        x = (t - ts0) / 3600.0
        return 3.0 + 197.0 * min(1.0, x) if x > 0 else 3.0

    roles = {}
    _gases_background(rng, ts_list, roles, skip=("co",))
    roles["co"] = [[t, _r2(max(0.0, co(t) + rng.gauss(0, 0.8)))] for t in ts_list]
    roles["ambient"] = [[t, _r2(27.0 + rng.gauss(0, 0.05))] for t in ts_list]
    t_ref = _first(ts_list, lambda t: co(t) >= 200.0)
    return dict(label="절연물 탄화(CO)", kind="accident", algo="fire", step=step, t_start=ts0,
                t_ref=t_ref, ref_label="CO 기본 경보치(200ppm) 도달",
                roles=roles, expect={"need": ["fire_watch"]})


def _contact_load(rng, t):
    """하루 부하: 낮(6~22시) 8~15A 오르내림, 밤 3A."""
    hour = ((t - T0) / 3600.0) % 24
    if 6 <= hour < 22:
        return 11.5 + 3.5 * math.sin(2 * math.pi * hour / 3.0) + rng.gauss(0, 0.4)
    return 3.0 + rng.gauss(0, 0.2)


def _contact_loosen():
    """접점 풀림 — 이틀은 정상(기준 학습), 이후 19일에 걸쳐 접촉저항이 가속 증가."""
    rng, step = _rng("contact_loosen"), 300
    days = 21
    ts_list = _grid(days * 86400, step)
    ts0 = T0 + 2 * 86400

    def k(t):
        if t <= ts0:
            return 0.03
        x = (t - ts0) / ((days - 2) * 86400.0)
        return 0.03 + (0.142 - 0.03) * x ** 1.5

    cur, amb, ctemp, true_ct = [], [], [], {}
    for t in ts_list:
        hour = ((t - T0) / 3600.0) % 24
        i = max(0.0, _contact_load(rng, t))
        ta = 25.0 + 3.0 * math.sin(2 * math.pi * (hour - 9) / 24.0)
        tc = ta + k(t) * i * i
        true_ct[t] = tc
        cur.append([t, _r2(i)])
        amb.append([t, _r2(ta + rng.gauss(0, 0.1))])
        ctemp.append([t, _r2(tc + rng.gauss(0, 0.3))])
    t_ref = _first(ts_list, lambda t: true_ct[t] >= 60.0)
    return dict(label="접점 풀림(3주)", kind="accident", algo="contact", step=step, t_start=ts0,
                t_ref=t_ref, ref_label="접점 절대 60°C 도달",
                roles={"current": cur, "ambient": amb, "contact_temp": ctemp},
                expect={"need": ["contact_watch"]})


def _contact_series(rng, ts_list, k_of, i_of, tau=180.0):
    """접점 온도 = 함내 + k·I² 를 열 시정수 tau로 따라간다(순간 전류 튐은 온도에 거의 안 남는다)."""
    cur, amb, ctemp = [], [], []
    tc = None
    step = ts_list[1] - ts_list[0]
    a = 1.0 - math.exp(-step / tau)
    for t in ts_list:
        i = max(0.0, i_of(t))
        ta = 26.0 + rng.gauss(0, 0.05)
        eq = ta + k_of(t) * i * i
        tc = eq if tc is None else tc + a * (eq - tc)
        cur.append([t, _r2(i)])
        amb.append([t, _r2(ta)])
        ctemp.append([t, _r2(tc + rng.gauss(0, 0.2))])
    return {"current": cur, "ambient": amb, "contact_temp": ctemp}


def _contact_jump():
    """볼트 이완 — 학습 50분 뒤 접촉저항이 갑자기 4배(k 0.03→0.12)."""
    rng, step = _rng("contact_jump"), 5
    ts_list = _grid(5400, step)
    ts0 = T0 + 3000

    def i_of(t):
        return 12.0 + 2.0 * math.sin(2 * math.pi * (t - T0) / 900.0) + rng.gauss(0, 0.3)

    roles = _contact_series(rng, ts_list, lambda t: 0.12 if t >= ts0 else 0.03, i_of)
    return dict(label="접점 급변(볼트 이완)", kind="accident", algo="contact", step=step, t_start=ts0,
                t_ref=ts0 + 600, ref_label="급변 후 10분",
                roles=roles, expect={"need": ["contact_danger"]})


def _monsoon_dew():
    """장마철 — 함내 26°C, 습도가 4시간에 걸쳐 70→99.5%."""
    rng, step = _rng("monsoon_dew"), 30
    ts_list = _grid(6 * 3600, step)
    ts0 = T0 + 3600

    def rh(t):
        return 70.0 + 29.5 * _smooth((t - ts0) / (4 * 3600.0))

    temp = {t: 26.0 + 0.3 * math.sin(2 * math.pi * (t - T0) / 7200.0) for t in ts_list}
    t_ref = _first(ts_list, lambda t: temp[t] - _dew_point(temp[t], rh(t)) <= 0.3)
    return dict(label="장마철 결로", kind="accident", algo="dew", step=step, t_start=ts0,
                t_ref=t_ref, ref_label="결로 여유 0.3°C 도달",
                roles={"ambient": [[t, _r2(temp[t] + rng.gauss(0, 0.05))] for t in ts_list],
                       "humidity": [[t, _r2(min(100.0, rh(t) + rng.gauss(0, 0.3)))] for t in ts_list]},
                expect={"need": ["heater"]})


def _night_chill():
    """야간 급냉 — 저녁 25°C·75%, 3시간에 걸쳐 15°C까지 식음(수분량 그대로 → 습도 상승)."""
    rng, step = _rng("night_chill"), 30
    ts_list = _grid(6 * 3600, step)
    ts0 = T0 + 3600
    td = _dew_point(25.0, 75.0)

    def temp(t):
        return 25.0 - 10.0 * min(1.0, max(0.0, (t - ts0) / (3 * 3600.0)))

    t_ref = _first(ts_list, lambda t: temp(t) - td <= 0.3)
    return dict(label="야간 급냉 결로", kind="accident", algo="dew", step=step, t_start=ts0,
                t_ref=t_ref, ref_label="결로 여유 0.3°C 도달",
                roles={"ambient": [[t, _r2(temp(t) + rng.gauss(0, 0.05))] for t in ts_list],
                       "humidity": [[t, _r2(min(100.0, _rh_from(temp(t), td) + rng.gauss(0, 0.3)))]
                                    for t in ts_list]},
                expect={"need": ["fan", "heater"]})


# ── 함정 ────────────────────────────────────────────────────
def _spray():
    """청소 스프레이 — 판넬 근처에서 뿌린 알코올 에어로졸. VOC가 10초 만에 1500ppm, 20초 시정수로 빠짐."""
    rng, step = _rng("spray"), 5
    ts_list = _grid(2400, step)
    ts0 = T0 + 1200

    def voc(t):
        d = t - ts0
        if d < 0:
            return 60.0
        if d < 10:
            return 60.0 + 1440.0 * d / 10.0
        return 60.0 + 1440.0 * math.exp(-(d - 10) / 20.0)

    roles = {}
    _gases_background(rng, ts_list, roles, skip=("voc",))
    roles["voc"] = [[t, _r2(max(0.0, voc(t) + rng.gauss(0, 8)))] for t in ts_list]
    roles["ambient"] = [[t, _r2(27.0 + rng.gauss(0, 0.05))] for t in ts_list]
    return dict(label="함정: 청소 스프레이", kind="trap", algo="fire", step=step, t_start=ts0,
                t_ref=None, ref_label="", roles=roles, expect={"forbid": ["fire_danger", "vent_open"]})


def _welding():
    """근처 용접 — 흄으로 CO 120ppm·VOC 350ppm이 1분에 걸쳐 올라 4분 머문 뒤 3분에 걸쳐 빠짐."""
    rng, step = _rng("welding"), 5
    ts_list = _grid(2400, step)
    ts0 = T0 + 1200

    def shape(t):
        d = t - ts0
        if d < 0:
            return 0.0
        if d < 60:
            return d / 60.0
        if d < 300:
            return 1.0
        return max(0.0, 1.0 - (d - 300) / 180.0)

    roles = {}
    _gases_background(rng, ts_list, roles, skip=("co", "voc"))
    roles["co"] = [[t, _r2(max(0.0, 3.0 + 117.0 * shape(t) + rng.gauss(0, 0.8)))] for t in ts_list]
    roles["voc"] = [[t, _r2(max(0.0, 60.0 + 290.0 * shape(t) + rng.gauss(0, 8)))] for t in ts_list]
    roles["ambient"] = [[t, _r2(27.0 + rng.gauss(0, 0.05))] for t in ts_list]
    return dict(label="함정: 근처 용접", kind="trap", algo="fire", step=step, t_start=ts0,
                t_ref=None, ref_label="", roles=roles, expect={"forbid": ["fire_danger", "vent_open"]},
                known="알려진 한계 — 가스만으로는 용접 흄과 열폭주를 구분할 수 없음. 현장 용접 중엔 벤트를 수동 모드로")


def _motor_start():
    """모터 기동 — 학습 뒤 기동전류 60A가 한 표본(5초), 이후 부하 14A로 운전. 접점은 정상."""
    rng, step = _rng("motor_start"), 5
    ts_list = _grid(4800, step)
    ts0 = T0 + 3000

    def i_of(t):
        if ts0 <= t < ts0 + step:
            return 60.0
        return (14.0 if t >= ts0 else 10.0) + rng.gauss(0, 0.3)

    roles = _contact_series(rng, ts_list, lambda t: 0.03, i_of)
    return dict(label="함정: 모터 기동", kind="trap", algo="contact", step=step, t_start=ts0,
                t_ref=None, ref_label="", roles=roles, expect={"forbid": ["contact_danger"]})


def _summer_noon():
    """여름 한낮 — 함내 28→42°C로 8시간에 걸쳐 오르고 습도는 60→35%로 내려감. 가스는 평상."""
    rng, step = _rng("summer_noon"), 30
    ts_list = _grid(8 * 3600, step)

    def x(t):
        return _smooth((t - T0) / (8 * 3600.0))

    roles = {}
    _gases_background(rng, ts_list, roles)
    roles["ambient"] = [[t, _r2(28.0 + 14.0 * x(t) + rng.gauss(0, 0.05))] for t in ts_list]
    roles["humidity"] = [[t, _r2(60.0 - 25.0 * x(t) + rng.gauss(0, 0.3))] for t in ts_list]
    return dict(label="함정: 여름 한낮", kind="trap", algo="fire", step=step, t_start=T0,
                t_ref=None, ref_label="", roles=roles,
                expect={"forbid": ["fire_danger", "vent_open", "dew_danger"]})


def _sensor_glitch():
    """센서 값 튐·끊김 — H2가 3분마다 한 표본씩 30%LEL로 튀고, 25분에 가스 3종이 2분간 끊김."""
    rng, step = _rng("sensor_glitch"), 5
    ts_list = _grid(2400, step)
    roles = {}
    _gases_background(rng, ts_list, roles)
    roles["h2"] = [[t, 30.0 if (t - T0) % 180 == 90 else v] for t, v in roles["h2"]]
    gap = (T0 + 1500, T0 + 1620)
    for g in ("h2", "voc", "co"):
        roles[g] = [p for p in roles[g] if not (gap[0] <= p[0] < gap[1])]
    roles["ambient"] = [[t, _r2(27.0 + rng.gauss(0, 0.05))] for t in ts_list]
    return dict(label="함정: 센서 튐·끊김", kind="trap", algo="fire", step=step, t_start=T0 + 90,
                t_ref=None, ref_label="", roles=roles, expect={"forbid": ["fire_danger", "vent_open"]})


_BUILDERS = {
    "cable_overheat": _cable_overheat, "co_char": _co_char, "contact_loosen": _contact_loosen,
    "contact_jump": _contact_jump, "monsoon_dew": _monsoon_dew, "night_chill": _night_chill,
    "spray": _spray, "welding": _welding, "motor_start": _motor_start,
    "summer_noon": _summer_noon, "sensor_glitch": _sensor_glitch,
}
NAMES = tuple(_BUILDERS)
_cache: dict = {}


def build(name: str) -> dict:
    """시나리오 하나(캐시). 반환값을 고치지 말 것 — 캐시가 공유된다."""
    if name not in _cache:
        d = _BUILDERS[name]()
        ts = sorted({p[0] for pts in d["roles"].values() for p in pts})
        d.update(name=name, t0=ts[0], t_end=ts[-1])
        _cache[name] = d
    return _cache[name]


def export_all() -> list:
    """화면·JS 재생용 JSON(순서 고정)."""
    return [build(n) for n in NAMES]
