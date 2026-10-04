"""오프라인 보관함 — 통신이 끊긴 동안의 텔레메트리를 저장장치(eMMC)에 차례로 쌓았다가, 다시 연결되면 오래된 것부터 보낸다.

메모리 큐만으로는 (1) 몇 시간을 넘는 끊김에 앞부분이 버려지고 (2) 그 사이 전원이 꺼지면 전부 사라진다.
여기 쌓아 두면 재부팅 뒤에도 남는다. 서버는 끊긴 동안의 값이 나중에 올라온 것으로 '통신만 끊김(판넬은 켜져 있었다)'을 가린다.

저장장치 수명을 아끼려고 **보낼 수 있을 때는 아무것도 쓰지 않는다** — 전송이 실패한 뒤부터만 쓴다(agent가 결정).
형식: 디렉터리 안 seg-00000001.jsonl …(한 줄 = 묶음 하나, 500줄마다 새 파일) + pos.json(가장 오래된 파일에서 보낸 줄 수).
글 쓰다 전원이 나가 마지막 줄이 깨졌으면 그 줄만 건너뛴다. 상한(바이트)을 넘으면 가장 오래된 파일부터 지운다(지운 수를 센다).
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("jcc_ccm.spool")

SEG_LINES = 500
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class Spool:
    def __init__(self, path: str, max_bytes: int = DEFAULT_MAX_BYTES, seg_lines: int = SEG_LINES):
        self.path, self.max_bytes, self.seg_lines = path, max_bytes, seg_lines
        os.makedirs(path, exist_ok=True)
        self.dropped = 0
        self._seal()
        self._pos = self._load_pos()
        self._write_lines = self._lines(self._segs()[-1]) if self._segs() else 0

    # ── 파일 ──
    def _segs(self) -> list:
        return sorted(int(n[4:12]) for n in os.listdir(self.path) if n.startswith("seg-") and n.endswith(".jsonl") and n[4:12].isdigit())

    def _file(self, n: int) -> str:
        return os.path.join(self.path, f"seg-{n:08d}.jsonl")

    def _lines(self, n: int) -> int:
        try:
            with open(self._file(n), "rb") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    def _load_pos(self) -> dict:
        try:
            with open(os.path.join(self.path, "pos.json"), encoding="utf-8") as fh:
                p = json.load(fh)
            return {"seg": int(p.get("seg", 0)), "line": int(p.get("line", 0))}
        except (OSError, ValueError, TypeError):
            return {"seg": 0, "line": 0}

    def _save_pos(self) -> None:
        tmp = os.path.join(self.path, "pos.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._pos, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, os.path.join(self.path, "pos.json"))

    def _bytes(self) -> int:
        return sum(os.path.getsize(self._file(n)) for n in self._segs())

    def _seal(self) -> None:
        """쓰다가 전원이 나가 마지막 줄이 줄바꿈 없이 끝났으면 끊어 둔다 — 안 그러면 다음 묶음이 그 깨진 줄에 붙어 같이 버려진다."""
        segs = self._segs()
        if not segs:
            return
        f = self._file(segs[-1])
        try:
            with open(f, "rb+") as fh:
                size = fh.seek(0, os.SEEK_END)
                if size:
                    fh.seek(size - 1)
                    if fh.read(1) != b"\n":
                        fh.seek(size)
                        fh.write(b"\n")
                        fh.flush()
                    os.fsync(fh.fileno())
        except OSError:
            pass

    # ── 쓰기 ──
    def append(self, payload: dict) -> None:
        segs = self._segs()
        cur = segs[-1] if segs else 1
        if not segs or self._write_lines >= self.seg_lines:
            cur = (segs[-1] + 1) if segs else 1
            self._write_lines = 0
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self._file(cur), "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())        # 전원이 나가도 남게
        self._write_lines += 1
        self._trim()

    def _trim(self) -> None:
        while self._bytes() > self.max_bytes and len(self._segs()) > 1:
            n = self._segs()[0]
            lost = self._lines(n) - (self._pos["line"] if self._pos["seg"] == n else 0)
            os.remove(self._file(n))
            self.dropped += max(0, lost)
            if self._pos["seg"] == n:
                self._pos = {"seg": 0, "line": 0}
                self._save_pos()
            log.warning("보관함 상한(%d MB) — 가장 오래된 묶음 %d건을 지웠습니다.", self.max_bytes // (1024 * 1024), lost)

    # ── 읽기·보낸 만큼 넘기기 ──
    def peek(self, n: int = 50) -> list:
        """가장 오래된 것부터 최대 n건(깨진 줄은 건너뜀)."""
        out = []
        for seg in self._segs():
            skip = self._pos["line"] if seg == self._pos["seg"] else 0
            try:
                with open(self._file(seg), encoding="utf-8") as fh:
                    for i, line in enumerate(fh):
                        if i < skip:
                            continue
                        try:
                            out.append((seg, i, json.loads(line)))
                        except ValueError:
                            out.append((seg, i, None))      # 깨진 줄 — 보내지 않고 넘긴다
                        if len(out) >= n:
                            return out
            except OSError:
                continue
        return out

    def commit(self, upto) -> None:
        """peek가 준 (seg, line) 위치까지 보냈다. 다 보낸 파일은 지운다."""
        seg, line = upto
        for s in self._segs():
            if s < seg:
                os.remove(self._file(s))
        if line + 1 >= self._lines(seg):           # 이 파일을 다 보냈다 → 지운다
            if seg == (self._segs() or [0])[-1]:
                self._write_lines = 0             # 쓰는 중이던 파일이었다 — 다음 쓰기는 새 파일로
            os.remove(self._file(seg))
            self._pos = {"seg": 0, "line": 0}
        else:
            self._pos = {"seg": seg, "line": line + 1}
        self._save_pos()

    def __len__(self) -> int:
        total = 0
        for seg in self._segs():
            total += self._lines(seg) - (self._pos["line"] if seg == self._pos["seg"] else 0)
        return max(0, total)
