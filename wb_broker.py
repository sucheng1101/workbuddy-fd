"""代理业务闭环（v0.5.0 最小可验收实现）。

本模块只做「代理实体 / 登录会话 / 账号绑定 / 条数结算」四件事，全部用标准库
把状态落到 ``accounts/`` 下的独立 JSON 文件，不引入数据库或第三方依赖，也不
新增任何金额字段。

设计边界（见 docs/SPEC-v0.1.0.md、docs/SPEC-v0.3.0.md）：

- 代理是独立的实体，管理员可以创建、暂停、恢复、轮换其登录入口；
- 代理登录后拿到独立 session（内存态，进程重启即失效），可退出或自然过期；
- 账号通过 ``account_bindings`` 归属到某个代理入口，一个账号同一时刻只属于
  一个代理；绑定/解绑/冻结/恢复只由管理员操作；
- 结算只记录「有效账号条数」快照，管理员确认后状态可追踪；同一周期重复生成
  是幂等的（返回既有单据，不覆盖、不重复计数）。

本模块不直接依赖账号池：有效账号判定所需的实时字段由调用方（wb_proxy）解析
后传入，这样即使账号池是测试桩也能一致地工作。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

AGENT_SESSION_TTL = 8 * 3600
PASSWORD_ITERATIONS = 100000
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
ENTRY_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

# 有效账号 = 已审核通过、已启用、未过期；冻结是绑定层的额外闸门。
STATUS_ACTIVE = "active"
ENTRY_STATUS_ACTIVE = "active"
ENTRY_STATUS_PAUSED = "paused"
STATUS_PENDING = "pending_review"
STATUS_REJECTED = "rejected"
STATUS_PAUSED = "paused"
STATUS_EXPIRED = "expired"
STATUS_FROZEN = "frozen"
SETTLEMENT_DRAFT = "draft"
SETTLEMENT_CONFIRMED = "confirmed"


class BrokerError(Exception):
    """带 HTTP 语义的业务错误。调用方把 ``code`` 直接映射到响应状态码。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)


def account_status(review_status, enabled, expires_at, now=None):
    """把账号实时字段归一到代理可见状态之一。

    判定顺序：审核态 > 启用态 > 过期态 > 有效。冻结由绑定层单独叠加。
    """
    now = time.time() if now is None else now
    review = str(review_status or "approved").strip().lower()
    if review in ("pending_review", "pending"):
        return STATUS_PENDING
    if review in ("rejected", "denied"):
        return STATUS_REJECTED
    if not enabled:
        return STATUS_PAUSED
    try:
        expires = float(expires_at or 0)
    except (TypeError, ValueError):
        expires = 0.0
    if expires and expires <= now:
        return STATUS_EXPIRED
    return STATUS_ACTIVE


def task_eligible(account):
    """账号是否可参与自动任务/签到/福利：已启用且审核通过。

    公开登录成功后的「正常账号」会自动写 enabled=True、
    review_status=approved，因此直接通过；被停用、待审核或已拒绝的账号
    不参与任何自动任务，直到管理员显式处理。
    """
    if account is None:
        return False
    if not getattr(account, "enabled", False):
        return False
    review = str(getattr(account, "review_status", "approved") or "approved")
    return review.strip().lower() == "approved"


def normalize_period(period):
    period = str(period or "").strip()
    if not PERIOD_RE.match(period):
        raise BrokerError(400, "period must be formatted as YYYY-MM")
    return period


def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt.encode("utf-8"),
        PASSWORD_ITERATIONS)
    return "pbkdf2_sha256$%s$%s" % (salt, digest.hex())


def _verify_password(stored, password):
    if not stored:
        return False
    try:
        scheme, salt, digest = str(stored).split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    candidate = _hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(candidate, digest)


