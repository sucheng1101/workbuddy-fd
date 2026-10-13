# workbuddy-fd v0.1.0 业务规格

## 1. 版本边界

v0.1.0 是本地设计与可运行基线版本：

- 继承 `workbuddy2api-hub` 的 OAuth、账号池、协议网关、签到/任务调度能力；
- 建立管理员、代理、客户/账号提供者三类角色的领域模型；
- 明确公开登录页、并发 OAuth 会话、账号脱敏、按条数结算与管理员 NewAPI 接入的接口边界；
- Docker 可构建、可启动、可探活；
- 本版本不宣称多租户业务功能已经完成，后续按独立功能提交实现。

## 2. 角色与权限

| 能力 | 管理员 | 代理 | 客户/账号提供者 |
|---|---:|---:|---:|
| 查看全部账号 | 是 | 否 | 否 |
| 查看账号昵称 | 是 | 仅自己的入口 | 否 |
| 查看账号状态 | 是 | 仅自己的入口 | 仅提交结果 |
| 查看登录时间 | 是 | 仅自己的入口 | 否 |
| 查看账号 ID/UID | 是 | 否 | 否 |
| 查看凭证/Token | 是（内部） | 否 | 否 |
| 查看额度/用量 | 是（内部运维） | 否 | 否 |
| 查看金额 | 不作为产品字段 | 不作为产品字段 | 不作为产品字段 |
| 删除账号 | 是 | 否 | 否 |
| 删除账号 ID/绑定 | 是 | 否 | 否 |
| 创建公开登录页 | 是 | 是 | 否 |
| 管理代理 | 是 | 否 | 否 |
| 管理任务/签到 | 是 | 否 | 否 |
| 生成管理员 NewAPI Key | 是 | 否 | 否 |

代理接口采用后端白名单 DTO，只返回 `nickname/status/login_at`，不能依赖前端隐藏字段实现隔离。

## 3. 公开登录页

- 地址：`/p/{public_slug}`；管理员和代理均可创建。
- 登录页可重复使用；每次访问创建独立 `oauth_session`。
- 会话至少包含：`session_id`、`state`、`code_verifier`、`expires_at`、`page_id`。
- 使用 OAuth `state` + PKCE；回调必须绑定原始会话和入口，不接受跨入口回调。
- 同一入口允许多个并行会话；会话成功后产生一个待审核 `account_binding`。
- 登录页可暂停、过期、轮换短码；暂停不删除历史账号记录。

## 4. 账号状态机

```text
pending_review -> active -> paused -> active
pending_review -> rejected
active -> expired / error
```

- 只有管理员可以审核、启用、暂停、删除账号。
- 代理只能读取自己的入口关联账号的脱敏投影。
- 删除是管理员域操作；代理路由不注册删除、ID 修改和凭证导出接口。

## 5. 结算口径

第一版只保存条数，不设计金额字段：

- `effective_account_count = approved AND enabled AND not expired`；
- 默认按自然月统计，可在管理员设置中扩展周期；
- 管理员与代理按有效账号条数确认；
- 代理与客户之间的客户结算由代理线下或代理自己的业务流程处理；
- 页面显示条数、周期、确认状态，不显示金额。

## 6. 领域表（目标）

```text
admins
agents
public_login_pages
oauth_sessions
accounts
account_bindings
account_status_events
settlement_periods
settlement_items
api_keys
audit_logs
```

关键约束：

- `public_login_pages.slug` 唯一；
- `oauth_sessions.state` 唯一且短时有效；
- `accounts.internal_id/uid` 仅管理员域可读；
- `account_bindings` 记录入口归属，不复制凭证；
- `settlement_items` 只记录条数快照和来源，不记录金额；
- 所有权限拒绝和状态变化写入 `audit_logs`。

## 7. 管理员 NewAPI 接入

管理员后台生成独立 API Key，默认绑定管理员账号池：

```text
Base URL: https://DOMAIN/v1
Key: sk-fd-<random>
```

后续可扩展区域、模型和入口绑定；v0.1.0 只要求管理员专用 Key 的模型网关接入边界清晰。

## 8. 验收标准

### 本版本

- `docker compose -f docker-compose.v0.1.0.yml config` 成功；
- 项目镜像以 `workbuddy-fd:v0.1.0` 构建；
- 容器名为 `workbuddy-fd-v0.1.0`；
- `/health` 返回 HTTP 200；
- `/` 可打开面板；
- 账号和用量目录持久化；
- 工作区无凭证、Token、密码落盘。

### 后续功能版本

- 代理 API 实际只返回三字段；
- 代理删除账号/ID 路由不存在或统一拒绝；
- 并发 OAuth 会话互不串号；
- 管理员可生成自己的 NewAPI Key；
- 条数统计可重算并有审计记录。
