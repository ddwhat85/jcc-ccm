"""화재 예지 FRI 코어 시나리오 테스트 (pytest 없이 실행: python tests/test_fire_risk.py)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jcc_server.fire_risk import assess, FireConfig, VentController, slope_per_min, robust_z

def lin(start, end, secs=60, n=13, t0=1000.0):
    """start→end로 secs초 동안 선형 증가하는 (ts,val) 표본 n개."""
    return [(t0 + secs*i/(n-1), start + (end-start)*i/(n-1)) for i in range(n)]
def flat(val, secs=60, n=13, t0=1000.0):
    return [(t0 + secs*i/(n-1), val) for i in range(n)]
def noisy(base, n=40):
    import random; r=random.Random(1); return [round(base*(1+r.uniform(-0.15,0.15))+r.uniform(-0.05,0.05),3) for _ in range(n)]

F=[]
def ck(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra); (F.append(name) if not cond else None)

cfg=FireConfig()

# 1) 정상: 낮고 안정 → normal, 벤트 안 열림
a=assess({"h2":{"value":0.4,"series":flat(0.4),"baseline":[0.4]*20},
          "voc":{"value":30,"series":flat(30),"baseline":[30]*20}}, cfg)
ck("정상=normal", a.stage=="normal" and a.fri<30, f"fri={a.fri}")

# 2) H2만 서서히 상승(경고 접근) → watch, 벤트 안 열림
a=assess({"h2":{"value":9,"series":lin(1,9),"baseline":noisy(0.5)},
          "voc":{"value":40,"series":flat(40),"baseline":noisy(40)}}, cfg)
ck("H2 단독=watch(경고)", a.stage=="watch", f"stage={a.stage} fri={a.fri} {a.reasons}")
ck("H2 단독은 danger 아님(동반 아님)", a.stage!="danger", "")

# 3) H2+VOC 동반 급상승(값도 상승) → danger, FRI 높음
a=assess({"h2":{"value":18,"series":lin(2,18),"baseline":noisy(2)},
          "voc":{"value":600,"series":lin(50,600),"baseline":noisy(50)}}, cfg)
ck("H2+VOC 동반=danger↑", a.stage in ("danger","critical") and a.fri>=55, f"fri={a.fri} {a.reasons}")
ck("동반 근거 표기", any("동반" in r for r in a.reasons))

# 4) 예지: 값은 경고 아래지만 둘 다 빠르게 상승 → 조기 danger
a=assess({"h2":{"value":9,"series":lin(0.5,9),"baseline":noisy(0.6)},        # <warn 10
          "voc":{"value":190,"series":lin(30,190),"baseline":noisy(35)}}, cfg)  # <warn 200
ck("예지: 임계 전 조기 danger", a.stage in ("danger","critical") and a.fri>=55, f"fri={a.fri} stage={a.stage} {a.reasons}")

# 5) 극한: 가스 위험초과 + 연기 → critical
a=assess({"h2":{"value":40,"series":flat(40),"baseline":noisy(2)},
          "voc":{"value":1500,"series":flat(1500),"baseline":noisy(50)},
          "smoke":True}, cfg)
ck("가스초과+연기=critical", a.stage=="critical" and a.fri>=80, f"fri={a.fri}")

# 6) 베이스라인 이상탐지: 평소 5 근처인데 갑자기 50 → z 큼
z=robust_z(50, [5,5.2,4.8,5,5.1,4.9,5,5.3,4.7,5]*3)
ck("robust_z 급등 감지", z>6, f"z={round(z,1)}")

# 7) 기울기: 60초에 12 상승 → 12/분
sp=slope_per_min(lin(0,12,60))
ck("slope_per_min≈12", 11.0<sp<13.0, f"sp={round(sp,2)}")

# ── VentController: 개방→히스테리시스→복구 ──
vc=VentController(cfg, recover_hold=5.0); t=1000.0
r1=vc.step(70,"danger",t); ck("위험→벤트 개방", r1=="open" and vc.open)
r2=vc.step(40,"watch",t+1); ck("경계(40)에선 안 닫힘(히스테리시스)", r2 is None and vc.open)
r3=vc.step(10,"normal",t+2)         # close 밑 진입(타이머 시작)
r4=vc.step(10,"normal",t+9)         # 5초 유지 후 → close
ck("복구 유지 후 벤트 닫힘", r3 is None and r4=="close" and not vc.open, f"r3={r3} r4={r4}")
# 수동 우선
vc.set_manual(True); ck("수동시 자동보류", vc.step(90,"critical",t+20) is None and vc.open)

# ── CO(세 번째 가스) ──
# 8) CO 센서가 없으면 예전 H2·VOC 두 가스 식과 결과가 완전히 같다(무작위 1000건)
import random
from jcc_server.fire_risk import _gas_score, clamp01
def old_fri(sig, c):
    h2, voc = sig.get("h2") or {}, sig.get("voc") or {}
    gh, _ = _gas_score(h2.get("value"), h2.get("series"), h2.get("baseline"), c.h2, c)
    gv, _ = _gas_score(voc.get("value"), voc.get("series"), voc.get("baseline"), c.voc, c)
    co = 0.20 * min(gh, gv) if (gh >= 0.3 and gv >= 0.3) else 0.0
    f = 100.0 * clamp01(0.38 * gh + 0.38 * gv + co)
    if (h2.get("value") or 0) >= c.h2.alarm or (voc.get("value") or 0) >= c.voc.alarm:
        f = max(f, c.crit_fri)
    return round(f, 1)
rng = random.Random(7); same = 0
for _ in range(1000):
    h, v = rng.uniform(0, 30), rng.uniform(0, 1200)
    sig = {"h2": {"value": h, "series": lin(rng.uniform(0, h), h), "baseline": noisy(rng.uniform(0.2, 3))},
           "voc": {"value": v, "series": lin(rng.uniform(0, v), v), "baseline": noisy(rng.uniform(20, 80))}}
    same += assess(sig, cfg).fri == old_fri(sig, cfg)
ck("CO 없으면 예전 식과 동일(1000/1000)", same == 1000, f"{same}/1000")

# 9) H2 + CO 동반 상승(VOC 센서 없음) → danger, 근거에 H2·CO
a = assess({"h2": {"value": 12, "series": lin(1, 12), "baseline": noisy(0.6)},
            "co": {"value": 120, "series": lin(5, 120), "baseline": noisy(4)}}, cfg)
ck("H2·CO 동반=danger↑", a.stage in ("danger", "critical"), f"fri={a.fri} {a.reasons}")
ck("근거: H2·CO 동반 상승", any("H2·CO 동반" in r for r in a.reasons), str(a.reasons))

# 10) CO 단독 급상승(절연물 탄화 등) → 적어도 주의, 동반 아님
a = assess({"co": {"value": 90, "series": lin(3, 90), "baseline": noisy(3)}}, cfg)
ck("CO 단독 급상승=watch", a.stage == "watch" and not any("동반" in r for r in a.reasons),
   f"fri={a.fri} stage={a.stage} {a.reasons}")

# 11) CO 위험선 초과 → 하드 하한, 두 가스 위험선 초과 → critical
a = assess({"co": {"value": 250, "series": flat(250), "baseline": noisy(4)}}, cfg)
ck("CO 위험선 초과 → FRI≥80", a.fri >= 80, f"fri={a.fri}")
a = assess({"h2": {"value": 30, "series": flat(30), "baseline": noisy(1)},
            "co": {"value": 250, "series": flat(250), "baseline": noisy(4)}}, cfg)
ck("두 가스 위험선 초과 → critical", a.stage == "critical", a.stage)

# 12) 세 가스 모두 오르면 근거에 셋 다
a = assess({"h2": {"value": 12, "series": lin(1, 12), "baseline": noisy(0.6)},
            "voc": {"value": 400, "series": lin(30, 400), "baseline": noisy(30)},
            "co": {"value": 120, "series": lin(5, 120), "baseline": noisy(4)}}, cfg)
ck("세 가스 동반 표기", any("H2·VOC·CO 동반" in r for r in a.reasons), str(a.reasons))

print("\nFAILS:", F if F else "NONE"); sys.exit(1 if F else 0)
