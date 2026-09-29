"""OTA 번들 만들기 + 서명 — JCC 오프라인 PC에서 새 펌웨어를 배포할 때.

    python tools/ota_build.py --key ~/.jcc/ota_ed25519.key            # 버전 = jcc_ccm/__init__.py
    python tools/ota_build.py --key ... --out server/ota               # 서버가 제안할 폴더로

결과: jcc-ccm-<버전>.tar.gz(firmware/jcc_ccm, 캐시 제외) + jcc-ccm-<버전>.json(manifest+서명).
번들은 결정적으로 만든다(파일 순서·시각·소유자 고정) — 같은 소스면 같은 해시.
서버는 이 두 파일을 JCC_OTA_DIR에 두기만 한다. 서명은 여기서 끝나므로 서버는 개인키를 모른다.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
sys.path.insert(0, FW)

from jcc_ccm import ed25519  # noqa: E402
from jcc_ccm.ota import manifest_message, parse_version  # noqa: E402

_VER_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)


def read_version() -> str:
    with open(os.path.join(FW, "jcc_ccm", "__init__.py"), encoding="utf-8") as fh:
        m = _VER_RE.search(fh.read())
    if not m:
        raise SystemExit("jcc_ccm/__init__.py 에서 __version__을 못 찾음")
    return m.group(1)


def _files():
    pkg = os.path.join(FW, "jcc_ccm")
    for d, dirs, files in os.walk(pkg):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(files):
            if f.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(d, f)
            yield full, os.path.relpath(full, FW).replace(os.sep, "/")


def build(key_path: str, out_dir: str, version: str | None = None) -> dict:
    """번들·manifest를 만들어 out_dir에 쓰고 manifest를 돌려준다.
    version을 주면 번들 안의 __init__.__version__도 그 값으로 바꿔 넣는다(자체 점검이 대조한다)."""
    with open(key_path, encoding="utf-8") as fh:
        seed = bytes.fromhex(fh.read().strip())
    ver = version or read_version()
    if parse_version(ver) is None:
        raise SystemExit(f"버전 형식 오류: {ver}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for full, arc in _files():
            with open(full, "rb") as fh:
                data = fh.read()
            if arc == "jcc_ccm/__init__.py":
                data = _VER_RE.sub(f'__version__ = "{ver}"', data.decode("utf-8")).encode("utf-8")
            ti = tarfile.TarInfo(arc)
            ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
    blob = buf.getvalue()
    name = f"jcc-ccm-{ver}.tar.gz"
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "wb") as fh:
        fh.write(blob)
    m = {"name": "jcc-ccm", "version": ver, "sha256": hashlib.sha256(blob).hexdigest(),
         "size": len(blob), "file": name, "built_at": int(time.time())}
    m["sig"] = ed25519.sign(seed, manifest_message(m)).hex()
    with open(os.path.join(out_dir, f"jcc-ccm-{ver}.json"), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    return m


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="JCC-CCM OTA 번들 생성·서명")
    ap.add_argument("--key", required=True, help="ota_keygen.py로 만든 개인키 파일")
    ap.add_argument("--out", default=os.path.join(ROOT, "server", "ota"))
    ap.add_argument("--version", help="번들 버전(기본: jcc_ccm/__init__.py)")
    args = ap.parse_args(argv)
    m = build(args.key, args.out, args.version)
    print(f"번들: {os.path.join(args.out, m['file'])}  ({m['size']} bytes, sha256 {m['sha256'][:16]}…)")
    print(f"manifest: jcc-ccm-{m['version']}.json (서명 포함) — 서버 JCC_OTA_DIR에 두 파일을 두면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
