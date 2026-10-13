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


if __name__ == "__main__":
    unittest.main(verbosity=2)
