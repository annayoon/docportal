"""민감정보 탐지·마스킹 + 금칙어 검사.

민감정보: 검색/요약/챗봇에 나가는 '추출 텍스트'를 마스킹한다 (원본 파일은 그대로).
금칙어: 사용자가 직접 입력하는 영역(제목·위키 본문·태그)만 검사해 차단한다.
"""

import re

# ── 한국형 민감정보 패턴 ──────────────────────────────────────────
# (이름, 정규식, 마스킹 함수) — 오탐을 줄이려 자릿수·구분자를 엄격히 잡는다.

def _mask_rrn(m: re.Match) -> str:
    # 주민등록번호 850101-1234567 → 850101-1******  (성별자리 1개만 남김)
    return f"{m.group(1)}-{m.group(2)[0]}{'*' * 6}"


def _mask_tail(keep_head: int, keep_tail: int):
    def f(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) <= keep_head + keep_tail:
            return "*" * len(m.group(0))
        return m.group(0)[:keep_head] + "*" * (len(m.group(0)) - keep_head - keep_tail) + m.group(0)[-keep_tail:]
    return f


# 순서 중요: 형식이 겹치는 패턴은 더 특징적인 것을 먼저 검사한다
# (주민번호 → 카드 → 휴대전화(01x 접두) → 계좌 → 이메일)
_PATTERNS = [
    # 주민등록번호: 6자리-(1~4로 시작하는)7자리
    ("주민등록번호", re.compile(r"\b(\d{6})-([1-4]\d{6})\b"), _mask_rrn),
    # 신용카드: 4-4-4-4 (구분자 - 또는 공백)
    ("신용카드번호", re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b"), _mask_tail(0, 4)),
    # 휴대전화: 010-1234-5678 (계좌번호와 형식이 겹치므로 먼저 잡는다)
    ("휴대전화", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"), _mask_tail(3, 4)),
    # 계좌번호: 숫자-숫자-숫자 형태 (하이픈 2개 이상)
    ("계좌번호", re.compile(r"\b\d{2,6}-\d{2,6}-\d{2,7}(?:-\d{1,6})?\b"), _mask_tail(2, 3)),
    # 이메일
    ("이메일", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
     lambda m: m.group(0)[0] + "***@" + m.group(0).split("@", 1)[1]),
]


def scan_and_mask(text: str) -> tuple[str, list[str]]:
    """텍스트에서 민감정보를 마스킹하고, 발견된 유형 목록을 함께 반환한다."""
    if not text:
        return text, []
    found: list[str] = []
    for name, pattern, masker in _PATTERNS:
        if pattern.search(text):
            found.append(name)
            text = pattern.sub(masker, text)
    return text, found


def find_banned(text: str, banned_words: list[str]) -> list[str]:
    """텍스트에 포함된 금칙어 목록을 반환한다 (대소문자 무시)."""
    if not text or not banned_words:
        return []
    low = text.lower()
    return [w for w in banned_words if w and w.lower() in low]
