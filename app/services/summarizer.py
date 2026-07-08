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
            return summary.strip(), ", ".join(keywords)
    except Exception:
        logger.warning("Ollama 분석 실패 — 빈도 기반으로 폴백", exc_info=True)
    return _extractive_summary(text), ", ".join(_extractive_keywords(text))


def _ollama_analyze(text: str) -> tuple[str, list[str]]:
    prompt = (
        "다음 문서를 분석해 JSON 객체로만 답하라. 형식:\n"
        '{"summary": "핵심 내용을 \'- \'로 시작하는 3~5줄로 요약한 하나의 문자열",\n'
        ' "keywords": ["문서의 주제·분류·검색에 유용한 핵심 키워드(명사구) 5~10개"]}\n'
        "키워드는 한국어를 우선으로 하되, 고유명사·기술용어는 원문 표기를 유지하라.\n\n"
        f"--- 문서 시작 ---\n{text}\n--- 문서 끝 ---"
    )
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(
            {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json",
             "options": {"temperature": 0.2}}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        parsed = json.loads(json.loads(resp.read())["response"])
    summary = parsed.get("summary", "")
    if isinstance(summary, list):
        summary = "\n".join(str(line) for line in summary)
    keywords = [str(k).strip() for k in parsed.get("keywords", []) if str(k).strip()]
    return str(summary), keywords[:10]


def _extractive_summary(text: str, max_sentences: int = 5) -> str:
    """단어 빈도 점수로 핵심 문장을 뽑는 간단한 추출 요약 (LLM 불필요)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?다요음됨])\s+|\n+", text) if len(s.strip()) >= 10]
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


def _extractive_keywords(text: str, max_keywords: int = 8) -> list[str]:
    """최다 빈도 단어를 키워드로 사용 (LLM 불필요)."""
    freq = Counter(w for w in _words(text) if len(w) >= 2)
    return [w for w, _ in freq.most_common(max_keywords)]


def _words(text: str) -> list[str]:
    return re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
