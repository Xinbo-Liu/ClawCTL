# Agent 治理基线

## 固定结论

1. `config/control_plane/` 是 base control-plane 的基座真源，负责 service / profile / extension 装配入口与 base schema。
2. `config/control_plane/profiles/agent_platform.service.json` 是主仓库内唯一正式默认运行 profile。
3. 仓内 extension 通过正式 profile、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 进入运行面。
4. formal module 只能落在 `agent/extensions/<extension-id>/agent/modules/<module_ref>/`，对应 Python 真源只能落在 `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/`。
5. 共享 group、job、model、target 对象由扩展包内 `agent/control_plane/` 提供，并通过 `activation.enabledExtensionIds` 声明归属。
6. 主仓库默认运行面只有 `base + agent_platform`；仓内托管的 managed explicit extension 可以常驻，但不自动进入默认运行面。

## 正式结构

- managed extension 索引：`agent/extensions/index.json`
- 扩展包根：`agent/extensions/<extension-id>/`
- 模块目录：`agent/extensions/<extension-id>/agent/modules/<module_ref>/`
- 模块 Python 真源：`agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/`
- 共享领域 Python：`agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/`
- 共享 control-plane 对象：`agent/extensions/<extension-id>/agent/control_plane/`

## 禁止项

- formal module authoring 面固定为扩展包内真源；根级 `agent/modules/`、`agent/domains/`、`python/openclaw/modules/`、`python/openclaw/domains/` 不属于 formal authoring 面。
- 扩展包共享领域固定只在 `python/<python-package>/domains/` authoring；`agent/extensions/<extension-id>/agent/domains/` 不属于正式作者模型。
- 扩展包说明固定按 managed explicit extension 装配口径编写；仓内允许托管 managed explicit extension，但必须显式装配。
