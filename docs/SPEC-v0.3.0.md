# workbuddy-fd v0.3.0 公开登录入口规格

## 1. 版本边界

v0.3.0 是公开登录入口版本，基于 v0.2.0 Docker 基线与 `/cli` 配置页继续演进。版本目标是让管理员可以创建可重复使用的登录页，访客通过短地址发起 WorkBuddy OAuth 登录，并把成功登录的账号提交到账号池的后续审核流程。

本版本保持以下兼容性：

- 现有管理员面板、账号池、签到/任务调度和 `/v1` 协议不变；
- `/cli` 页面及 Codex/OpenCode/DeepSeek 配置器不变；
- `accounts/`、`usage/` 持久化目录不变；
- OAuth 会话只在进程内保存短期状态，登录页注册表写入 `accounts/public_login_pages.json`。

非目标：代理后台、代理结算账本、金额字段、账号额度/用量展示、账号删除权限不在 v0.3.0 实现范围内，继续按 v0.1.0 领域约束保留。

## 2. 角色与权限边界

| 能力 | 管理员 | 访客 |
|---|---:|---:|
| 查看公开登录页列表 | 是（面板密码） | 否 |
| 创建公开登录页 | 是（面板密码） | 否 |
| 修改名称/区域 | 是（面板密码） | 否 |
| 暂停/恢复入口 | 是（面板密码） | 否 |
| 轮换入口短码 | 是（面板密码） | 否 |
| 访问 `/p/{slug}` | 否（无需面板会话） | 是 |
| 发起 OAuth 登录 | 否（无需面板会话） | 是 |
| 查看账号凭证、UID、额度、用量 | 否（公开入口不返回） | 否 |

管理员接口统一受现有面板密码会话保护。公开入口只返回访客完成登录所需的短期信息和账号昵称/区域摘要，不返回 access token、refresh token、内部 ID 或额度数据。

## 3. 公开登录页模型

页面注册表文件：`accounts/public_login_pages.json`。每个页面至少包含：

- `id`：内部随机标识，不出现在公开 HTML；
- `slug`：唯一短地址段，仅允许小写字母、数字、`_`、`-`；
- `name`：页面显示名称；
- `realm`：`intl` 或 `cn`；
- `platform`：默认 `CLI`；
- `paused`：暂停标志；
- `created_at`、`updated_at`：Unix 时间戳。

公开地址格式：`/p/{slug}`。登录页可重复使用，暂停只阻止新访问/新会话，不删除历史页面记录。

## 4. 管理员 API

### 4.1 列表

```http
GET /public-login/pages
```

响应：`{"pages": [{"id", "slug", "name", "realm", "platform", "paused", "created_at", "updated_at", "public_url"}]}`。

### 4.2 创建

```http
POST /public-login/pages
Content-Type: application/json

{"slug":"partner-a","name":"Partner A 登录","realm":"intl","platform":"CLI"}
```

成功返回 HTTP `201` 和页面脱敏视图。`slug` 重复返回 `409`。

### 4.3 更新与生命周期

```http
POST /public-login/pages/{id}/update
POST /public-login/pages/{id}/pause
POST /public-login/pages/{id}/resume
POST /public-login/pages/{id}/rotate
```

`update` 可修改 `name` 和 `realm`；`pause`/`resume` 切换入口状态；`rotate` 生成新的随机 slug。所有变更覆盖写入注册表并更新时间戳。

## 5. 访客 OAuth 流程

### 5.1 打开入口

```http
GET /p/{slug}
```

入口有效时返回无缓存 HTML 登录页；暂停或不存在时分别返回 HTTP `410` 或 `404`。

### 5.2 创建独立会话

```http
POST /p/{slug}/oauth/start
Content-Type: application/json

{}
```

成功响应包含：

```json
{
  "session": "SESSION",
  "state": "STATE",
  "authUrl": "https://TARGET/oauth/...",
  "expires_at": 0
}
```

