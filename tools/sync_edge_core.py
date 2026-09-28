"""예지 코어를 서버 → 펌웨어(엣지)로 복사한다. 두 벌이 갈라지지 않게 하는 유일한 통로.

원본(정본): server/jcc_server/{fire_risk,contact_heat,dewpoint,inputs,predict}.py
사본:       firmware/jcc_ccm/predict/ (바이트 단위로 동일해야 한다)

CCM에는 펌웨어 폴더만 올라가므로 서버 패키지를 import할 수 없다 → 복사가 필요하다.
대신 손으로 고치지 않고 이 스크립트로만 복사하고, 테스트가 동일성을 검사한다.

    python tools/sync_edge_core.py          # 복사
    python tools/sync_edge_core.py --check  # 다르면 종료코드 1 (테스트·CI용)
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "server", "jcc_server")
DST = os.path.join(ROOT, "firmware", "jcc_ccm", "predict")
FILES = ("fire_risk.py", "contact_heat.py", "dewpoint.py", "inputs.py", "predict.py")


def differing() -> list[str]:
    out = []
    for f in FILES:
        a, b = os.path.join(SRC, f), os.path.join(DST, f)
        if not os.path.isfile(b):
            out.append(f)
            continue
        with open(a, "rb") as fa, open(b, "rb") as fb:
            if fa.read() != fb.read():
                out.append(f)
    return out


def main(argv: list[str]) -> int:
    if "--check" in argv:
        bad = differing()
        if bad:
            print("엣지 코어가 서버와 다릅니다:", ", ".join(bad))
            print("→ python tools/sync_edge_core.py 로 다시 복사하세요(사본은 손으로 고치지 않는다).")
            return 1
        print("엣지 코어 = 서버 코어 (동일)")
        return 0
    os.makedirs(DST, exist_ok=True)
    for f in FILES:
        shutil.copyfile(os.path.join(SRC, f), os.path.join(DST, f))
    print(f"복사 완료 → {DST}: {', '.join(FILES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