def _atomic_write(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return default
    return raw if isinstance(raw, dict) else default


class Broker(object):
    """代理、会话、绑定与结算的进程内状态 + 独立 JSON 持久化。"""

    def __init__(self, directory, log=None, clock=None):
        self.dir = directory
        self.log = log or (lambda msg: None)
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._agents = {}
        self._bindings = {}
        self._settlements = {}
        self._sessions = {}
        self.load()

    # ---- 路径与持久化 ----
    def _path(self, name):
        return os.path.join(self.dir, name)

    def load(self):
        with self._lock:
            agents = _read_json(self._path("proxy_agents.json"), {})
            bindings = _read_json(self._path("proxy_bindings.json"), {})
            settlements = _read_json(self._path("proxy_settlements.json"), {})
            self._agents = agents.get("agents") if isinstance(agents.get("agents"), dict) else {}
            self._bindings = bindings.get("bindings") if isinstance(bindings.get("bindings"), dict) else {}
            self._settlements = settlements.get("settlements") if isinstance(settlements.get("settlements"), dict) else {}
            return self

    def _save_agents(self):
        _atomic_write(self._path("proxy_agents.json"), {"agents": self._agents})

    def _save_bindings(self):
        _atomic_write(self._path("proxy_bindings.json"), {"bindings": self._bindings})

    def _save_settlements(self):
        _atomic_write(self._path("proxy_settlements.json"), {"settlements": self._settlements})

    # ---- 视图 ----
    @staticmethod
    def agent_view(agent):
        public_slug = str(agent.get("public_slug") or "").strip()
        paused = bool(agent.get("paused"))
        return {
            "id": agent.get("id"),
            "name": agent.get("name"),
            "entry": agent.get("entry"),
            "public_slug": public_slug or None,
            "login_url": ("/p/%s" % public_slug) if public_slug else None,
            "entry_status": ENTRY_STATUS_PAUSED if paused else ENTRY_STATUS_ACTIVE,
            "paused": paused,
            "created_at": agent.get("created_at"),
            "updated_at": agent.get("updated_at"),
        }

    def list_agents(self):
        with self._lock:
            agents = [self.agent_view(a) for a in self._agents.values()]
        agents.sort(key=lambda a: (a.get("created_at") or 0))
        return agents

    def agents(self):
        """所有代理原始记录（副本），供调用方做惰性关联/迁移。"""
        with self._lock:
            return [dict(a) for a in self._agents.values()]

    def get_agent(self, agent_id):
        with self._lock:
            return self._agents.get(str(agent_id))

    def _require_agent(self, agent_id):
        agent = self.get_agent(agent_id)
        if not agent:
            raise BrokerError(404, "agent not found")
        return agent

    def _unique_entry(self, base):
        base = re.sub(r"[^a-z0-9_-]", "-", str(base or "").strip().lower()).strip("-")[:64]
        if not base:
            base = "agent-" + secrets.token_hex(3)
        candidate = base
        n = 2
        with self._lock:
            taken = {str(a.get("entry", "")).lower() for a in self._agents.values()}
        while candidate in taken:
            candidate = "%s-%d" % (base, n)
            n += 1
        return candidate

    # ---- 代理实体 ----
    def create_agent(self, name, password, entry=None):
        name = str(name or "").strip()
        if not name:
            raise BrokerError(400, "agent name is required")
        generated = None
        if not password:
            password = secrets.token_urlsafe(9)
            generated = password
        entry_slug = entry if entry else name
        with self._lock:
            entry_slug = self._unique_entry(entry_slug)
            now = self.clock()
            agent_id = secrets.token_hex(12)
            agent = {
                "id": agent_id,
                "name": name[:80],
                "entry": entry_slug,
                "public_slug": None,
                "password_hash": _hash_password(password),
                "paused": False,
                "created_at": now,
                "updated_at": now,
            }
            self._agents[agent_id] = agent
            self._save_agents()
        view = self.agent_view(agent)
        if generated:
            view["password"] = generated
        return view

    def set_paused(self, agent_id, paused):
        with self._lock:
            agent = self._require_agent(agent_id)
            agent["paused"] = bool(paused)
            agent["updated_at"] = self.clock()
            self._save_agents()
            if paused:
                self._revoke_agent_sessions(agent_id)
            return self.agent_view(agent)

    def rotate_entry(self, agent_id, password=None):
        with self._lock:
            agent = self._require_agent(agent_id)
            agent["entry"] = self._unique_entry("agent-" + secrets.token_hex(3))
            generated = None
            if password:
                agent["password_hash"] = _hash_password(password)
            else:
                generated = secrets.token_urlsafe(9)
                agent["password_hash"] = _hash_password(generated)
            agent["updated_at"] = self.clock()
            self._save_agents()
            self._revoke_agent_sessions(agent_id)
            view = self.agent_view(agent)
        if generated:
            view["password"] = generated
        return view

    def set_public_slug(self, agent_id, slug):
        """把代理实体与其稳定公开登录页 slug 关联起来。

        slug 只是公开入口地址的一部分：轮换它不改变代理 id/name/entry，因此
        代理身份保持稳定，只有访客侧地址变化。
        """
        slug = str(slug or "").strip().lower()
        with self._lock:
            agent = self._require_agent(agent_id)
            agent["public_slug"] = slug or None
            agent["updated_at"] = self.clock()
            self._save_agents()
            return self.agent_view(agent)

    # ---- 代理会话 ----
    def _revoke_agent_sessions(self, agent_id):
        for token in [t for t, s in self._sessions.items() if s.get("agent_id") == agent_id]:
            self._sessions.pop(token, None)

    def _purge_expired(self, now=None):
        now = self.clock() if now is None else now
        for token in [t for t, s in self._sessions.items() if s.get("expires_at", 0) <= now]:
            self._sessions.pop(token, None)

    def login(self, entry, password):
        entry = str(entry or "").strip().lower()
        with self._lock:
            self._purge_expired()
            agent = next((a for a in self._agents.values()
                          if str(a.get("entry", "")).lower() == entry), None)
            if not agent or not _verify_password(agent.get("password_hash"), password):
                raise BrokerError(401, "invalid agent credentials")
            if agent.get("paused"):
                raise BrokerError(403, "agent entry is paused")
            now = self.clock()
            token = secrets.token_urlsafe(24)
            self._sessions[token] = {
                "agent_id": agent.get("id"),
                "created_at": now,
                "expires_at": now + AGENT_SESSION_TTL,
            }
            return token, now + AGENT_SESSION_TTL

    def session_agent_id(self, token):
        token = str(token or "").strip()
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session.get("expires_at", 0) <= self.clock():
                self._sessions.pop(token, None)
                return None
            return session.get("agent_id")

    def session_agent(self, token):
        """已登录且未暂停的代理记录；否则 None。"""
        agent_id = self.session_agent_id(token)
        if not agent_id:
            return None
        agent = self.get_agent(agent_id)
        if not agent or agent.get("paused"):
            return None
        return agent

    def logout(self, token):
        with self._lock:
            return bool(self._sessions.pop(str(token or "").strip(), None))

    # ---- 账号绑定 ----
    def _binding_for(self, uid):
        return self._bindings.get(str(uid))

    def bind(self, agent_id, uid):
        uid = str(uid or "").strip()
        if not uid:
            raise BrokerError(400, "account uid is required")
        with self._lock:
            agent = self._require_agent(agent_id)
            existing = self._binding_for(uid)
            if existing and existing.get("agent_id") != agent.get("id"):
                raise BrokerError(403, "account belongs to another agent")
            now = self.clock()
            if existing:
                return dict(existing), False
            binding = {"agent_id": agent.get("id"), "frozen": False,
                       "bound_at": now, "updated_at": now}
            self._bindings[uid] = binding
            self._save_bindings()
            return dict(binding), True

    def _require_binding(self, agent_id, uid):
        binding = self._binding_for(uid)
        if binding is None:
            raise BrokerError(404, "account is not bound")
        if binding.get("agent_id") != str(agent_id):
            raise BrokerError(403, "account belongs to another agent")
        return binding

    def unbind(self, agent_id, uid):
        uid = str(uid or "").strip()
        with self._lock:
            self._require_agent(agent_id)
            self._require_binding(agent_id, uid)
            self._bindings.pop(uid, None)
            self._save_bindings()
            return True

    def set_frozen(self, agent_id, uid, frozen):
        uid = str(uid or "").strip()
        with self._lock:
            self._require_agent(agent_id)
            binding = self._require_binding(agent_id, uid)
            binding["frozen"] = bool(frozen)
            binding["updated_at"] = self.clock()
            self._save_bindings()
            return dict(binding)

    def bound_uids(self, agent_id):
        agent_id = str(agent_id)
        with self._lock:
            return sorted(uid for uid, b in self._bindings.items()
                          if b.get("agent_id") == agent_id)

    def bound_bindings(self, agent_id):
        """uid -> 绑定副本，仅限该代理名下，供聚合统计使用。"""
        agent_id = str(agent_id)
        with self._lock:
            return {uid: dict(b) for uid, b in self._bindings.items()
                    if b.get("agent_id") == agent_id}

    def all_bindings(self):
        """uid -> 绑定副本的全量快照，供管理员账号归属映射使用。"""
        with self._lock:
            return {uid: dict(b) for uid, b in self._bindings.items()}

    def agent_for_uid(self, uid):
        """账号当前归属的代理记录（无绑定时 None）。"""
        with self._lock:
            binding = self._binding_for(uid)
            if not binding:
                return None
            return self._agents.get(str(binding.get("agent_id")))

    def is_frozen(self, agent_id, uid):
        with self._lock:
            binding = self._binding_for(uid)
            return bool(binding and binding.get("agent_id") == str(agent_id)
                        and binding.get("frozen"))

    # ---- 条数结算 ----
    def generate_settlement(self, period, items):
        """按周期生成结算单；同周期已存在时幂等返回既有单据。

        ``items`` 是 ``[{"agent_id", "agent_name", "effective_account_count"}]``，
        由调用方按实时账号池算好后传入，本模块只保存条数快照。
        """
        period = normalize_period(period)
        with self._lock:
            existing = next((s for s in self._settlements.values()
                             if s.get("period") == period), None)
            if existing:
                view = self._settlement_view(existing)
                view["duplicate"] = True
                return view
            now = self.clock()
            snapshot = []
            total = 0
            for item in items:
                count = int(item.get("effective_account_count") or 0)
                snapshot.append({
                    "agent_id": item.get("agent_id"),
                    "agent_name": item.get("agent_name"),
                    "effective_account_count": count,
                })
                total += count
            settlement = {
                "id": secrets.token_hex(12),
                "period": period,
                "status": SETTLEMENT_DRAFT,
                "created_at": now,
                "confirmed_at": None,
                "total_effective_accounts": total,
                "items": snapshot,
            }
            self._settlements[settlement["id"]] = settlement
            self._save_settlements()
            view = self._settlement_view(settlement)
            view["duplicate"] = False
            return view

    def _settlement_view(self, settlement):
        return {
            "id": settlement.get("id"),
            "period": settlement.get("period"),
            "status": settlement.get("status"),
            "created_at": settlement.get("created_at"),
            "confirmed_at": settlement.get("confirmed_at"),
            "total_effective_accounts": settlement.get("total_effective_accounts", 0),
            "items": [dict(item) for item in settlement.get("items", [])],
        }

    def list_settlements(self):
        with self._lock:
            settlements = [self._settlement_view(s) for s in self._settlements.values()]
        settlements.sort(key=lambda s: (s.get("period") or "", s.get("created_at") or 0))
        return settlements

    def confirm_settlement(self, settlement_id):
        with self._lock:
            settlement = self._settlements.get(str(settlement_id))
            if not settlement:
                raise BrokerError(404, "settlement not found")
            if settlement.get("status") == SETTLEMENT_CONFIRMED:
                raise BrokerError(409, "settlement already confirmed")
            settlement["status"] = SETTLEMENT_CONFIRMED
            settlement["confirmed_at"] = self.clock()
            self._save_settlements()
            return self._settlement_view(settlement)

    def settlements_for_agent(self, agent_id):
        agent_id = str(agent_id)
        out = []
        with self._lock:
            for settlement in self._settlements.values():
                summary = [dict(item) for item in settlement.get("items", [])
                           if str(item.get("agent_id")) == agent_id]
                if not summary:
                    continue
                out.append({
                    "id": settlement.get("id"),
                    "period": settlement.get("period"),
                    "status": settlement.get("status"),
                    "confirmed_at": settlement.get("confirmed_at"),
                    "items": summary,
                })
        out.sort(key=lambda s: s.get("period") or "")
        return out
