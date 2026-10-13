"""The standalone /cli page stays reachable and delegates protected data calls."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(label, condition):
    if not condition:
        raise AssertionError(label)


source = open(os.path.join(ROOT, "wb_proxy.py"), encoding="utf-8").read()
page = open(os.path.join(ROOT, "cli.html"), encoding="utf-8").read()

check("cli asset exists", os.path.isfile(os.path.join(ROOT, "cli.html")))
check("cli route is registered", 'if path == "/cli":' in source)
check("cli is not panel-gated as a document", 'if path == "/cli":\n            return True' not in source)
check("cli page identifies version", "workbuddy-fd v0.2.0" in page)
check("cli page keeps the requested gateway default", "http://127.0.0.1:8788/v1" in page)
check("cli page fixes the requested model", "deepseek-v4.1-flash" in page)
check("cli page supports manual gateway edits", 'id="baseUrl"' in page)
check("cli page documents the selected DeepSeek key environment", "DEEPSEEK_API_KEY" in page)
check("cli page exposes OpenCode command", "--client opencode" in page)
check("cli page keeps panel login", "/panel/login" in page)
check("Docker image copies the page", "COPY wb_*.py dashboard.html cli.html ./" in open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read())
check("versioned compose uses v0.2.0", os.path.isfile(os.path.join(ROOT, "docker-compose.v0.2.0.yml")))

print("cli page assertions passed (10 checks)")
