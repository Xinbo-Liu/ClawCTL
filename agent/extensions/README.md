# 显式扩展包目录

`agent/extensions/` 是主仓库内唯一允许托管受管业务扩展包的目录。

固定规则如下：

- 主仓库默认运行面固定为 `base` 与 `agent_platform`。
- 放在 `agent/extensions/<extension-id>/` 下的扩展包必须保持自包含边界，不得拆回主仓库正式面。
- [index.json](index.json) 和 `config/control_plane/profile_registry.tsv` 是显式登记真源；未登记目录可以通过严格自动发现进入可选 profile，显式登记优先于发现冲突，不要求所有接入项目同时登记。
- 受控组合 profile 还必须登记在 `config/control_plane/repo_combination_profiles.json`，由该文件声明组合 service 路径、精确启用的 extension 集合、manifest 目录和允许共享的 deploy env 字段。
- `profile_registry.tsv` 只能使用合同 profile 路径：`base`、`agent_platform`、受控组合 profile 与 `<extension-id>` 不能互相别名到其他 service。
- 未登记目录只有在自带 `<extension-id>.service.json`、`<extension-id>.json`、扩展内 Python root，并通过启用集合、manifest 目录与路径边界校验后，才补充为可选 `--control-plane-profile <extension-id>`。
- 显式登记的默认 service 与自动发现 service 只能启用 `agent_platform` 与自身 `<extension-id>`，只能加载平台 manifest 目录与自身 manifest 目录；扩展贡献的 registry 文件、surfaceFragments 与 governanceSurfaces 必须留在扩展根目录内。
- 任意启用非平台 extension 的 service 都必须先启用 `agent_platform`；多扩展组合 profile 只接受仓内合同 manifest 目录。当前基座发布面不登记业务组合 profile。
- extension manifest 必须位于 `agent/extensions/<extension-id>/config/control_plane/extensions.d/<extension-id>.json`；任何可见且带 `id` 的 manifest、`index.json` 与布尔字段都采用严格字段合同，未知字段、非合同别名或字符串布尔值都会使接入无效。
- 非平台 extension 在 manifest 中暴露的 `jobRunners`、`cliCommands`、`internalApiRoutes`、`readyChecks` 与声明回调必须来自自身 `python/<package>`，不能借用平台或其他扩展包的 Python 模块。
- 无效目录和显式登记冲突只进入 `control-plane config profiles --format json` 诊断输出，不进入可选 profile 列表。
- 受管扩展可以通过 `--control-plane-profile` 接入，也可以显式传入仓内合同 service 的 `--config-path`；目录存在本身不等于进入默认运行面。
- 扩展包共享对象的 `activation.enabledExtensionIds` 必须且只能等于自身 `<extension-id>`，不能把其他扩展 ID 混入自身 owner 对象。
- 扩展有外部 Python runtime 依赖时，`requirements.lock` 与 `offline_wheelhouse/` 共同作为仓内离线依赖真源；部署时先同步 runtime wheelhouse，再生成扩展 venv。

仓内显式扩展包统一复用：

- `agent/extensions/<extension-id>/`
- `agent/extensions/index.json`

不要在 `agent/modules/`、`config/control_plane/profiles/` 或 `python/openclaw/extensions/` 下重新引入业务扩展真源。
