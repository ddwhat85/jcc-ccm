# 이미지 넣는 곳 (제품 사진 · 회사 로고)

이 폴더에 파일을 넣기만 하면 화면에 **자동으로** 나타난다.
파일이 없으면 기존 도식/글자 로고로 자동 대체되므로, 없어도 화면은 정상 동작한다.

| 파일명 | 쓰이는 곳 | 권장 |
|---|---|---|
| `jcc-logo.png` | 좌측 상단 메뉴바 회사 로고 | 배경 투명 PNG, 높이 최소 48px |
| `infrasensing-h2.jpg` | 수소센서(InfraSensing) 인스펙터 제품 사진 | 정사각형에 가깝게, 512px 이상 |

## 센서 사진 추가하는 법

1. 사진 파일을 이 폴더에 넣는다 (예: `banner-qm30vt.jpg`)
2. `firmware/jcc_ccm/discovery/profiles.py` 에서 해당 제품에 한 줄 추가:

   ```python
   "photo": "img/banner-qm30vt.jpg",
   ```

3. [AI 자동연결]을 다시 누르면 반영된다.

지원 형식: png · jpg · jpeg · webp · gif · svg
