"""원격 업데이트 종단 시험 — 실제 서버(HTTP) ↔ 실제 CCM 에이전트, 임시 설치 폴더.

  1) 배포 대상 CCM에만 새 버전 제안
  2) CCM: 받기 → 해시·서명 확인 → 안전 설치 → 새 버전 자체 점검 → 전환 → 재시작 요청
  3) 런처가 새 버전으로 시험 부팅 → 전송 3회 성공 → 확정 → 서버에 버전 변경 기록
  4) 위조 번들(해시 불일치)·다른 키로 서명한 번들은 설치하지 않고 다시 받지 않음
  5) 시험 부팅이 계속 실패하면 런처가 되돌리고, 서버에 롤백이 기록된다

python -m tests.test_ota_e2e   (server/ 에서)
"""
from __future__ import annotations

import collections
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "firmware"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    tmp = tempfile.mkdtemp()
    ota_dir = os.path.join(tmp, "ota_srv")
    os.environ["JCC_OTA_DIR"] = ota_dir
    os.environ["JCC_OTA_TARGET"] = "ccm-ota"         # 단계적 배포: 이 CCM만

    from jcc_server.app import make_server
    from jcc_server.storage import Storage
    from jcc_ccm import config as cfgmod, ed25519, ota
    from jcc_ccm.agent import Agent
    from jcc_ccm.sensors import Reading
    from jcc_ccm.transport.http_transport import HttpTransport
    import ota_build

    seed = bytes(range(1, 33))
    key = os.path.join(tmp, "jcc.key")
    with open(key, "w") as fh:
        fh.write(seed.hex())
    pub = ed25519.public_key(seed).hex()
    ota_build.build(key, ota_dir, version="0.9.0")

    st = Storage(os.path.join(tmp, "s.db"))
    srv = make_server("127.0.0.1", 0, st)
    base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    install = os.path.join(tmp, "opt-jcc-ccm")
    os.makedirs(install)

    def cfg_for(dev):
        text = f"""[device]
id = "{dev}"
panel = "p1"
[collection]
interval_seconds = 2
[transport]
kind = "http"
[transport.http]
url = "{base_url}/v1/telemetry"
timeout_seconds = 5
[[sensors]]
key = "t"
name = "t"
driver = "ambient"
metric = "temp"
[ota]
enabled = true
public_key = "{pub}"
base_dir = "{install.replace(chr(92), '/')}"
"""
        p = os.path.join(tmp, dev + ".toml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return cfgmod.load(p)

    class Drv:
        def read(self):
            return Reading(key="t", name="t", unit="C", value=25.0, ok=True)

    def agent(dev, version):
        from jcc_ccm.ota import OtaManager
        cfg = cfg_for(dev)
        a = Agent.__new__(Agent)
        a._cfg, a._drivers, a._transport = cfg, [Drv()], HttpTransport(cfg)
        a._queue, a._stop, a._edge, a._restart = collections.deque(maxlen=50), False, None, False
        a._ota = OtaManager(cfg, current_version=version)
        import jcc_ccm.agent as agmod
        agmod.__version__ = version                     # 보고 버전(payload fw)도 같게
        return a

    def events(kind):
        return [e["detail"] for e in st.list_events(limit=200) if e["etype"] == kind]

    try:
        print("=== OTA 종단 시험 ===")
        # 배포 대상이 아닌 CCM에는 제안하지 않음
        other = agent("ccm-other", "0.1.0")
        other._tick()
        check("배포 대상 아닌 CCM엔 제안 없음", not other._restart and ota.read_current(install) == "")

        # 1~2) 대상 CCM: 받기·확인·설치·자체 점검·전환
        a = agent("ccm-ota", "0.1.0")
        t0 = time.time()
        a._tick()
        check("새 버전 설치·전환 후 재시작 요청", a._restart and ota.read_current(install) == "0.9.0",
              f"current={ota.read_current(install)} ({time.time() - t0:.1f}초)")
        check("버전 폴더에 설치", os.path.isfile(os.path.join(install, "releases", "0.9.0", "jcc_ccm", "agent.py")))
        check("시험 부팅 대기 기록", (ota.load_state(install).get("pending") or {}).get("version") == "0.9.0")
        check("서버에 제안 기록", any("0.9.0" in d for d in events("ota_offer")))

        # 3) 재부팅: 런처 → 시험 부팅 → 새 버전이 전송 3회 성공 → 확정
        spec = importlib.util.spec_from_file_location("launcher", os.path.join(ROOT, "firmware", "scripts", "launcher.py"))
        L = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(L)
        act, rel = L.prepare(install, time.time())
        check("런처: 새 버전으로 시험 부팅", act == "trial" and rel.endswith(os.path.join("releases", "0.9.0")), act)
        b = agent("ccm-ota", "0.9.0")
        for _ in range(3):
            b._tick()
        s = ota.load_state(install)
        check("전송 3회 성공 → 확정", s.get("pending") is None and (s.get("last") or {}).get("version") == "0.9.0",
              json.dumps(s, ensure_ascii=False)[:120])
        check("서버에 버전 변경·시험 부팅 기록",
              any("0.1.0 → 0.9.0" in d for d in events("firmware")) and events("ota_trial"))
        b._tick()
        check("이미 최신이면 더 제안하지 않음", not b._restart)

        # 4) 위조 번들: 파일 한 바이트 바꿈(서명된 해시와 불일치) → 설치 안 함, 다시 안 받음
        m = ota_build.build(key, ota_dir, version="0.9.1")
        bp = os.path.join(ota_dir, m["file"])
        blob = bytearray(open(bp, "rb").read())
        blob[len(blob) // 2] ^= 0xFF
        open(bp, "wb").write(bytes(blob))
        c = agent("ccm-ota", "0.9.0")
        c._tick()
        check("위조 번들 거부(해시 불일치)", not c._restart and ota.read_current(install) == "0.9.0"
              and "0.9.1" in ota.load_state(install).get("bad", []), c._ota.last_error)
        c._tick()
        check("거부한 버전은 다시 받지 않음", not c._restart)

        # 다른 키로 서명한 번들(서버가 뚫려 가짜를 올린 상황)
        evil_key = os.path.join(tmp, "evil.key")
        with open(evil_key, "w") as fh:
            fh.write((b"\x09" * 32).hex())
        ota_build.build(evil_key, ota_dir, version="0.9.2")
        d = agent("ccm-ota", "0.9.0")
        d._tick()
        check("다른 키로 서명한 번들 거부", not d._restart and ota.read_current(install) == "0.9.0"
              and "서명" in d._ota.last_error, d._ota.last_error)
        check("거부 사유가 서버 보고에 실림", "error" in d._ota.status())

        # 5) 시험 부팅 실패 → 런처 롤백 → 서버에 기록
        ota.activate(install, "0.9.3", time.time())       # (가상) 새 버전이 계속 죽는 상황
        for _ in range(L.MAX_BOOTS + 1):
            act, rel = L.prepare(install, time.time())
        check("계속 죽으면 이전 버전으로 롤백", act == "rollback" and ota.read_current(install) == "0.9.0", act)
        e = agent("ccm-ota", "0.9.0")
        e._tick()
        check("서버에 자동 롤백 기록", any("0.9.3" in x and "0.9.0" in x for x in events("ota_rollback")),
              (events("ota_rollback") or ["없음"])[0])
    finally:
        srv.shutdown()
        st.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.CRITICAL)
    raise SystemExit(run())