每次请求生成独立 `session`、OAuth `state`、PKCE `code_verifier` 和过期时间；会话绑定页面 `id` 及上游登录状态。默认 TTL 为 15 分钟。多个访客或同一访客的并行会话互不覆盖。

### 5.3 轮询

```http
GET /p/{slug}/oauth/poll?session=SESSION
```

轮询只接受与页面绑定的会话。成功时返回：

```json
{
  "status": "ok",
  "review_status": "pending_review",
  "account": {"nickname": "DISPLAY_NAME", "realm": "intl"}
}
```

会话不存在返回 `404`，过期返回 `410`；上游错误/过期状态会清理短期会话。成功响应不包含任何凭证、UID、额度、用量或金额字段。

### 5.4 取消会话

```http
POST /p/{slug}/oauth/cancel
Content-Type: application/json

{"session":"SESSION"}
```

取消只释放当前页面绑定的目标会话并调用账号池取消操作，不会影响同一入口的其他访客；重复取消返回 `{"cancelled":false}`。

## 6. 账号接入状态

公开 OAuth 完成后，账号进入账号池的待审核接入流程，公开端只展示“已提交审核”和昵称摘要。管理员后续可以在现有账号面板中审核、启用、暂停或删除账号。代理端（后续版本）只能读取昵称、状态、登录时间的白名单投影，不能删除账号或 ID。

## 7. 持久化与并发约束

- 页面注册表使用临时文件 + `os.replace` 原子替换，避免进程中断产生半文件；
- OAuth 会话仅保存在内存，包含上游 state 的映射，进程重启后自然失效；
- 所有会话访问在进程锁内完成，页面之间、访客之间不得共享 session；
- 公开 HTML 与轮询响应设置 `Cache-Control: no-store`；
- 凭证和 API Key 只能保存在既有账号目录/运行时内存，禁止写入 Git、规格和日志。

## 8. Docker 运行约束

v0.3.0 使用独立 Compose 文件，沿用 8788 端口和两个持久化卷：

- Compose project：`workbuddy-fd-v0-3-0`；
- image：`workbuddy-fd:v0.3.0`；
- container：`workbuddy-fd-v0-3-0`；
- `accounts/` 挂载到 `/app/accounts`，`usage/` 挂载到 `/app/usage`。

## 9. 验收标准

### 文档/配置

- `docker compose -f docker-compose.v0.3.0.yml config` 成功；
- `VERSION` 内容为 `v0.3.0`；
- Compose project、image、container、环境变量和 label 均包含 v0.3.0；
- 端口 `8788:8788` 与 `accounts/`、`usage/` 卷保持兼容。

### 运行

```powershell
docker compose -f docker-compose.v0.3.0.yml up -d --build --force-recreate
Invoke-WebRequest http://127.0.0.1:8788/health
Invoke-WebRequest http://127.0.0.1:8788/
```

要求容器健康、`/health` 返回 HTTP 200、管理员面板仍可打开，已有 `/v1/models` 和 `/cli` 路由不回归。

### 公开登录入口合约

在管理员会话下验证：

1. `GET /public-login/pages` 返回列表；
2. `POST /public-login/pages` 创建唯一 slug，并返回 `201`；
3. 重复 slug 返回 `409`；
4. `pause` 后 `GET /p/{slug}` 返回 `410`，`resume` 后恢复 `200`；
5. `rotate` 后新 slug 可访问，旧 slug 不再指向页面；
6. 两次并行 `POST /p/{slug}/oauth/start` 返回不同 `session` 与 `state`；
7. 不同页面的 session 轮询不能互相读取；
8. 轮询响应不含 token、UID、额度、用量、金额字段；
9. 页面注册表在容器重建后仍可从 `accounts/` 恢复；
10. 工作区不产生凭证、Token、密码等跟踪文件。

## 10. 后续版本入口

- v0.4.0：代理/管理员登录页归属和账号脱敏 DTO；
- v0.5.0：按有效账号条数的结算周期、确认与审计；
- 后续：管理员 NewAPI Key 与代理专属 `/v1` 入口绑定。



