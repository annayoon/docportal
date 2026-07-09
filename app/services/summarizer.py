"""문서 요약 + 키워드 추출. Ollama(로컬 LLM)가 있으면 사용하고, 없으면 빈도 기반으로 폴백."""

import json
import logging
import re
import urllib.request
from collections import Counter

from ..config import OLLAMA_MODEL, OLLAMA_URL, SUMMARY_INPUT_LEN

logger = logging.getLogger(__name__)


def analyze_version(version_id: int) -> None:
    """업로드 직후 백그라운드에서 실행 — 버전 본문을 요약·키워드 추출해 캐시하고 재인덱싱.

    실패해도 업로드에는 영향 없음(수동 [요약 생성] 버튼으로 재시도 가능).
    """
    from ..db import get_conn, reindex_document  # 순환 import 방지

    conn = get_conn()
    try:
        ver = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
        if ver is None or not ver["content_text"].strip():
            return
        summary, keywords = analyze(ver["content_text"])
        conn.execute(
            "UPDATE versions SET summary = ?, keywords = ? WHERE id = ?",
            (summary, keywords, version_id),
        )
        # 키워드가 검색에 잡히도록 문서 인덱스 갱신
        reindex_document(conn, ver["document_id"])
        conn.commit()
        # MaxKB 지식베이스에도 최신 본문 반영 (미설정이면 no-op)
        from .maxkb import sync_async

        sync_async(ver["document_id"])
    except Exception:
        logger.exception("자동 요약/키워드 추출 실패: version_id=%s", version_id)
    finally:
        conn.close()


def analyze(text: str) -> tuple[str, str]:
    """본문에서 (요약, 키워드 콤마 문자열)을 뽑는다."""
    text = text.strip()[:SUMMARY_INPUT_LEN]
    if not text:
        return "", ""
    try:
        summary, keywords = _ollama_analyze(text)
        if summary.strip():
            # LLM이 키워드를 못 주면 키워드만 빈도 기반으로 보충
            if not keywords:
                keywords = _extractive_keywords(text)
            return summary.strip(), ", ".join(keywords)
    except Exception:
        logger.warning("Ollama 분석 실패 — 빈도 기반으로 폴백", exc_info=True)
    return _extractive_summary(text), ", ".join(_extractive_keywords(text))


def _ollama_analyze(text: str) -> tuple[str, list[str]]:
    """평문 프로토콜('요약:'/'키워드:' 섹션) — JSON 강제보다 모델 편차에 훨씬 강하다."""
    prompt = (
        "당신은 사내 문서 요약기다. 아래 문서를 읽고, 문서를 베끼지 말고 "
        "정확히 다음 형식으로만 답하라:\n\n"
        "요약:\n- (핵심 내용을 새 문장으로 3~5줄)\n\n"
        "키워드: (주제·분류·검색에 유용한 핵심 키워드 5~10개, 쉼표로 구분)\n\n"
        f"--- 문서 시작 ---\n{text}\n--- 문서 끝 ---"
    )
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(
            {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
             # num_ctx를 명시하지 않으면 기본(4096)보다 긴 문서에서 지시문이
             # 잘려 빈 응답이 나온다 — 8천자 본문 + 지시 + 출력 여유분
             "options": {"temperature": 0.2, "num_predict": 1200, "num_ctx": 10240}}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = json.loads(resp.read())["response"].strip()

    # qwen3 계열 등이 앞에 붙이는 사고 과정 제거 (출력 한도에 걸려 안 닫힌 경우 포함)
    raw = re.sub(r"<think>.*?(</think>|\Z)", "", raw, flags=re.S).strip()

    # '요약:' ~ '키워드:' 구간 추출 (마커가 없으면 전체를 요약 후보로)
    summary_match = re.search(r"요약\s*[::]?\s*\n?(.+?)(?=\n\s*키워드\s*[::]|\Z)", raw, re.S)
    summary = (summary_match.group(1) if summary_match else raw).strip()
    summary = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", summary).strip()

    keyword_match = re.search(r"키워드\s*[::]\s*(.+)", raw)
    keywords = []
    if keyword_match:
        keywords = [
            k.strip(" #·—-*") for k in re.split(r"[,、\n]", keyword_match.group(1))
            if 1 < len(k.strip(" #·—-*")) <= 30
        ][:10]

    # 안전장치: 원문 메아리·깨진 응답은 요약으로 쓰지 않는다 (호출부가 폴백 처리)
    if not summary or len(summary) > 1200:
        raise ValueError(f"요약 형식 아님(길이 {len(summary)}): {raw[:120]!r}")
    if summary.lstrip().startswith("{") or summary[:200] in text:
        raise ValueError(f"원문 복사/JSON 응답으로 판단: {raw[:120]!r}")
    return summary, keywords


def _extractive_summary(text: str, max_sentences: int = 5) -> str:
    """단어 빈도 점수로 핵심 문장을 뽑는 간단한 추출 요약 (LLM 불필요)."""
    # PDF 추출 텍스트는 문장 중간에 줄바꿈이 있으므로, 먼저 한 줄로 합친 뒤 문장 경계로 자른다
    joined = re.sub(r"\s*\n\s*", " ", text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", joined) if len(s.strip()) >= 15]
    if len(sentences) < 2:  # 마침표가 거의 없는 문서는 종결어미로 재시도
        sentences = [s.strip() for s in re.split(r"(?<=[다요음됨])\s+", joined) if len(s.strip()) >= 15]
    if not sentences:
        return text[:300]
    freq = Counter(_words(text))
    scored = [
        (sum(freq[w] for w in _words(s)) / (len(s) ** 0.5), i, s)
        for i, s in enumerate(sentences)
    ]
    top = sorted(scored, reverse=True)[:max_sentences]
    top_in_order = [s for _, _, s in sorted(top, key=lambda t: t[1])]
    return "\n".join(f"- {s}" for s in top_in_order)


# 폴백 키워드에서 걸러낼 기능어 (조사 제거 후 기준)
_STOPWORDS = {
    "있다", "있는", "없다", "한다", "하는", "했다", "된다", "되는", "대한", "대해",
    "통해", "위해", "위한", "경우", "때문", "따라", "다음", "해당", "관련", "이용",
    "사용", "제공", "가능", "필요", "그림", "결과", "방법", "내용", "정보", "것이",
    "그리고", "그러나", "하지만", "또한", "이러한", "있으며", "합니다", "됩니다",
}
# 단어 끝에 붙은 흔한 조사 — 떼어내고 빈도를 합산한다 ("정보를"+"정보의"→"정보")
_JOSA = ("이라는", "라는", "에서", "으로", "까지", "부터", "에게",
         "을", "를", "이", "가", "은", "는", "의", "에", "로", "와", "과", "도", "만")


def _strip_josa(word: str) -> str:
    for josa in _JOSA:  # 긴 조사부터 매칭되도록 위 튜플은 길이순 정렬돼 있음
        if word.endswith(josa) and len(word) - len(josa) >= 2:
            return word[: -len(josa)]
    return word


def _extractive_keywords(text: str, max_keywords: int = 8) -> list[str]:
    """조사를 떼고 기능어를 거른 빈도 상위 단어 (LLM 불필요한 폴백용)."""
    freq: Counter = Counter()
    for w in _words(text):
        w = _strip_josa(w)
        if len(w) >= 2 and w not in _STOPWORDS:
            freq[w] += 1
    return [w for w, _ in freq.most_common(max_keywords)]


def _words(text: str) -> list[str]:
    return re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
