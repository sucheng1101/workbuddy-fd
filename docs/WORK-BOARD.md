# workbuddy-fd 工作板

## 当前状态

- 状态：RELEASE_READY（v0.1.0 设计已落地，源码镜像构建与 Docker 验证通过）
- 项目：workbuddy-fd
- 主分支：main
- 基线提交：da737b1
- 本地运行时：docker（workbuddy-fd-v0-1-0，8788，healthy；镜像 digest `sha256:7b74fb18607621be276037247db03f19ec25b3bd2155547a391e83896ae31f6b`）
- 当前任务：v0.1.0 规格落地、版本化 Compose、本地 Docker 启动验证

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

## v0.1.0 本轮产物

- VERSION：v0.1.0
- docs/SPEC-v0.1.0.md：权限、登录页、账号状态、结算、NewAPI 边界
- docker-compose.v0.1.0.yml：本地构建与版本化容器

## 下一步

1. 完成项目技能与会话记录；
2. 已完成 v0.1.0 权限矩阵、目标数据模型和 OAuth 并发会话规格；
3. 实现公开登录页、代理后台、管理员后台；
4. Docker 本地验证已通过：`docker compose -f docker-compose.v0.1.0.yml config`、源码 `up -d --build --force-recreate`、`/health`、`/`；
5. 创建独立功能提交并推送项目仓库。

## 运行与发布约束

- 不把任何 WorkBuddy 凭证、GitHub 密码或 API Key 写入仓库。
- 上游 remote 保持只读，不向上游推送。
- 生产发布前需要核对目标服务、备份、健康检查和回滚路径。
