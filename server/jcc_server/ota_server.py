"""서버 쪽 OTA — 배포 폴더의 최신 번들을 골라, 옛 버전을 보고한 CCM에 제안한다.

서버는 서명하지 않는다(개인키를 모른다). 번들·manifest는 JCC 오프라인 PC에서
tools/ota_build.py로 서명돼 JCC_OTA_DIR에 놓인다. CCM이 공개키로 검증하므로 서버가
뚫려도 가짜 펌웨어는 설치되지 않는다.

배포 대상은 명시해야만 제안한다(기본 꺼짐):
  JCC_OTA_TARGET=""             제안 안 함
  JCC_OTA_TARGET="all"          모든 CCM
  JCC_OTA_TARGET="ccm-2661,..." 지정한 CCM만(단계적 배포 — 한 대 먼저 시험)
"""
from __future__ import annotations

import json
import os

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ota")


def ota_dir() -> str:
    return os.environ.get("JCC_OTA_DIR") or DEFAULT_DIR


def parse_version(v):
    try:
        parts = tuple(int(x) for x in str(v).strip().split("."))
    except (TypeError, ValueError):
        return None
    return parts if parts and all(p >= 0 for p in parts) else None


def latest_manifest(folder: str | None = None):
    """폴더에서 번들 파일이 실제로 있는 가장 높은 버전의 manifest. 없으면 None."""
    folder = folder or ota_dir()
    best, best_v = None, None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or m.get("name") != "jcc-ccm":
            continue
        v = parse_version(m.get("version"))
        f = str(m.get("file") or "")
        if v is None or os.path.basename(f) != f or not os.path.isfile(os.path.join(folder, f)):
            continue
        if best_v is None or v > best_v:
            best, best_v = m, v
    return best


def targeted(device_id: str, target: str | None = None) -> bool:
    t = (os.environ.get("JCC_OTA_TARGET", "") if target is None else target).strip()
    if not t:
        return False
    return t == "all" or device_id in {x.strip() for x in t.split(",") if x.strip()}


def offer_for(device_id: str, fw, folder: str | None = None, target: str | None = None):
    """이 CCM에 제안할 manifest(+다운로드 주소). 대상이 아니거나 이미 최신이면 None."""
    if not device_id or not targeted(device_id, target):
        return None
    cur = parse_version(fw)
    m = latest_manifest(folder)
    if m is None or cur is None or parse_version(m["version"]) <= cur:
        return None
    return dict(m, url=f"/ota/{m['file']}")


def bundle_path(name: str, folder: str | None = None):
    """다운로드 요청 파일 경로(디렉터리 탈출·다른 확장자 차단). 없으면 None."""
    base = os.path.basename(name)
    if base != name or not base.endswith(".tar.gz"):
        return None
    p = os.path.join(folder or ota_dir(), base)
    return p if os.path.isfile(p) else None
