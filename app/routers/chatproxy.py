"""AI 챗봇(MaxKB) 리버스 프록시 — 포털 로그인 사용자만 접근.

MaxKB(8080)는 방화벽으로 외부 차단하고, 챗봇은 오직 이 경로를 통해서만 노출한다.
`/chat/*`는 공개 경로가 아니므로 AuthMiddleware가 로그인을 강제한다 →
결과적으로 '포털 로그인 = 챗봇 사용' 이 보장된다.
스트리밍(SSE) 답변을 그대로 흘려보내기 위해 httpx 스트리밍을 사용한다.
"""

import json

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import HTMLResponse, StreamingResponse

from ..config import MAXKB_URL, maxkb_configured

router = APIRouter()

# 챗봇 페이지에 주입할 '문서 포털로 돌아가기' 버튼 (MaxKB는 건드리지 않고 프록시에서 삽입)
# 상단 양쪽 모서리에 MaxKB 자체 아이콘(메뉴/공유/접기)이 있어 겹치므로 좌측 하단에 배치
_BACK_BUTTON = (
    '<a href="/" target="_top" style="position:fixed;left:16px;bottom:96px;z-index:2147483647;'
    'background:#2563eb;color:#fff;padding:8px 14px;border-radius:999px;'
    'font:600 13px/1 -apple-system,\'Malgun Gothic\',sans-serif;text-decoration:none;'
    'box-shadow:0 3px 12px rgba(16,24,40,.3)">← 문서 포털</a>'
)

# MaxKB 프론트엔드에 하드코딩된 영어 라벨을 한국어로 치환 (JS 파일을 건드리지 않음).
# SPA라 요소가 나중에 렌더링되므로 주기적으로 반영한다.
_I18N = {
    "Type your question": "질문을 입력하세요",
    "New Chat": "새 대화",
    "Chat History": "대화 기록",
}
_I18N_SCRIPT = (
    "<script>(function(){var M=" + json.dumps(_I18N, ensure_ascii=False) + ";"
    "function fix(){"
    "document.querySelectorAll('textarea[placeholder],input[placeholder]').forEach("
    "function(e){if(M[e.placeholder])e.placeholder=M[e.placeholder];});"
    "document.querySelectorAll('button,span,div,a').forEach(function(e){"
    "if(e.children.length===0){var t=e.textContent.trim();if(M[t])e.textContent=M[t];}});"
    "}new MutationObserver(fix).observe(document.documentElement,{subtree:true,childList:true});"
    "setInterval(fix,500);fix();})();</script>"
)

# 프록시가 다시 쓰거나 의미 없어지는 헤더는 전달하지 않는다
# accept-encoding 제거: 업스트림이 압축 없이 응답하게 해 raw 스트림을 그대로 전달
_DROP_REQ = {"host", "cookie", "content-length", "connection"}
# content-encoding은 보존해야 함 — raw 스트림(압축 그대로)을 전달하므로
# 브라우저가 이 헤더를 보고 압축을 해제한다. content-length/transfer-encoding은
# 스트리밍이라 길이가 바뀌므로 제거.
_DROP_RESP = {"content-length", "transfer-encoding", "connection"}


@router.api_route("/chat", methods=["GET"])
@router.api_route("/chat/{path:path}", methods=["GET", "POST"])
async def chat_proxy(request: Request, path: str = ""):
    if not maxkb_configured():
        raise HTTPException(503, "AI 챗봇이 설정되어 있지 않습니다.")
    url = f"{MAXKB_URL}/chat/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ}

    client = httpx.AsyncClient(timeout=300.0)
    upstream = client.build_request(request.method, url, content=body, headers=headers)
    resp = await client.send(upstream, stream=True)

    # 챗봇 페이지(HTML)에만 '포털로' 버튼 주입 — 나머지(에셋·API·스트리밍)는 그대로 전달
    if "text/html" in resp.headers.get("content-type", ""):
        raw = await resp.aread()  # httpx가 content-encoding에 따라 자동 해제
        await resp.aclose()
        await client.aclose()
        html = raw.decode("utf-8", errors="replace")
        inject = _BACK_BUTTON + _I18N_SCRIPT
        if "</body>" in html:
            html = html.replace("</body>", inject + "</body>", 1)
        else:
            html += inject
        # 압축 해제된 평문을 돌려주므로 content-encoding 등 원본 헤더는 버린다
        return HTMLResponse(html, status_code=resp.status_code)

    async def close():
        await resp.aclose()
        await client.aclose()

    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESP},
        background=BackgroundTask(close),
    )
