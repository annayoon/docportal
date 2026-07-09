#!/usr/bin/env python3
"""MaxKB 지식베이스/앱 내용 조회 (읽기 전용) — 챗봇이 뭘 근거로 답하는지 진단용.

  curl -s https://raw.githubusercontent.com/annayoon/docportal/main/deploy/maxkb_inspect.py -o /tmp/i.py
  python3 /tmp/i.py

환경변수: MAXKB_URL(기본 http://127.0.0.1:8080), MAXKB_PASSWORD(기본 MaxKB@123..)
"""
import json
import os
import urllib.request

BASE = os.environ.get("MAXKB_URL", "http://127.0.0.1:8080").rstrip("/") + "/admin/api"
PW = os.environ.get("MAXKB_PASSWORD", "MaxKB@123..")
WS = "default"


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

print("=== 지식베이스 목록 ===")
kbs = records(call("GET", f"/workspace/{WS}/knowledge", token=tok).get("data"))
for kb in kbs:
    print(f"\n[KB] {kb.get('name')}  (id={kb.get('id')})")
    try:
        docs = records(call("GET", f"/workspace/{WS}/knowledge/{kb['id']}/document", token=tok).get("data"))
        print(f"  문서 {len(docs)}개:")
        for d in docs:
            print(f"    - {d.get('name')}  (단락 {d.get('paragraph_count','?')})")
    except Exception as e:
        print(f"  (문서 조회 실패: {e})")

print("\n=== 챗봇 앱과 연결된 지식베이스 ===")
apps = records(call("GET", f"/workspace/{WS}/application/1/50", token=tok).get("data"))
for a in apps:
    detail = call("GET", f"/workspace/{WS}/application/{a['id']}", token=tok).get("data", {})
    kb_ids = detail.get("knowledge_id_list") or detail.get("dataset_id_list") or []
    print(f"[APP] {a.get('name')} → 연결 KB: {kb_ids}")
