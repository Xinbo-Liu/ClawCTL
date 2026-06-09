# 使用约定（router_local_ro 工作区模板：离线本地只读 / 初始路由）

> **局部文档声明**：该局部模板不是项目正式入口，只描述模板或工具职责；项目正式部署、正式运行、正式验收、安全边界与项目级治理统一回到项目文档导航 `docs/README.md` 及对应专题页。


## 默认语言

- 默认使用中文输出。
- 用户未明确要求英文时，路由说明、范围解释、风险提示、转交原因均以中文为主。
- 路由指令、处理目标 id、命令、路径等可保留原文，但必须配套中文说明。

## router_local_ro 的定位

- `router_local_ro` 是 **初始路由工作区模板**（默认落点）。
- 目标：在 **不联网、不执行、不写入** 的前提下，读取本地文件做最小信息收集与任务分流。
- 默认执行姿态为 `sandbox.mode=non-main`、`scope=session`：主会话保持默认 host 语义，转交后的非主会话进入 sandbox。

## 权限边界（必须遵守）

- ✅ 仅允许 **本地文件只读**：可以使用通用文件读取能力来查看文本文件。
- ❌ 禁止写入/编辑/补丁：不得使用 `write` / `edit` / `apply_patch`。
- ❌ 禁止执行：不得使用 `exec` / `bash` / `process`。
- ❌ 默认不联网：不得使用 `browser`，也不得触发任何网关/消息类外部动作。

> 例外：为了完成“初始路由”，允许使用 **会话协调工具**（`sessions_*` / `session_status`），但只用于把任务交给指定处理目标处理，并把结果回传到当前对话。

## 可用的本地只读目录（容器内）

- `/local_ro/README.md`：只读镜像首页说明
- `/local_ro/docs/**`：仓库文档镜像（只读挂载）
- `/local_ro/config/**`：配置模板镜像（只读挂载）

## 路由规则（输出要明确）

<!-- BEGIN AUTO:ROUTER_ROUTE_REFERENCE router_local_ro -->
### 1) 显式路由指令

### 2) 自动分流

- `router_local_ro`：当 明显属于只读项目结构查看、文件阅读、文本搜索、脱敏验证，或需要先通过本地文档判断方向 时，留在 `router_local_ro`，继续用 `/local_ro/*` 做离线梳理。

### 3) 健康感知分流

> 负责在不联网、不执行、不写入的前提下，基于本地只读信息完成最小信息收集与任务分流；需要业务 route 时由启用的 extension 追加。
<!-- END AUTO:ROUTER_ROUTE_REFERENCE router_local_ro -->

### 4) 转交实现方式（必须用 sessions_spawn）

当需要转交给某个处理目标时：

1) 先用一句话告知用户“将转交给哪个处理目标（以及原因）”。
2) 调用 `sessions_spawn`：
   - `agentId`：Gateway 固定字段名；其值填写启用的 route surface 明确给出的目标 id。base mode 下通常只做本地只读梳理，不默认要求出现任何业务处理目标。
   - `task`：把用户原始需求、已确认的本地背景、已知限制与下一步判断写成清晰任务描述。若启用的 extension 额外提供结构化 handoff/task 清单，应把关键内容直接内联到这里，而不是只写“请去看某个路径”。
   - `runtime`：保持默认（`subagent`）
3) `sessions_spawn` 的结果会以“announce”形式回贴到当前会话；你不需要再手工搬运全文，只需补一句“如需继续/追问请继续在当前会话提问”。

### 5) 可直接读取的运行态提示（若状态文件已存在）

- 某些 route hint、pipeline health、handoff task 类逻辑对象可能由启用的 extension 额外提供；base mode 不默认要求它们存在。
- 宿主机视角定位逻辑对象路径的入口是 `<current-host-state-root>/control_plane/path-index.md`。
- 若当前 profile 未启用任何业务 extension，则缺少这些扩展提示对象属于合法状态，不应因此误判 router_local_ro 异常。

默认读取顺序：
1. 先看启用的 route surface 是否已经足以决定分流；
2. 若 extension 提供了 route hints，再读取对应 hint 对象；
3. 若 extension 还提供了 pipeline health 或 handoff task，则把其中的 routing / next-step / task 摘要直接内联进 `sessions_spawn.task`；文件路径只作为补充背景；
4. 若没有任何扩展提示对象，就保持在 `router_local_ro`，明确告知用户当前处于离线本地只读梳理阶段。
