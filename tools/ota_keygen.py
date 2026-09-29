"""OTA 서명 키 만들기 — JCC 사무실의 오프라인 PC에서 한 번만.

    python tools/ota_keygen.py                 # ~/.jcc/ota_ed25519.key 생성, 공개키 출력
    python tools/ota_keygen.py --out D:/키.key

개인키: 펌웨어 번들 서명용. 절대 저장소·서버·CCM에 두지 않는다(오프라인 보관·백업).
        잃어버리면 새 키를 만들고 모든 CCM의 공개키를 현장에서 바꿔야 한다.
공개키: 각 CCM의 config.toml [ota] public_key 에 넣는다(비밀 아님).
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "firmware"))

from jcc_ccm import ed25519  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="JCC-CCM OTA 서명 키 생성")
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), ".jcc", "ota_ed25519.key"))
    args = ap.parse_args(argv)
    if os.path.abspath(args.out).startswith(ROOT):
        print("저장소 안에 개인키를 만들 수 없습니다 — 저장소 밖 경로를 지정하세요.", file=sys.stderr)
        return 2
    if os.path.exists(args.out):
        print(f"이미 키가 있습니다: {args.out} (덮어쓰면 기존 CCM들이 새 번들을 거부합니다)", file=sys.stderr)
        return 2
    seed = os.urandom(32)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(seed.hex() + "\n")
    print(f"개인키 저장: {args.out}  (오프라인 보관·백업, 저장소·서버·CCM에 두지 말 것)")
    print("CCM config.toml 에 넣을 공개키:")
    print("[ota]")
    print("enabled = true")
    print(f'public_key = "{ed25519.public_key(seed).hex()}"')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
