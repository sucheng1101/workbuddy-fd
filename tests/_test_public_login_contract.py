"""Contract tests for reusable public OAuth login pages (v0.3.0).

These tests exercise the real ``wb_proxy.Handler`` dispatcher with an in-memory
request and a deterministic pool stub.  No upstream network or Docker service
is needed.  The suite deliberately keeps the public session id separate from
the upstream OAuth state: two visitors must never poll one another's login.

Run directly with::

    python tests/_test_public_login_contract.py
"""
import email
import html
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts  # noqa: E402
import wb_broker  # noqa: E402
import wb_proxy as proxy  # noqa: E402


class FakePool(object):
    """Deterministic account pool used by the public OAuth contract."""

    def __init__(self):
        self.started = []
        self.polls = {}
        self.counter = 0
        self.poll_options = []
        self.cancelled = []

    def start_login(self, realm, platform):
        self.counter += 1
        state = "upstream-state-%d" % self.counter
        self.started.append((realm, platform, state))
        self.polls[state] = [{"status": "pending", "message": "waiting"}]
        return {
            "state": state,
            "authUrl": "https://login.example.test/oauth?state=" + state,
        }

    def poll_login(self, state, enabled=True, review_status=None):
        self.poll_options.append((state, enabled, review_status))
        queue = self.polls.setdefault(state, [])
        if queue:
            return queue.pop(0)
        return {"status": "pending", "message": "waiting"}

    def cancel_login(self, state):
        self.cancelled.append(state)
        return True


class DispatchRequest(proxy.Handler):
    """Minimal real Handler request over memory-backed streams."""

    def __init__(self, path, headers=None, command="GET", body=b""):
        self.path = path
        self.command = command
        self.request_version = "HTTP/1.1"
        self.close_connection = True
        merged = dict(headers or {})
        if body and "Content-Length" not in merged:
            merged["Content-Length"] = str(len(body))
        raw = "".join("%s: %s\r\n" % item for item in merged.items())
        self.headers = email.message_from_string(raw)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.client_address = ("127.0.0.1", 0)

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass

    def log_message(self, fmt, *args):
        pass

    def dispatch(self):
        getattr(self, "do_" + self.command)()
        raw = self.wfile.getvalue().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        return self.status, payload, raw


class PublicLoginContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="public-login-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.pool = FakePool()
        self.page_snapshot = proxy._PUBLIC_LOGIN_PAGES
        self.session_snapshot = proxy._PUBLIC_OAUTH_SESSIONS
        proxy._PUBLIC_LOGIN_PAGES = {}
        proxy._PUBLIC_OAUTH_SESSIONS = {}
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        proxy._PUBLIC_LOGIN_PAGES = self.page_snapshot
        proxy._PUBLIC_OAUTH_SESSIONS = self.session_snapshot

    def dispatch(self, path, *, command="GET", payload=None, headers=None):
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        return DispatchRequest(path, headers=headers, command=command,
                               body=body).dispatch()

    def panel_headers(self):
        return {"X-Panel-Token": proxy.PANEL.create()}

    def create_page(self, slug="demo-page", **extra):
        payload = {"slug": slug, "name": "Demo", "realm": "intl",
                   "platform": "CLI"}
        payload.update(extra)
        status, body, _ = self.dispatch("/public-login/pages",
                                        command="POST", payload=payload,
                                        headers=self.panel_headers())
        self.assertEqual(status, 201, body)
        return body

    def patched_runtime(self):
        return mock.patch.multiple(
            proxy, ACCOUNTS_DIR=self.tmp.name, POOL=self.pool,
            CURRENT_REALM="intl")

    def test_admin_create_list_duplicate_pause_and_resume(self):
        # The registry is a management surface; anonymous writes and reads are
        # refused before the body is interpreted.
        status, body, _ = self.dispatch("/public-login/pages")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["message"], "panel password required")
        status, body, _ = self.dispatch("/public-login/pages",
                                        command="POST", payload={"slug": "x"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["message"], "panel password required")

        with self.patched_runtime():
            status, body, _ = self.dispatch("/public-login/pages", command="POST",
                                            payload={"slug": "!!!"},
                                            headers=self.panel_headers())
            self.assertEqual(status, 400)
            self.assertIn("slug", body["error"]["message"])
            page = self.create_page("代理 A!!")
            self.assertEqual(page["slug"], "a")
            self.assertEqual(page["public_url"], "/p/a")
            self.assertFalse(page["paused"])
            self.assertEqual(page["realm"], "intl")

            status, body, _ = self.dispatch("/public-login/pages",
                                            headers=self.panel_headers())
            self.assertEqual(status, 200)
            self.assertEqual([p["id"] for p in body["pages"]], [page["id"]])

            status, body, _ = self.dispatch("/public-login/pages",
                                            command="POST", payload={"slug": "a"},
                                            headers=self.panel_headers())
            self.assertEqual(status, 409)

            path = "/public-login/pages/%s/pause" % page["id"]
            status, body, _ = self.dispatch(path, command="POST", payload={},
                                            headers=self.panel_headers())
            self.assertEqual(status, 200)
            self.assertTrue(body["paused"])

            status, body, _ = self.dispatch("/p/a")
            self.assertEqual(status, 410)
            self.assertIn("paused", body["error"]["message"])

            path = "/public-login/pages/%s/resume" % page["id"]
            status, body, _ = self.dispatch(path, command="POST", payload={},
                                            headers=self.panel_headers())
            self.assertEqual(status, 200)
            self.assertFalse(body["paused"])

            status, _body, raw = self.dispatch("/p/a")
            self.assertEqual(status, 200)
            self.assertIn("开始登录", raw)
            self.assertIn("/p/a/oauth/start", raw)

    def test_public_start_uses_page_settings_and_is_reusable(self):
        with self.patched_runtime():
            page = self.create_page("shared", realm="cn", platform="WorkBuddy")

            first_status, first, _ = self.dispatch(
                "/p/shared/oauth/start", command="POST", payload={})
            second_status, second, _ = self.dispatch(
                "/p/shared/oauth/start", command="POST", payload={})
            self.assertEqual(first_status, 200)
            self.assertEqual(second_status, 200)
            self.assertEqual(self.pool.started[0][:2], ("cn", "WorkBuddy"))
            self.assertEqual(len(self.pool.started), 2)
            self.assertNotEqual(first["session"], second["session"])
            self.assertNotEqual(first["state"], second["state"])
            self.assertNotEqual(first["authUrl"], second["authUrl"])
            self.assertNotEqual(
                proxy._PUBLIC_OAUTH_SESSIONS[first["session"]]["code_verifier"],
                proxy._PUBLIC_OAUTH_SESSIONS[second["session"]]["code_verifier"],
            )
            self.assertEqual(first["expires_at"],
                             proxy._PUBLIC_OAUTH_SESSIONS[first["session"]]["expires_at"])
            self.assertEqual(proxy._PUBLIC_OAUTH_SESSIONS[first["session"]]["page_id"],
                             page["id"])
            self.assertEqual(proxy._PUBLIC_OAUTH_SESSIONS[first["session"]]["code_verifier"].__class__, str)

    def test_public_page_escapes_admin_supplied_name(self):
        with self.patched_runtime():
            page = self.create_page("escaped", name='</title><script>alert("x")</script>')
            status, _body, raw = self.dispatch("/p/escaped")
            self.assertEqual(status, 200)
            self.assertIn(html.escape(page["name"], quote=True), raw)
            self.assertNotIn("<script>alert(\"x\")</script>", raw)

    def test_poll_isolated_and_redacts_account_identity(self):
        with self.patched_runtime():
            self.create_page("poll")
            _, first, _ = self.dispatch("/p/poll/oauth/start", command="POST", payload={})
            _, second, _ = self.dispatch("/p/poll/oauth/start", command="POST", payload={})
            first_upstream = proxy._PUBLIC_OAUTH_SESSIONS[first["session"]]["upstream_state"]
            second_upstream = proxy._PUBLIC_OAUTH_SESSIONS[second["session"]]["upstream_state"]
            self.pool.polls[first_upstream] = [{"status": "pending"}, {
                "status": "ok", "account": {"nickname": "Alice", "realm": "intl",
                                               "uid": "SECRET-UID", "access_token": "SECRET"}}]
            self.pool.polls[second_upstream] = [{"status": "ok", "account": {
                "nickname": "Bob", "realm": "cn", "uid": "OTHER-UID"}}]

            status, pending, _ = self.dispatch(
                "/p/poll/oauth/poll?session=" + first["session"])
            self.assertEqual((status, pending["status"]), (200, "pending"))

            status, one, _ = self.dispatch(
                "/p/poll/oauth/poll?session=" + first["session"])
            self.assertEqual(status, 200)
            self.assertEqual(one["review_status"], "pending_review")
            self.assertEqual(one["account"], {"nickname": "Alice", "realm": "intl"})
            self.assertEqual(self.pool.poll_options[-1][1:], (False, "pending_review"))
            self.assertNotIn("uid", json.dumps(one))
            self.assertNotIn(first["session"], proxy._PUBLIC_OAUTH_SESSIONS)

            status, two, _ = self.dispatch(
                "/p/poll/oauth/poll?session=" + second["session"])
            self.assertEqual(status, 200)
            self.assertEqual(two["account"]["nickname"], "Bob")
            self.assertNotEqual(one["account"]["nickname"], two["account"]["nickname"])

            status, body, _ = self.dispatch(
                "/p/poll/oauth/poll?session=" + first["session"])
            self.assertEqual(status, 404)
            self.assertEqual(body["status"], "unknown")

    def test_unknown_expired_and_paused_public_sessions_are_errors(self):
        with self.patched_runtime():
            self.create_page("edges")
            status, body, _ = self.dispatch("/p/missing")
            self.assertEqual(status, 404)
            self.assertIn("not found", body["error"]["message"])

            status, body, _ = self.dispatch("/p/edges/oauth/poll?session=bogus")
            self.assertEqual(status, 404)
            self.assertEqual(body["status"], "unknown")

            _, started, _ = self.dispatch("/p/edges/oauth/start", command="POST", payload={})
            proxy._PUBLIC_OAUTH_SESSIONS[started["session"]]["expires_at"] = time.time() - 1
            status, body, _ = self.dispatch(
                "/p/edges/oauth/poll?session=" + started["session"])
            self.assertEqual(status, 410)
            self.assertEqual(body["status"], "expired")

            page_id = next(iter(proxy._PUBLIC_LOGIN_PAGES))
            self.dispatch("/public-login/pages/%s/pause" % page_id,
                          command="POST", payload={}, headers=self.panel_headers())
            status, body, _ = self.dispatch("/p/edges/oauth/start",
                                            command="POST", payload={})
            self.assertEqual(status, 410)
            self.assertIn("paused", body["error"]["message"])

    def test_cancel_releases_only_the_selected_public_session(self):
        with self.patched_runtime():
            self.create_page("cancel")
            _, first, _ = self.dispatch("/p/cancel/oauth/start", command="POST", payload={})
            _, second, _ = self.dispatch("/p/cancel/oauth/start", command="POST", payload={})
            first_upstream = proxy._PUBLIC_OAUTH_SESSIONS[first["session"]]["upstream_state"]
            second_upstream = proxy._PUBLIC_OAUTH_SESSIONS[second["session"]]["upstream_state"]

            status, body, _ = self.dispatch("/p/cancel/oauth/cancel", command="POST",
                                            payload={"session": first["session"]})
            self.assertEqual(status, 200)
            self.assertEqual(body, {"cancelled": True})
            self.assertEqual(self.pool.cancelled, [first_upstream])
            self.assertNotIn(first["session"], proxy._PUBLIC_OAUTH_SESSIONS)
            self.assertIn(second["session"], proxy._PUBLIC_OAUTH_SESSIONS)

            # A second cancel is idempotent and never cancels the other visitor.
            status, body, _ = self.dispatch("/p/cancel/oauth/cancel", command="POST",
                                            payload={"session": first["session"]})
            self.assertEqual(status, 200)
            self.assertEqual(body, {"cancelled": False})
            self.assertEqual(self.pool.cancelled, [first_upstream])
            self.assertEqual(
                proxy._PUBLIC_OAUTH_SESSIONS[second["session"]]["upstream_state"],
                second_upstream,
            )


class BrokerContractTests(unittest.TestCase):
    """代理业务闭环第一阶段：代理会话、白名单 DTO、越权拒绝、结算幂等。

    使用真实的 ``wb_accounts.AccountPool``（临时目录 + 合成账号）与真实的
    ``wb_proxy.Handler`` 分发器，不碰上游网络。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="broker-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.pool_dir = self.tmp.name
        self.pool = wb_accounts.AccountPool(self.pool_dir, log=lambda msg: None)
        self._saved = (dict(proxy._BROKERS), proxy.POOL, proxy.ACCOUNTS_DIR)
        proxy._BROKERS.clear()
        proxy.POOL = self.pool
        proxy.ACCOUNTS_DIR = self.pool_dir
        proxy._agent_login_attempts.clear()
        self.addCleanup(proxy._agent_login_attempts.clear)
        self.addCleanup(self._restore)

    def _restore(self):
        saved_brokers, pool, accounts_dir = self._saved
        proxy._BROKERS.clear()
        proxy._BROKERS.update(saved_brokers)
        proxy.POOL = pool
        proxy.ACCOUNTS_DIR = accounts_dir

    # ---- 基础工具 ----
    def dispatch(self, path, *, command="GET", payload=None, headers=None):
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        return DispatchRequest(path, headers=headers, command=command,
                               body=body).dispatch()

    def panel_headers(self):
        return {"X-Panel-Token": proxy.PANEL.create()}

    def agent_headers(self, token):
        return {"X-Agent-Token": token}

    def add_account(self, uid, nickname="", review="approved", enabled=True,
                    expires_in=3600, realm="intl"):
        data = {"uid": uid, "nickname": nickname, "accessToken": "",
                "realm": realm, "expiresAt": int(time.time()) + expires_in,
                "reviewStatus": review, "enabled": enabled}
        with open(os.path.join(self.pool_dir, uid + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh)
        self.pool.load()

    def create_agent(self, name="代理A", password="pw12345"):
        status, body, _ = self.dispatch("/broker/agents", command="POST",
                                        payload={"name": name, "password": password},
                                        headers=self.panel_headers())
        self.assertEqual(status, 201, body)
        return body

    def agent_login(self, entry, password="pw12345"):
        status, body, _ = self.dispatch("/agent/login", command="POST",
                                        payload={"entry": entry, "password": password})
        self.assertEqual(status, 200, body)
        return body["token"]

    def bind(self, agent_id, uid, expect=201):
        status, body, _ = self.dispatch(
            "/broker/agents/%s/bind" % agent_id, command="POST",
            payload={"uid": uid}, headers=self.panel_headers())
        self.assertEqual(status, expect, body)
        return body

    # ---- 代理实体与登录会话 ----
    def test_agent_lifecycle_login_logout_and_session_expiry(self):
        # 管理接口必须带面板会话。
        status, body, _ = self.dispatch("/broker/agents")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["message"], "panel password required")

        agent = self.create_agent("Acme", "secret-1")
        self.assertEqual(agent["entry"], "acme")
        self.assertNotIn("password_hash", json.dumps(agent))

        # 口令错误被拒。
        status, _body, _ = self.dispatch("/agent/login", command="POST",
                                         payload={"entry": "acme", "password": "nope"})
        self.assertEqual(status, 401)

        token = self.agent_login("acme", "secret-1")
        status, body, _ = self.dispatch("/agent/session",
                                        headers=self.agent_headers(token))
        self.assertEqual(status, 200)
        self.assertEqual(body["agent"]["id"], agent["id"])

        # 代理 token 打不开管理接口。
        status, _body, _ = self.dispatch("/broker/agents",
                                         headers=self.agent_headers(token))
        self.assertEqual(status, 401)

        # 退出后 session 立即失效。
        status, body, _ = self.dispatch("/agent/logout", command="POST", payload={},
                                        headers=self.agent_headers(token))
        self.assertEqual((status, body["logged_out"]), (200, True))
        status, _body, _ = self.dispatch("/agent/session",
                                         headers=self.agent_headers(token))
        self.assertEqual(status, 401)

        # 过期：把 broker 时钟推到 TTL 之后。
        token = self.agent_login("acme", "secret-1")
        real_clock = proxy.broker().clock
        proxy.broker().clock = lambda: time.time() + wb_broker.AGENT_SESSION_TTL + 60
        status, _body, _ = self.dispatch("/agent/session",
                                         headers=self.agent_headers(token))
        self.assertEqual(status, 401)
        proxy.broker().clock = real_clock

        # 暂停入口：登录被拒，已登录会话也被拒。
        token = self.agent_login("acme", "secret-1")
        status, body, _ = self.dispatch("/broker/agents/%s/pause" % agent["id"],
                                        command="POST", payload={},
                                        headers=self.panel_headers())
        self.assertEqual((status, body["paused"]), (200, True))
        status, _body, _ = self.dispatch("/agent/login", command="POST",
                                         payload={"entry": "acme", "password": "secret-1"})
        self.assertEqual(status, 403)
        # 暂停会立刻吊销该代理既有 session，因此这里 401（已被清理）或 403
        # （暂停闸门）都算通过。
        status, _body, _ = self.dispatch("/agent/session",
                                         headers=self.agent_headers(token))
        self.assertIn(status, (401, 403))

        status, body, _ = self.dispatch("/broker/agents/%s/resume" % agent["id"],
                                        command="POST", payload={},
                                        headers=self.panel_headers())
        self.assertEqual((status, body["paused"]), (200, False))
        self.agent_login("acme", "secret-1")

    # ---- 白名单 DTO 脱敏 ----
    def test_agent_account_dto_exposes_only_whitelisted_fields(self):
        self.add_account("uid-alice", nickname="Alice")
        agent = self.create_agent("Redact")
        self.bind(agent["id"], "uid-alice")
        token = self.agent_login(agent["entry"], "pw12345")

        status, body, raw = self.dispatch("/agent/accounts",
                                          headers=self.agent_headers(token))
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["effective_account_count"], 1)
        dto = body["accounts"][0]
        self.assertEqual(set(dto.keys()), {"nickname", "status", "login_at"})
        self.assertEqual(dto["nickname"], "Alice")
        self.assertEqual(dto["status"], "active")
        self.assertTrue(dto["login_at"])

        lowered = raw.lower()
        for forbidden in ("uid", "token", "credential", "quota", "usage",
                          "amount", "access", "refresh", "secret-uid"):
            self.assertNotIn(forbidden, lowered, "白名单 DTO 泄漏字段: %s" % forbidden)
        self.assertNotIn("uid-alice", raw)

    # ---- 跨代理越权 / 无删除路由 ----
    def test_cross_agent_operations_are_denied(self):
        self.add_account("uid-x", nickname="X")
        a = self.create_agent("AgentA")
        b = self.create_agent("AgentB")
        self.bind(a["id"], "uid-x", expect=201)

        # 同一账号不能绑到另一个代理。
        self.bind(b["id"], "uid-x", expect=403)
        for action in ("unbind", "freeze", "restore"):
            status, body, _ = self.dispatch(
                "/broker/agents/%s/%s" % (b["id"], action), command="POST",
                payload={"uid": "uid-x"}, headers=self.panel_headers())
            self.assertEqual(status, 403, (action, body))

        # 代理 session 不能碰管理员域。
        token = self.agent_login(a["entry"], "pw12345")
        status, _body, _ = self.dispatch("/broker/agents", headers=self.agent_headers(token))
        self.assertEqual(status, 401)
        status, _body, _ = self.dispatch(
            "/broker/agents/%s/unbind" % a["id"], command="POST",
            payload={"uid": "uid-x"}, headers=self.agent_headers(token))
        self.assertEqual(status, 401)

        # 绑定不存在的账号被拒。
        status, body, _ = self.dispatch(
            "/broker/agents/%s/bind" % a["id"], command="POST",
            payload={"uid": "nope"}, headers=self.panel_headers())
        self.assertEqual(status, 404)

    def test_agent_routes_have_no_delete(self):
        self.add_account("uid-y", nickname="Y")
        agent = self.create_agent("NoDelete")
        self.bind(agent["id"], "uid-y", expect=201)
        token = self.agent_login(agent["entry"], "pw12345")

        # 代理域没有删除路由：未知动作一律 404，不会落到删除实现上。
        for path in ("/agent/accounts/delete", "/agent/delete"):
            status, _body, _ = self.dispatch(path, command="POST", payload={"uid": "uid-y"},
                                             headers=self.agent_headers(token))
            self.assertEqual(status, 404, path)
        # 管理员域也没有代理删除路由。
        status, _body, _ = self.dispatch(
            "/broker/agents/%s/delete" % agent["id"], command="POST",
            payload={"uid": "uid-y"}, headers=self.panel_headers())
        self.assertEqual(status, 404)

        # 解绑/冻结是管理员域操作，代理 token 不能调用。
        status, _body, _ = self.dispatch(
            "/broker/agents/%s/unbind" % agent["id"], command="POST",
            payload={"uid": "uid-y"}, headers=self.agent_headers(token))
        self.assertEqual(status, 401)

    # ---- 结算：按有效账号计数、幂等、确认可追踪 ----
    def test_settlement_counts_effective_accounts_and_is_idempotent(self):
        self.add_account("u-active", nickname="Active")
        self.add_account("u-paused", nickname="Paused", enabled=False)
        self.add_account("u-expired", nickname="Expired", expires_in=-3600)
        self.add_account("u-pending", nickname="Pending", review="pending_review")
        self.add_account("u-active-b", nickname="ActiveB")

        a = self.create_agent("AgentA")
        b = self.create_agent("AgentB")
        for uid in ("u-active", "u-paused", "u-expired"):
            self.bind(a["id"], uid, expect=201)
        for uid in ("u-pending", "u-active-b"):
            self.bind(b["id"], uid, expect=201)

        # 冻结的活跃账号不计入有效条数。
        self.bind(a["id"], "u-active", expect=200)  # 幂等绑定
        status, _body, _ = self.dispatch(
            "/broker/agents/%s/freeze" % a["id"], command="POST",
            payload={"uid": "u-active"}, headers=self.panel_headers())
        self.assertEqual(status, 200)
        status, _body, _ = self.dispatch(
            "/broker/agents/%s/restore" % a["id"], command="POST",
            payload={"uid": "u-active"}, headers=self.panel_headers())
        self.assertEqual(status, 200)

        status, body, _ = self.dispatch("/broker/settlements", command="POST",
                                        payload={"period": "2026-10"},
                                        headers=self.panel_headers())
        self.assertEqual(status, 200, body)
        self.assertFalse(body["duplicate"])
        counts = {item["agent_id"]: item["effective_account_count"]
                  for item in body["items"]}
        self.assertEqual(counts[a["id"]], 1)   # 只有 u-active 有效
        self.assertEqual(counts[b["id"]], 1)   # 只有 u-active-b 有效
        self.assertEqual(body["total_effective_accounts"], 2)
        self.assertEqual(body["status"], "draft")
        self.assertNotIn("amount", json.dumps(body).lower())

        # 重复生成同周期是幂等的：同一张单据、计数不变、不新增。
        status, again, _ = self.dispatch("/broker/settlements", command="POST",
                                         payload={"period": "2026-10"},
                                         headers=self.panel_headers())
        self.assertEqual(status, 200)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], body["id"])
        self.assertEqual(again["total_effective_accounts"], 2)
        status, listing, _ = self.dispatch("/broker/settlements",
                                           headers=self.panel_headers())
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["settlements"]), 1)

        # 确认后状态可追踪，且不可重复确认。
        status, confirmed, _ = self.dispatch(
            "/broker/settlements/%s/confirm" % body["id"], command="POST",
            payload={}, headers=self.panel_headers())
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertTrue(confirmed["confirmed_at"])
        status, _body, _ = self.dispatch(
            "/broker/settlements/%s/confirm" % body["id"], command="POST",
            payload={}, headers=self.panel_headers())
        self.assertEqual(status, 409)

        # 代理只能看到自己的条数摘要，看不到另一个代理的。
        token = self.agent_login(b["entry"], "pw12345")
        status, mine, raw = self.dispatch("/agent/settlements",
                                          headers=self.agent_headers(token))
        self.assertEqual(status, 200)
        self.assertEqual(len(mine["settlements"]), 1)
        items = mine["settlements"][0]["items"]
        self.assertEqual([item["agent_id"] for item in items], [b["id"]])
        self.assertEqual(items[0]["effective_account_count"], 1)
        self.assertNotIn(a["id"], raw)
        self.assertNotIn("amount", raw.lower())

        # 冻结后有效条数下降，可在下一个周期重新生成。
        status, _body, _ = self.dispatch(
            "/broker/agents/%s/freeze" % b["id"], command="POST",
            payload={"uid": "u-active-b"}, headers=self.panel_headers())
        self.assertEqual(status, 200)
        status, body2, _ = self.dispatch("/broker/settlements", command="POST",
                                         payload={"period": "2026-11"},
                                         headers=self.panel_headers())
        self.assertEqual(status, 200)
        counts2 = {item["agent_id"]: item["effective_account_count"]
                   for item in body2["items"]}
        self.assertEqual(counts2[b["id"]], 0)

    def test_agent_login_is_rate_limited_per_source(self):
        agent = self.create_agent("Locked", "correct-pw")
        client_a = {"X-Real-IP": "203.0.113.7"}
        client_b = {"X-Real-IP": "203.0.113.8"}
        with mock.patch.object(proxy.time, "sleep", lambda _s: None):
            for _ in range(5):
                status, _body, _ = self.dispatch(
                    "/agent/login", command="POST",
                    payload={"entry": "locked", "password": "nope"},
                    headers=client_a)
                self.assertEqual(status, 401)
            # 失败落在 A 的真实来源上，而不是 socket 对端。
            self.assertEqual(set(proxy._agent_login_attempts), {"203.0.113.7"})
            # 第 6 次即使密码正确也因为同一来源被锁而 429。
            status, body, _ = self.dispatch(
                "/agent/login", command="POST",
                payload={"entry": "locked", "password": "correct-pw"},
                headers=client_a)
            self.assertEqual(status, 429)
            self.assertIn("too many login attempts", body["error"]["message"])
            # 同一时刻、同一网关：来源 B 照常登录，不被 A 连坐。
            status, body, _ = self.dispatch(
                "/agent/login", command="POST",
                payload={"entry": "locked", "password": "correct-pw"},
                headers=client_b)
            self.assertEqual(status, 200, body)
            self.assertIn("token", body)
            self.assertEqual(agent["entry"], "locked")

    def test_agent_login_success_clears_bucket_and_pause_is_not_a_failure(self):
        agent = self.create_agent("Dual", "correct-pw")
        client = {"X-Real-IP": "203.0.113.7"}
        with mock.patch.object(proxy.time, "sleep", lambda _s: None):
            for _ in range(3):
                status, _body, _ = self.dispatch(
                    "/agent/login", command="POST",
                    payload={"entry": "dual", "password": "bad"},
                    headers=client)
                self.assertEqual(status, 401)
            # 成功登录清桶：旧账不残留，再错 3 次也不会触发限流。
            status, body, _ = self.dispatch(
                "/agent/login", command="POST",
                payload={"entry": "dual", "password": "correct-pw"},
                headers=client)
            self.assertEqual(status, 200, body)
            self.assertNotIn("203.0.113.7", proxy._agent_login_attempts)
            for _ in range(3):
                status, _body, _ = self.dispatch(
                    "/agent/login", command="POST",
                    payload={"entry": "dual", "password": "bad"},
                    headers=client)
                self.assertEqual(status, 401)

            # 暂停入口的 403 不计入失败次数，也不该把管理员的操作变成爆破。
            proxy._agent_login_attempts.clear()
            self.dispatch("/broker/agents/%s/pause" % agent["id"], command="POST",
                          payload={}, headers=self.panel_headers())
            status, _body, _ = self.dispatch(
                "/agent/login", command="POST",
                payload={"entry": "dual", "password": "correct-pw"},
                headers=client)
            self.assertEqual(status, 403)
            self.assertEqual(proxy._agent_login_attempts, {})

    def test_agent_login_rate_limit_is_separate_from_panel_login(self):
        # 代理域爆破不能锁死管理员面板登录：两个桶互不影响。
        with mock.patch.object(proxy.time, "sleep", lambda _s: None):
            for _ in range(5):
                status, _body, _ = self.dispatch(
                    "/agent/login", command="POST",
                    payload={"entry": "nobody", "password": "nope"},
                    headers={"X-Real-IP": "203.0.113.7"})
                self.assertEqual(status, 401)
            status, _body, _ = self.dispatch(
                "/agent/login", command="POST",
                payload={"entry": "nobody", "password": "nope"},
                headers={"X-Real-IP": "203.0.113.7"})
            self.assertEqual(status, 429)
            self.assertEqual(proxy._login_attempts, {})
            # 同一来源用正确面板密码照样能进面板。
            status, body, _ = self.dispatch(
                "/panel/login", command="POST",
                payload={"password": proxy.wb_settings.DEFAULT_PANEL_PASSWORD},
                headers={"X-Real-IP": "203.0.113.7"})
            self.assertEqual(status, 200, body)
            self.assertIs(body["ok"], True)

    def test_agent_pending_review_listing_is_admin_only(self):
        self.add_account("u-p1", nickname="P1", review="pending_review")
        status, body, _ = self.dispatch("/broker/agents/pending",
                                        headers=self.panel_headers())
        self.assertEqual(status, 200)
        self.assertEqual([row["uid"] for row in body["pending"]], ["u-p1"])
        self.assertEqual(body["pending"][0]["nickname"], "P1")
        # 匿名访问被拒。
        status, _body, _ = self.dispatch("/broker/agents/pending")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
