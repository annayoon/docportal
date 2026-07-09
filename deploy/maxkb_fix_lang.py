#!/usr/bin/env python3
"""MaxKB 챗봇 앱 프롬프트에 '한국어 전용' 규칙 주입 — Qwen의 일본어/중국어 혼입 억제.

  curl -s https://raw.githubusercontent.com/annayoon/docportal/main/deploy/maxkb_fix_lang.py -o /tmp/f.py
  MAXKB_PASSWORD=$(sudo grep -h DOCPORTAL_MAXKB_PASSWORD /etc/systemd/system/docportal.service.d/*.conf | cut -d= -f2-) python3 /tmp/f.py

환경변수: MAXKB_URL(기본 http://127.0.0.1:8080), MAXKB_PASSWORD, APP_NAME(기본 'DocPortal 챗봇')
"""
import json
import os
import urllib.request

BASE = os.environ.get("MAXKB_URL", "http://127.0.0.1:8080").rstrip("/") + "/admin/api"
PW = os.environ.get("MAXKB_PASSWORD", "MaxKB@123..")
APP_NAME = os.environ.get("APP_NAME", "DocPortal 챗봇")
WS = "default"

SYSTEM = (
    "당신은 사내 문서 포털의 지식 도우미입니다. "
    "반드시 한국어로만 답하십시오. 일본어(かな·カタカナ)나 중국어(한자)를 절대 섞지 마십시오. "
    "외래어도 한글로 표기하십시오 — 예: 'ライセンス'(X) → '라이선스'(O), '管理'(X) → '관리'(O). "
    "한자 병기도 하지 마십시오."
)
PROMPT = (
    "알려진 정보: {data}\n"
    "위 문서 발췌만 근거로 질문에 답하라. 발췌에 없는 내용은 지어내지 말고 모른다고 답하라.\n"
    "답변은 반드시 한국어로만 작성하고, 일본어·중국어 문자를 절대 사용하지 마라.\n"
    "질문: {question}"
)


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def records(data):
    if isinstance(data, dict):
        return data.get("records", data.get("model", []))
    return data or []


tok = call("POST", "/user/login", {"username": "admin", "password": PW})["data"]["token"]
apps = records(call("GET", f"/workspace/{WS}/application/1/50", token=tok).get("data"))
app = next((a for a in apps if a.get("name") == APP_NAME), None)
if not app:
    print(f"'{APP_NAME}' 앱을 찾을 수 없습니다. 앱 목록: {[a.get('name') for a in apps]}")
    raise SystemExit(1)

detail = call("GET", f"/workspace/{WS}/application/{app['id']}", token=tok).get("data", {})
ms = dict(detail.get("model_setting") or {})
ms["system"] = SYSTEM
ms["prompt"] = PROMPT
ms.setdefault("no_references_prompt", "{question}")

r = call("PUT", f"/workspace/{WS}/application/{app['id']}",
         {"model_setting": ms, "prologue": detail.get("prologue") or ""}, token=tok)
print("업데이트 응답:", r.get("code"), r.get("message"))

# 확인
after = call("GET", f"/workspace/{WS}/application/{app['id']}", token=tok).get("data", {})
sys_now = (after.get("model_setting") or {}).get("system", "")
print("한국어 전용 규칙 반영:", "한국어로만" in sys_now)
