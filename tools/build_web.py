"""정적 데모 사이트 빌드 (Vercel 등 정적 호스팅용).

서버 없이 브라우저만으로 도는 시연용 빌드를 만든다. 화면(server/static/index.html)은
하나의 원본을 그대로 쓰고, 그 앞에 demo-api.js(인-브라우저 백엔드)만 끼워 넣는다.
→ 화면을 고치면 실서버와 데모 양쪽에 똑같이 반영된다(로직 갈라짐 방지).

    python tools/build_web.py

결과: web/index.html, web/demo-api.js, web/img/*
Vercel에서 이 저장소를 가져와 Root Directory를 web 으로 지정하면 배포된다.
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_HTML = os.path.join(ROOT, "server", "static", "index.html")
SRC_IMG = os.path.join(ROOT, "server", "static", "img")
OUT = os.path.join(ROOT, "web")

BANNER = (
    "<!-- 이 파일은 tools/build_web.py 가 생성합니다. 직접 고치지 마세요.\n"
    "     화면을 바꾸려면 server/static/index.html 을 고치고 다시 빌드하세요. -->\n"
)


def main() -> int:
    if not os.path.isfile(SRC_HTML):
        print(f"원본을 찾을 수 없습니다: {SRC_HTML}")
        return 1
    with open(SRC_HTML, "r", encoding="utf-8") as fh:
        html = fh.read()

    # 화면 스크립트보다 먼저 로드돼야 fetch 가로채기가 걸린다.
    marker = "<script>"
    idx = html.find(marker)
    if idx < 0:
        print("index.html 에서 <script> 를 찾지 못했습니다.")
        return 1
    html = (BANNER + html[:idx]
            + '<script src="demo-api.js"></script>\n'
            + html[idx:])

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    # 제품 사진·로고 복사 (없으면 화면이 도식으로 대체하므로 실패해도 무방)
    dst_img = os.path.join(OUT, "img")
    if os.path.isdir(SRC_IMG):
        os.makedirs(dst_img, exist_ok=True)
        for name in os.listdir(SRC_IMG):
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")):
                shutil.copy2(os.path.join(SRC_IMG, name), os.path.join(dst_img, name))

    # 호스팅 페이지(Artifact 등)는 자체 <html>/<head>/<body> 뼈대를 씌우므로,
    # 그 태그를 뺀 본문 전용판도 만든다(title·style은 맨 앞에 유지).
    import re
    body_only = re.sub(r"<!DOCTYPE[^>]*>", "", html, flags=re.I)
    body_only = re.sub(r"</?html[^>]*>", "", body_only, flags=re.I)
    body_only = re.sub(r"</?head[^>]*>", "", body_only, flags=re.I)
    body_only = re.sub(r"</?body[^>]*>", "", body_only, flags=re.I)
    body_only = re.sub(r"<meta[^>]*>", "", body_only, flags=re.I)
    with open(os.path.join(OUT, "artifact.html"), "w", encoding="utf-8") as fh:
        fh.write(body_only.replace(BANNER, "", 1).lstrip())

    imgs = len(os.listdir(dst_img)) if os.path.isdir(dst_img) else 0
    print(f"빌드 완료 → {OUT}")
    print(f"  index.html  (demo-api.js 주입됨)")
    print(f"  img/        이미지 {imgs}개")
    print("\nVercel 배포: 저장소 가져오기 → Root Directory 를 'web' 으로 지정 → Deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
