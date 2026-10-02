"""정적 데모 사이트 빌드 (Vercel 등 정적 호스팅용).

서버 없이 브라우저만으로 도는 시연용 빌드를 만든다. 화면(server/static/index.html)은
하나의 원본을 그대로 쓰고, 그 앞에 demo-api.js(인-브라우저 백엔드)만 끼워 넣는다.
→ 화면을 고치면 실서버와 데모 양쪽에 똑같이 반영된다(로직 갈라짐 방지).

    python tools/build_web.py

결과: web/index.html, web/demo-api.js, web/img/*
Vercel에서 이 저장소를 가져와 Root Directory를 web 으로 지정하면 배포된다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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


def _artifact(html: str, core_js: str, tuning_js: str, api_src: str) -> str:
    """호스팅 페이지(Artifact 등)용 자기완결 본문. 호스트가 <html>/<head>/<body> 뼈대를 씌우므로 그 태그를 빼고,
    외부 스크립트(판정 코어·튜닝 데이터·데모 백엔드)는 통째로 인라인한다. 치환값은 함수로 넘긴다 —
    문자열로 넘기면 JS 안의 역슬래시가 re 이스케이프로 해석된다."""
    body = re.sub(r"<!DOCTYPE[^>]*>", "", html, flags=re.I)
    for tag in ("html", "head", "body"):
        body = re.sub(r"</?" + tag + r"[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<meta[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<!-- 이 파일은 tools/build_web\.py 가 생성합니다\..*?-->\n", "", body, count=1, flags=re.S).lstrip()
    body = re.sub(r'<script src="predict-core\.js[^"]*"></script>',
                  lambda _m: "<script>\n" + core_js + "\n</script>", body, count=1)
    body = re.sub(r'<script src="tuning-data\.js[^"]*"></script>',
                  lambda _m: "<script>\n" + tuning_js + "\n</script>", body, count=1)
    if api_src:
        body = re.sub(r'<script src="demo-api\.js[^"]*"></script>',
                      lambda _m: "<script>\n" + api_src + "\n</script>", body, count=1)
    # 인코딩 선언은 반드시 남긴다(위에서 <meta>를 전부 지웠다) — 호스트가 charset을 안 붙여도 한글이 안 깨지게
    body = '<meta charset="utf-8">\n' + body.lstrip()
    # 아티팩트는 글꼴 파일을 함께 올리지 않으므로 서버 보관 글꼴 블록을 구글 글꼴 불러오기로 바꾼다
    return re.sub(r"/\*@FONTS-LOCAL.*?/\*@FONTS-END\*/",
                  lambda _m: '@import url("https://fonts.googleapis.com/css2?family=Gothic+A1:wght@300;400;500;600;700;800'
                             '&family=B612:wght@400;700&family=B612+Mono:wght@400;700&display=swap");',
                  body, count=1, flags=re.S)


def main() -> int:
    if not os.path.isfile(SRC_HTML):
        print(f"원본을 찾을 수 없습니다: {SRC_HTML}")
        return 1
    with open(SRC_HTML, "r", encoding="utf-8") as fh:
        html = fh.read()

    # demo-api.js 내용 해시로 캐시버스팅(재배포·수정 때 브라우저가 옛 버전을 물지 않게).
    api_path = os.path.join(OUT, "demo-api.js")
    ver = ""
    if os.path.isfile(api_path):
        with open(api_path, "rb") as fh:
            ver = "?v=" + hashlib.sha1(fh.read()).hexdigest()[:8]

    # 화면 스크립트보다 먼저 로드돼야 fetch 가로채기가 걸린다.
    marker = "<script>"
    idx = html.find(marker)
    if idx < 0:
        print("index.html 에서 <script> 를 찾지 못했습니다.")
        return 1
    html = (BANNER + html[:idx]
            + f'<script src="demo-api.js{ver}"></script>\n'
            + html[idx:])

    # 판정 코어(JS)는 서버 원본을 그대로 복사한다. 화면 원본에 이미 데모 백엔드보다 앞에
    # <script src="predict-core.js">가 있으므로 캐시버스팅 쿼리만 붙인다.
    os.makedirs(OUT, exist_ok=True)
    core_src = os.path.join(ROOT, "server", "static", "predict-core.js")
    shutil.copy2(core_src, os.path.join(OUT, "predict-core.js"))
    with open(core_src, "r", encoding="utf-8") as fh:
        core_js = fh.read()
    core_ver = "?v=" + hashlib.sha1(core_js.encode("utf-8")).hexdigest()[:8]
    html = html.replace('<script src="predict-core.js"></script>',
                        f'<script src="predict-core.js{core_ver}"></script>', 1)

    # 튜닝 콘솔 데이터(손잡이 레지스트리·시나리오)는 파이썬 정본에서 뽑아 싣는다 — 데모엔 서버가 없으므로.
    sys.path.insert(0, os.path.join(ROOT, "server"))
    from jcc_server.params import registry_view
    from jcc_server.scenarios import export_all
    tuning_js = ("/* tools/build_web.py 가 생성 — 직접 고치지 마세요(server/jcc_server/params.py·scenarios.py가 원본). */\n"
                 "window.JCC_TUNING_DATA = "
                 + json.dumps({"params": registry_view(), "scenarios": export_all()},
                              ensure_ascii=False, separators=(",", ":")) + ";\n")
    with open(os.path.join(OUT, "tuning-data.js"), "w", encoding="utf-8") as fh:
        fh.write(tuning_js)
    tuning_ver = "?v=" + hashlib.sha1(tuning_js.encode("utf-8")).hexdigest()[:8]
    html = html.replace(f'<script src="demo-api.js{ver}"></script>',
                        f'<script src="tuning-data.js{tuning_ver}"></script>\n<script src="demo-api.js{ver}"></script>', 1)

    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    # 제품 사진·로고 복사 (없으면 화면이 도식으로 대체하므로 실패해도 무방)
    dst_img = os.path.join(OUT, "img")
    if os.path.isdir(SRC_IMG):
        os.makedirs(dst_img, exist_ok=True)
        for name in os.listdir(SRC_IMG):
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")):
                shutil.copy2(os.path.join(SRC_IMG, name), os.path.join(dst_img, name))

    # 글꼴: 정적 데모(web/)는 서버와 같은 파일을 복사해 쓴다
    src_fonts, dst_fonts = os.path.join(ROOT, "server", "static", "fonts"), os.path.join(OUT, "fonts")
    if os.path.isdir(src_fonts):
        os.makedirs(dst_fonts, exist_ok=True)
        for name in os.listdir(src_fonts):
            shutil.copy2(os.path.join(src_fonts, name), os.path.join(dst_fonts, name))

    api_src = ""
    if os.path.isfile(api_path):
        with open(api_path, "r", encoding="utf-8") as fh:
            api_src = fh.read()
    body_only = _artifact(html, core_js, tuning_js, api_src)
    with open(os.path.join(OUT, "artifact.html"), "w", encoding="utf-8") as fh:
        fh.write(body_only)

    # 고객 화면(JCC GUARD) 시연판: 같은 데모 백엔드(판정 코어·튜닝 데이터 포함)를 첫 스크립트 앞에 끼운다
    src_guard = os.path.join(ROOT, "server", "static", "guard.html")
    if os.path.isfile(src_guard):
        with open(src_guard, "r", encoding="utf-8") as fh:
            g = fh.read()
        gi = g.find("<script>")
        g = (BANNER.replace("index.html", "guard.html") + g[:gi]
             + f'<script src="predict-core.js{core_ver}"></script>\n<script src="tuning-data.js{tuning_ver}"></script>\n'
             + f'<script src="demo-api.js{ver}"></script>\n' + g[gi:])
        with open(os.path.join(OUT, "guard.html"), "w", encoding="utf-8") as fh:
            fh.write(g)
        with open(os.path.join(OUT, "guard-artifact.html"), "w", encoding="utf-8") as fh:
            fh.write(_artifact(g, core_js, tuning_js, api_src))

    imgs = len(os.listdir(dst_img)) if os.path.isdir(dst_img) else 0
    print(f"빌드 완료 → {OUT}")
    print(f"  index.html  (demo-api.js 주입됨)")
    print(f"  img/        이미지 {imgs}개")
    print("\nVercel 배포: 저장소 가져오기 → Root Directory 를 'web' 으로 지정 → Deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
