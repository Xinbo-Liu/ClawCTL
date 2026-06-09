# 工具与权限模型（router_local_ro 工作区模板）

> **局部文档声明**：该局部模板不是项目正式入口，只描述模板或工具职责；项目正式部署、正式运行、正式验收、安全边界与项目级治理统一回到项目文档导航 `docs/README.md` 及对应专题页。


`router_local_ro` 的设计目标是：**只用离线本地只读能力完成初始路由**。

在 Gateway 活动配置文件 `openclaw.json` 中对该工作区模板的策略为：

- tools.profile：`full`（或不设置）
- tools.allow：`read`、`sessions_spawn`、`subagents`、`session_status`
- tools.deny：`write`、`edit`、`apply_patch`、`exec`、`bash`、`process`

同时该工作区模板允许把任务转交给**启用的 route surface 明确声明的处理目标**（用于初始路由）：

- `subagents.allowAgents`：由启用的 profile / extension 工作区配置决定；base mode 不默认要求任何业务处理目标。

> 说明：`sessions_spawn` 会以“announce”把子目标的结果回贴到当前会话，因此你可以在不切换 UI 的情况下完成“路由 → 交接 → 回传”。若当前没有启用任何附加处理目标，就继续停留在本地只读梳理面，不要伪造业务转交。

因此它只保留“通用文件只读”能力，用于：

- 从 `/local_ro/README.md` 与 `/local_ro/docs` 阅读项目文档
- 从 `/local_ro/config` 查看配置模板

以及“会话协调能力”，用于：

- `subagents` / `sessions_spawn`：把请求转交给当前已启用的处理目标
- `session_status`：自检当前会话/子会话状态
