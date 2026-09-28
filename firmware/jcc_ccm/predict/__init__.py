"""예지 코어(엣지 사본) — 화재 FRI·접점발열·결로 판정과 입력 조립.

⚠ 이 폴더의 fire_risk/contact_heat/dewpoint/inputs/predict.py 는 서버
(server/jcc_server/)의 정본을 tools/sync_edge_core.py 로 복사한 것이다.
여기서 직접 고치지 말고 서버 쪽을 고친 뒤 다시 복사한다(테스트가 동일성을 검사).
→ 서버와 CCM이 같은 입력에 같은 판정을 낸다(엣지+서버 이중화).
"""
from .inputs import build_panel_inputs, ROLES
from .predict import Predictor

__all__ = ["build_panel_inputs", "ROLES", "Predictor"]
