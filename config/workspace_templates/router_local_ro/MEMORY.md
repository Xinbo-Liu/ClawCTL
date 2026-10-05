# MEMORY.md（router_local_ro）

> **局部文档声明**：该局部模板不是项目正式入口，只描述模板或工具职责；项目部署、运行、验收、安全与治理入口见项目文档导航 `docs/README.md` 及对应专题页。

## 当前记忆

- `router_local_ro` 是默认初始路由工作区。
- 长期业务状态与运行历史不保存在本模板中，应读取 control-plane 与 scheduler 的正式产物。
- 若当前 profile 没有暴露业务 agent，则保持本地只读梳理并向用户说明限制。
