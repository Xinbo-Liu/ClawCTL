# `config/control_plane/` 目录说明

`config/control_plane/` 是 base control-plane 的基座真源。

## 内容范围

- `service.json`：base service 装配入口
- `profiles/*.service.json`：仓库内正式 profile
- `profile_registry.tsv`：control-plane profile id 到正式 service 配置路径的显式登记真源
- `extensions.d/*.json`：仓库内正式 extension manifest
- `schemas/`：base control-plane 与 extension 装配合同
- `object_families.json`：base 全局对象族与路径规则

## 边界

- 本目录定义 base service、显式登记 profile 与正式 extension 的装配事实。
- `agent/control_plane/` 定义共享 agent-plane 对象与运行资产事实。
- `python/openclaw/control_plane/` 负责读取与校验上述真源，不维护第二份对象快照。
- 业务扩展配置与演示配置只能通过显式登记 profile、有效目录发现 profile 或仓内合同 service 的显式 `--config-path` 接入，不在 base 目录内并列维护；仓外非标准 extension manifest 不属于正式入口。

## 默认入口

- base 入口：`config/control_plane/service.json`
- 正式默认运行 profile：`config/control_plane/profiles/agent_platform.service.json`
- 正式平台扩展：`config/control_plane/extensions.d/agent_platform.json`

`extensions.manifestsDirs` 是唯一正式 manifest 目录字段；单个 profile 可以跨多个仓内合同 manifest 目录启用多个 extension。任何可见且带 `id` 的 manifest 必须位于平台或 `agent/extensions/<extension-id>/` 的约定合同路径，并通过严格字段校验。

启用任何非平台 extension 的 profile 必须先启用 `agent_platform`。profile 继承链、base registry 路径、schema 路径与 manifest 目录都必须留在同一仓库内；业务 extension 暴露的 callable 入口必须来自自身扩展包 Python root。

base 全局对象族继续直接读取；extension owner 的对象族、诊断面、router / dispatch / recovery surface 在同名冲突时必须显式传 `--extension <id>`。
