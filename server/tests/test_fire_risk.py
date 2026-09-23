"""화재 예지 FRI 코어 시나리오 테스트 (pytest 없이 실행: python tests/test_fire_risk.py)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jcc_server.fire_risk import assess, FireConfig, VentController, slope_per_min, robust_z

def lin(start, end, secs=60, n=13, t0=1000.0):
    """start→end로 secs초 동안 선형 증가하는 (ts,val) 표본 n개."""
    return [(t0 + secs*i/(n-1), start + (end-start)*i/(n-1)) for i in range(n)]
def flat(val, secs=60, n=13, t0=1000.0):
    return [(t0 + secs*i/(n-1), val) for i in range(n)]
def noisy(base, n=16):
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
z=robust_z(50, [5,5.2,4.8,5,5.1,4.9,5,5.3,4.7,5])
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

print("\nFAILS:", F if F else "NONE"); sys.exit(1 if F else 0)
