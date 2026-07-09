#!/usr/bin/env python3
"""MaxKB 초기 설정 — 서버에서 실행.

기존 Ollama(gemma4:latest LLM + bge-m3 임베딩)를 등록하고
지식베이스 'DocPortal'과 챗봇 앱을 만든 뒤 공유 링크를 출력한다.
여러 번 실행해도 안전(멱등): 이미 있으면 재사용한다.

환경변수:
  MAXKB_URL       (기본 http://127.0.0.1:8080)
  MAXKB_USER      (기본 admin)
  MAXKB_PASSWORD  (기본 MaxKB@123..)
  MAXKB_PUBLIC_IP (챗봇 링크에 쓸 주소, 기본 10.0.112.200)
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("MAXKB_URL", "http://127.0.0.1:8080").rstrip("/") + "/admin/api"
WS = "default"
USER = os.environ.get("MAXKB_USER", "admin")
PW = os.environ.get("MAXKB_PASSWORD", "MaxKB@123..")
PUBLIC_IP = os.environ.get("MAXKB_PUBLIC_IP", "10.0.112.200")
CRED = {"api_base": "http://127.0.0.1:11434", "api_key": "none"}


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ERR": e.code, "body": e.read().decode(errors="replace")[:400]}


def main():
    tok = call("POST", "/user/login", {"username": USER, "password": PW})["data"]["token"]

    def models():
        return call("GET", "/workspace/%s/model_list" % WS, None, tok)["data"]["model"]

    existing = models()
    types = {m["model_type"] for m in existing}
    if "EMBEDDING" not in types:
        r = call("POST", "/workspace/%s/model" % WS, {
            "name": "bge-m3", "provider": "model_ollama_provider",
            "model_type": "EMBEDDING", "model_name": "bge-m3:latest", "credential": CRED}, tok)
        print("bge-m3 등록:", r.get("code") or r)
    if "LLM" not in types:
        r = call("POST", "/workspace/%s/model" % WS, {
            "name": "gemma4", "provider": "model_ollama_provider",
            "model_type": "LLM", "model_name": "gemma4:latest", "credential": CRED}, tok)
        print("gemma4 등록:", r.get("code") or r)

    ml = models()
    print("현재 모델:", [(m["name"], m["model_type"], m["status"]) for m in ml])
    emb = next((m["id"] for m in ml if m["model_type"] == "EMBEDDING"), None)
    llm = next((m["id"] for m in ml if m["model_type"] == "LLM"), None)
    if not emb or not llm:
        print("!! 임베딩/LLM 모델이 준비되지 않았습니다. 중단합니다.")
        return

    # 지식베이스 — 이름으로 재사용
    kbs = call("GET", "/workspace/%s/knowledge" % WS, None, tok).get("data") or {}
    kb_records = kbs.get("records", kbs) if isinstance(kbs, dict) else kbs
    kb = next((k for k in kb_records if k.get("name") == "DocPortal"), None)
    if kb:
        kb_id = kb["id"]
        print("KB 재사용:", kb_id)
    else:
        r = call("POST", "/workspace/%s/knowledge/base" % WS, {
            "name": "DocPortal", "desc": "DocPortal 자동 동기화",
            "embedding_model_id": emb, "folder_id": "default"}, tok)
        kb_id = r.get("data", {}).get("id") if r.get("code") == 200 else None
        print("KB 생성:", r.get("code") or r)
    if not kb_id:
        print("!! 지식베이스 생성 실패. 중단합니다.")
        return

    # 챗봇 앱 — 이름으로 재사용
    apps = call("GET", "/workspace/%s/application/1/50" % WS, None, tok).get("data") or {}
    app_records = apps.get("records", []) if isinstance(apps, dict) else apps
    app = next((a for a in app_records if a.get("name") == "DocPortal 챗봇"), None)
    if app:
        app_id = app["id"]
        print("앱 재사용:", app_id)
    else:
        r = call("POST", "/workspace/%s/application" % WS, {
            "name": "DocPortal 챗봇", "desc": "사내 문서 Q&A", "type": "SIMPLE",
            "folder_id": "default", "model_id": llm, "dialogue_number": 1,
            "prologue": "안녕하세요! 사내 문서를 기반으로 답변해 드립니다. 무엇이 궁금하세요?",
            "knowledge_id_list": [kb_id],
            "knowledge_setting": {
                "top_n": 3, "similarity": 0.45, "max_paragraph_char_number": 5000,
                "search_mode": "embedding",
                "no_references_setting": {"status": "ai_questioning", "value": "{question}"}},
            "model_setting": {
                "prompt": "알려진 정보: {data}\n위 문서 발췌만 근거로 질문에 한국어로 답하라. 발췌에 없는 내용은 지어내지 말고 모른다고 답하라.\n질문: {question}",
                "system": "당신은 사내 문서 포털의 지식 도우미입니다.",
                "no_references_prompt": "{question}", "reasoning_content_enable": False},
            "problem_optimization": False}, tok)
        app_id = r.get("data", {}).get("id") if r.get("code") == 200 else None
        print("앱 생성:", r.get("code") or r)
    if not app_id:
        print("!! 챗봇 앱 생성 실패. 중단합니다.")
        return

    call("PUT", "/workspace/%s/application/%s/publish" % (WS, app_id), {}, tok)
    at = call("GET", "/workspace/%s/application/%s/access_token" % (WS, app_id), None, tok)
    token_val = (at.get("data") or {}).get("access_token")

    print("\n=========================================")
    print("KB_ID    =", kb_id)
    print("CHAT_URL = http://%s:8080/chat/%s" % (PUBLIC_IP, token_val))
    print("=========================================")


if __name__ == "__main__":
    main()
