# -*- coding: utf-8 -*-
"""探一下本机凭据里的 DeepSeek key 能用哪些模型（不打印 key）。"""
import sys
import re
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

import requests

cred = pathlib.Path.home() / ".dsh" / ".credentials.yaml"
txt = cred.read_text(encoding="utf-8")
m = re.search(r"^DEEPSEEK_API_KEY:\s*[\"']?([^\"'\s]+)", txt, re.M)
if not m:
    raise SystemExit("凭据库里没解析到 DEEPSEEK_API_KEY")
key = m.group(1)

base = "https://api.deepseek.com"
try:
    r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=25)
    print(f"GET {base}/models -> {r.status_code}")
    print(r.text[:1200])
except Exception as e:                                       # noqa: BLE001
    print(f"请求失败：{type(e).__name__}: {e}")
