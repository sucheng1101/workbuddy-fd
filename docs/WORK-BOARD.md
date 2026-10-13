# workbuddy-fd 工作板

## 当前状态

- 状态：进行中（v0.3.0 公开登录入口实现与 Docker 验证）
- 项目：workbuddy-fd
- 分支：`codex/feature/public-login-v0.3.0`
- 基线提交：`b0ee219`（v0.2.0 CLI 页面与 DeepSeek/OpenCode 配置）
- 运行方式：Docker Compose；端口 `8788`
- 当前版本文件：`VERSION = v0.3.0`

## 已确认范围

- 核心基线：`ardeyouxipianyi/workbuddy2api-hub`
- 参考项目：`linguo2625469/workbuddy2api-panel`
- 项目仓库：`https://github.com/sucheng1101/workbuddy-fd`
- 部署方式：Docker / Docker Compose
- 角色：管理员、代理、客户/账号提供者
- 代理可见字段：账号昵称、状态、登录时间
- 代理隐藏字段：账号 ID、UID、凭证、额度、用量、金额
- 代理不可删除账号或 ID
- 结算口径：管理员与代理按有效账号条数结算，不展示金额

## 当前远程

- `origin`：项目所属 GitHub 仓库，可发布
- `upstream-hub`：Hub 上游，仅拉取
- `upstream-panel`：Panel 参考上游，仅拉取

## 已完成版本

### v0.1.0 基线

- 领域规格、权限矩阵、目标数据模型与版本化 Compose 已建立；
- Docker 构建、启动、健康检查和面板访问已验证；
- 相关基线/收尾提交：`b446c4f`、`82ff5b8`。

### v0.2.0 CLI 配置页

- 新增 `/cli` 页面和 `wb_agents.py` 配置器；
- 支持 Codex、OpenCode、DSH、Crush、ZCode 等客户端配置描述；
- OpenCode + DeepSeek 默认网关为 `http://127.0.0.1:8788/v1`，模型为 `deepseek-v4.1-flash`；
- v0.2.0 Compose 与 CLI 合约测试已完成。

## v0.3.0 本轮任务卡：公开登录入口

1. 管理员通过面板密码创建、查看、更新、暂停、恢复和轮换公开登录页；
2. 访客通过 `/p/{slug}` 打开可重复使用的登录页；
3. 每次 `POST /p/{slug}/oauth/start` 生成独立 session、state、PKCE verifier 和 15 分钟过期时间；
4. `GET /p/{slug}/oauth/poll?session=...` 只允许读取绑定页面的会话，成功结果进入 `pending_review` 摘要；
5. 页面注册表持久化到 `accounts/public_login_pages.json`，OAuth 短期状态仅驻留内存；
6. 公开响应不泄漏 token、UID、凭证、额度、用量或金额；
7. 不改变现有 `/v1` 协议、账号池任务调度和 `/cli` 配置行为。

### 预期接口

- `GET /public-login/pages`
- `POST /public-login/pages`
- `POST /public-login/pages/{id}/update`
- `POST /public-login/pages/{id}/pause`
- `POST /public-login/pages/{id}/resume`
- `POST /public-login/pages/{id}/rotate`
- `GET /p/{slug}`
- `POST /p/{slug}/oauth/start`
- `GET /p/{slug}/oauth/poll?session=SESSION`
- `POST /p/{slug}/oauth/cancel`

## v0.3.0 验收记录模板

### 配置与版本

- [ ] `docker compose -f docker-compose.v0.3.0.yml config`
- [ ] `VERSION` 为 `v0.3.0`
- [ ] Compose project/image/container/environment/label 均带 `v0.3.0`
- [ ] 端口 `8788` 与 `accounts/`、`usage/` 卷兼容

### 服务与回归

- [ ] `docker compose -f docker-compose.v0.3.0.yml up -d --build --force-recreate`
- [ ] `Invoke-WebRequest http://127.0.0.1:8788/health` 返回 200
- [ ] `Invoke-WebRequest http://127.0.0.1:8788/` 可打开管理员面板
- [ ] `/v1/models`、`/cli` 保持可用

### 公开入口合约

- [ ] 管理员列表/创建/更新/暂停/恢复/轮换接口鉴权正确
- [ ] 重复 slug 返回 409，不存在页面返回 404
- [ ] 暂停页面返回 410，恢复后返回 200
- [ ] 并行 OAuth session/state 不串号
- [ ] 页面绑定校验阻止跨入口轮询
- [ ] 取消会话只释放当前 session，重复取消幂等
- [ ] 登录成功只返回昵称/区域与 `pending_review`
- [ ] 容器重建后页面注册表仍存在
- [ ] Git 工作区无凭证、Token、密码落盘

## 运行与发布约束

- 不把任何 WorkBuddy 凭证、GitHub 密码或 API Key 写入仓库；
- 上游 remote 保持只读，不向上游推送；
- 每个独立功能完成并通过验证后创建独立 feature commit；
- 生产发布前核对目标服务、备份、健康检查和回滚路径。


