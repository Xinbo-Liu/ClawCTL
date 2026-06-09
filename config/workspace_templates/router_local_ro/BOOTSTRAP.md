# BOOTSTRAP.md（router_local_ro）

> **局部文档声明**：该局部模板不是项目正式入口，只描述模板或工具职责；项目正式部署、正式运行、正式验收、安全边界与项目级治理统一回到项目文档导航 `docs/README.md` 及对应专题页。

## 启动读取顺序

- `IDENTITY.md`
- `AGENTS.md`
- `TOOLS.md`
- `USER.md`
- `HEARTBEAT.md`
- `MEMORY.md`

## 运行边界

- 默认只做初始路由与最小信息收集。
- 需要业务执行时，通过 `sessions_spawn` 转交到当前 profile 暴露的业务 agent。
- control-plane 的正式执行入口由项目控制面命令承担。
