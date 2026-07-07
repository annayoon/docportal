"""문서 요약. Ollama(로컬 LLM)가 있으면 사용하고, 없으면 빈도 기반 추출 요약으로 폴백."""

import json
import logging
import re
import urllib.request
from collections import Counter

from ..config import OLLAMA_MODEL, OLLAMA_URL, SUMMARY_INPUT_LEN

logger = logging.getLogger(__name__)


def summarize(text: str) -> str:
    text = text.strip()[:SUMMARY_INPUT_LEN]
    if not text:
        return ""
    try:
        result = _ollama_summary(text)
        if result.strip():
            return result.strip()
    except Exception:
        logger.warning("Ollama 요약 실패 — 추출 요약으로 폴백", exc_info=True)
    return _extractive_summary(text)


def _ollama_summary(text: str) -> str:
    prompt = (
        "다음 문서의 핵심 내용을 한국어로 요약하라. "
        "3~5개의 요점을 '- '로 시작하는 목록으로 작성하고, 다른 말은 덧붙이지 마라.\n\n"
        f"--- 문서 시작 ---\n{text}\n--- 문서 끝 ---"
    )
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(
            {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
             "options": {"temperature": 0.2}}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"]


def _extractive_summary(text: str, max_sentences: int = 5) -> str:
    """단어 빈도 점수로 핵심 문장을 뽑는 간단한 추출 요약 (LLM 불필요)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?다요음됨])\s+|\n+", text) if len(s.strip()) >= 10]
    if not sentences:
        return text[:300]
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
    freq = Counter(words)
    scored = [
        (sum(freq[w] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", s)) / (len(s) ** 0.5), i, s)
        for i, s in enumerate(sentences)
    ]
    top = sorted(scored, reverse=True)[:max_sentences]
    top_in_order = [s for _, _, s in sorted(top, key=lambda t: t[1])]
    return "\n".join(f"- {s}" for s in top_in_order)
