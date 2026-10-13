# workbuddy-fd 工作板

## 当前状态

- 状态：READY（基线已建立，等待功能实施确认）
- 项目：workbuddy-fd
- 主分支：main
- 基线提交：4c6fab5
- 本地运行时：docker（尚未启动）
- 当前任务：从 `workbuddy2api-hub` 建立项目 fork，保留 `workbuddy2api-panel` 只读参考

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

## 下一步

1. 建立项目技能与会话记录；
2. 设计权限矩阵、数据库模型和 OAuth 并发会话；
3. 实现公开登录页、代理后台、管理员后台；
4. Docker 本地验证；
5. 创建独立功能提交并推送项目仓库。

## 运行与发布约束

- 不把任何 WorkBuddy 凭证、GitHub 密码或 API Key 写入仓库。
- 上游 remote 保持只读，不向上游推送。
- 生产发布前需要核对目标服务、备份、健康检查和回滚路径。
