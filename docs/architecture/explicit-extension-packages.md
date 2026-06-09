# 显式扩展包挂载与编排指南

本文档定义主仓库正式支持的显式扩展包挂载方式。新增 agent、agent group 与业务链路时，统一按本页执行。

## 本页解决什么问题

- 仓内显式扩展包的最小目录结构应该是什么。
- 如何组合 `base + agent_platform + explicit extension`。
- 共享对象如何声明 `activation.enabledExtensionIds`。
- 模块、组、作业、模型和 target 在扩展包内如何落位。
- 如何给扩展包提供默认模型与默认分发渠道，并在部署时覆盖。
- 如何通过显式登记或有效自动发现的 `--control-plane-profile`，以及仓内合同路径下的显式 `--config-path` 挂载、验证和运行扩展包。

## 适用范围

- 主仓库默认运行面只有 `base` 与 `agent_platform`。
- 业务链路与业务对象允许仓内托管，但只能作为显式扩展包存在。
- 显式扩展包必须位于 `agent/extensions/<extension-id>/`；`extension-id` 使用目录名，并遵循小写下划线命名。
- `agent/extensions/index.json` 与 `config/control_plane/profile_registry.tsv` 是显式登记真源；未登记目录可以通过严格自动发现进入可选 profile，显式登记与发现冲突时显式登记优先。
- 未登记扩展包只有在约定 service、manifest、Python root、启用集合与路径边界全部通过校验后，才补充为可选 `--control-plane-profile <extension-id>`。
- 受管显式扩展包可以通过显式登记或有效自动发现的 `--control-plane-profile` 挂载，也可以显式传入 `--config-path`；目录存在本身不等于进入默认运行面。
- 平台合同回归使用合成受管扩展 fixture 或动态读取受管扩展索引；具体业务扩展 ID、业务 group、业务 job、业务 check 名称不得作为平台合同。
- 测试只需要一个扩展样本时使用确定性的代表扩展 helper；验证仓库真实状态时遍历受管扩展索引，不假设仓库长期只有一个受管扩展。

## 目录结构

受管显式扩展包至少包含下面这些目录和文件：

```text
agent/extensions/<extension-id>/
  agent/
    modules/
    control_plane/
      groups/
      jobs/
      models/
      registries/
      targets/
  config/
    control_plane/
      profiles/
        <extension-id>.service.json
      extensions.d/
        <extension-id>.json
        <extension-id>.runtime_paths.json
        <extension-id>.object_families.json
        <extension-id>.testing_manifest.json
        <extension-id>.diagnostic_surface.json
  docs/
  python/
    <python-package>/
      domains/
      modules/
  tests/
    modules/
    regression/
    support/
    unit/
  offline_wheelhouse/
    manifest.json
    *.whl
  pyproject.toml
  requirements.lock
  README.md
```

显式受管扩展索引真源固定为 `agent/extensions/index.json`。索引根只允许 `extensions` 字段，索引中的每个条目只能声明：

- `id`
- `title`
- `rootDir`
- `defaultServiceConfigPath`
- `manifestDir`
- `pythonRoots`
- `status`

活跃条目的 `status` 固定使用 `managed_explicit_extension`；`retired` 只用于保留 ID 并阻断同名自动发现进入受管视图。`rootDir` 必须是 `agent/extensions/<extension-id>`，`defaultServiceConfigPath`、`manifestDir` 与 `pythonRoots` 必须分别指向该扩展包内的约定 service、manifest 目录与 `python/` 根；这些路径漂移属于索引读取错误。

显式受管扩展 profile 映射真源固定为 `config/control_plane/profile_registry.tsv`。registry 每行维护 `<profile-id>\t<config_path>`，并指向正式 service 配置路径；profile id 必须遵循小写下划线命名。`base` 只能指向 `config/control_plane/service.json`，`agent_platform` 只能指向 `config/control_plane/profiles/agent_platform.service.json`，扩展 profile 只能指向 `agent/extensions/<extension-id>/config/control_plane/profiles/<extension-id>.service.json`。仓内受控组合 profile 还必须登记在 `config/control_plane/repo_combination_profiles.json`，由该文件声明允许的组合 profile id、service 路径、enabled extension 集合、manifest 目录和共享 deploy env 字段；组合 profile service 固定使用 `config/control_plane/profiles/<combination-profile-id>.service.json`。不得用 registry 行为同一个 service 增加别名。

自动发现只扫描 `agent/extensions/<extension-id>/` 的直接子目录。有效自动发现候选必须自带：

- `config/control_plane/profiles/<extension-id>.service.json`
- `config/control_plane/extensions.d/<extension-id>.json`
- `python/<python-package>/__init__.py`

显式登记的默认 service 与自动发现 service 使用同一默认 profile 准入规则：service 必须通过 control-plane service schema 校验，且 `enabledExtensionIds` 只能包含 `agent_platform` 与自身 `<extension-id>`。service 的 `manifestsDirs` 只能加载主仓库 `config/control_plane/extensions.d` 与扩展包自己的 `config/control_plane/extensions.d`。受控组合 profile 使用 `repo_combination_profiles.json` 中的单独白名单规则；组合 profile 必须精确启用 `agent_platform` 与白名单声明的受管扩展，并只加载这些仓内合同 manifest 目录。manifest 必须位于 `config/control_plane/extensions.d/agent_platform.json` 或 `agent/extensions/<extension-id>/config/control_plane/extensions.d/<extension-id>.json` 的仓内合同路径。任何被 service 配置为可见且带 `id` 的 manifest，即使未启用，也必须通过同一严格合同校验。发现 manifest 的 `id` 必须等于目录名，manifest 声明的 schema 路径必须留在仓库内；jobs、models、targets、agentGroups、agentModules 目录必须解析在该扩展根目录内，runtime / dispatch registry 文件、surfaceFragments 与 governanceSurfaces 也必须解析在该扩展根目录内。发现候选与显式登记冲突时，显式登记生效，发现候选作为治理问题报告。

manifest 采用严格字段合同：顶层只允许 `id`、`title`、`version`、`compat`、`dependencies`、`migrations`、`registry`、`schemas`、`surfaceFragments`、`governanceSurfaces`、`jobRunners`、`cliCommands`、`internalApiRoutes`、`readyChecks`。`compat` 只接受 `controlPlane`，dependency 只接受对象形式的 `id`、`version`、`optional`，布尔字段必须使用 JSON boolean；未知字段、非标准别名 `control_plane`、`versionRange`、字符串布尔值都会使扩展无效。

扩展包 Python 运行包与测试目录都属于受管源码真源。`python/<python-package>/__init__.py` 必须提供字节码守卫，禁写 bytecode，并清理运行包下的 `__pycache__`；`tests/` 根目录和包含 Python 测试或辅助源码的直接子目录也必须提供 `__init__.py` 字节码守卫，禁写 bytecode 并清理本目录 `__pycache__`。直接模块运行、直接 unittest 选择器与仓库统一测试入口都不得在工作区留下 `__pycache__` 或 `.pyc` 残留。

## 扩展依赖与运行 venv

每个 managed explicit extension 的 Python 依赖真源分两层：

- `pyproject.toml` 声明扩展包元数据与直接依赖。
- `requirements.lock` 固定扩展 runtime venv 的安装集合；有外部依赖时必须使用精确版本与 `--hash=sha256:...`，无外部依赖时保留空锁文件或只有注释。
- `offline_wheelhouse/` 保存与 `requirements.lock` 完全一致的仓内离线 wheel 真源；有外部依赖时必须包含 `manifest.json`，且每个锁定包版本只能有一个 wheel，wheel 的 sha256 必须命中 lock 文件。

扩展 venv 是 runtime state 派生产物，不提交仓库，也不进入 release bundle。仓内 `offline_wheelhouse/` 是离线依赖真源，runtime paths 的 `extension_wheelhouse_dir` 是部署机上的同步目标缓存。`extension_envs_dir` 与 `extension_wheelhouse_dir` 在 host 视角默认落在 `<current-host-state-root>/control_plane/extension_envs` 与 `<current-host-state-root>/control_plane/wheelhouse/extensions`，scheduler 视角默认落在 `/home/openclaw/.openclaw/extension_envs` 与 `/home/openclaw/.openclaw/wheelhouse/extensions`。venv 目录按 extension id、Python tag、platform tag 与依赖 hash 分层，active env 通过 JSON manifest 指向，不依赖 symlink。active manifest 使用 `schemaVersion=2`，必须同时写入 `runtimePathViews.host` 与 `runtimePathViews.scheduler`，宿主机 CLI 与 scheduler 容器只读取各自视角的 `envPath`、`pythonExecutable` 与 `wheelhouseDir`，避免升级后单一绝对路径在另一视角失效。

部署、升级与 scheduler agent 运行前统一通过 `extension-env ensure` 对齐 active profile 的扩展离线依赖和 venv；部署/升级脚本必须经 `scripts/setup/lib/extension_env_gate.sh` 进入 scheduler 视角，并把 ensure JSON 写入部署或升级报告：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime --config-path <service-config> extension-env ensure --enabled --offline --json
```

`extension-env ensure` 先执行 managed extension lifecycle lock 校验，再校验 `requirements.lock`、`offline_wheelhouse/manifest.json` 与 wheel hash，然后同步 runtime wheelhouse、离线创建或复用 venv，最后执行 verify。lock 与源码不一致时，先执行 `bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane extensions lock` 并复核变更，再重新执行 ensure。默认安装策略是离线模式；`--allow-online` 只允许用于受控联网验证，不得进入默认部署或升级脚本。ensure 失败时保留当前可用 active manifest 和 venv。

运行时规则如下：

- managed extension agent 运行前必须命中有效 active manifest；manifest 缺失、损坏、依赖 hash 不匹配、Python 版本或平台不匹配、venv Python 不可执行都会直接失败，并提示 `extension-env ensure` 命令。
- managed extension agent 使用该扩展 venv Python 启动模块入口子进程；子进程只注入扩展 `python/`、仓库 `python/` 和 allowlist 环境变量。allowlist 由基础 OS/proxy env、注册表中的 `*Env` / `env:<NAME>` 显式声明、active runtime paths 的 `env_names` 与固定最小控制键组成，未声明的 `OPENCLAW_*` 或 `HOST_*` 不会按前缀整体透传。
- `base` 与 `agent_platform` 使用共享控制面运行方式，不切换到扩展 venv。

## 组合 Service

显式扩展包自己的 service 必须显式组合主仓库 `base` service、主仓库 `agent_platform` extension，以及扩展包自己的 manifest 目录。

```json
{
  "extends": "@repo/config/control_plane/service.json",
  "extensions": {
    "manifestsDirs": [
      "@repo/config/control_plane/extensions.d",
      "@extension/config/control_plane/extensions.d"
    ],
    "enabledExtensionIds": [
      "agent_platform",
      "<extension-id>"
    ]
  }
}
```

其中 `@repo/` 是主仓库固定路径合同，表示从仓库根目录解析；`@extension/` 是当前扩展包固定路径合同，表示从 `agent/extensions/<extension-id>/` 解析。显式扩展包回指主仓库 base service、schema 与正式 manifest 目录时使用 `@repo/`，引用扩展包内 manifest、registry、module source 与 surface fragment 时使用 `@extension/`。

固定约束如下：

- 默认运行面只有 `base + agent_platform`。
- 受管扩展 profile id 来自显式 registry 与有效自动发现结果，运行时可以通过 `--control-plane-profile <profile-id>` 选择正式 service。
- 受控组合 profile 由主仓库 registry 与 `config/control_plane/repo_combination_profiles.json` 共同登记；组合 profile 精确组合平台 manifest 与白名单声明的受管扩展 manifest 目录。组合 profile 不改变各扩展对象的自身 owner 归属。
- 显式 `--config-path` 是仓内合同 service 的正式挂载方式，用于未形成可用 profile id 的配置文件或显式验证路径；仓外非标准 extension manifest 不属于正式入口。

## 共享对象归属

显式扩展包内的共享对象都必须显式声明归属：

```json
{
  "id": "my_group",
  "activation": {
    "enabledExtensionIds": [
      "<extension-id>"
    ]
  }
}
```

平台规则如下：

- 缺少 `activation.enabledExtensionIds` 视为配置错误。
- 扩展包目录中的共享对象必须且只能声明 `activation.enabledExtensionIds: ["<extension-id>"]`，不得把其他扩展 ID 混入自身 owner 对象。
- 共享对象只在 profile 命中自身 extension id 时可见。
- owner-aware surface 冲突时，读取端必须显式传 `--extension <id>`。

## 推荐放置方式

- `agent/extensions/<extension-id>/agent/modules/`：模块目录、`module.json`、README、skills、permissions、tools 与实现入口说明。
- `agent/extensions/<extension-id>/python/<python-package>/domains/`：扩展包共享领域代码、共享验证与共享编排支撑。
- `agent/extensions/<extension-id>/agent/control_plane/groups/`：group 合同、成员顺序、release/recovery/acceptance 绑定。
- `agent/extensions/<extension-id>/agent/control_plane/jobs/`：调度条目、operationRef 与 job order。
- `agent/extensions/<extension-id>/agent/control_plane/models/`：默认 model profile。
- `agent/extensions/<extension-id>/agent/control_plane/registries/`：扩展自有 dispatch target registry，由扩展 manifest 的 `dispatchTargetRegistryPaths` 启用。
- `agent/extensions/<extension-id>/agent/control_plane/targets/`：默认 dispatch target binding，只绑定 operation 与 agent；实际发送 endpoint 由扩展自有 dispatch registry 与部署 env 输入解析。
- `config/control_plane/extensions.d/*.runtime_paths.json`：扩展包私有 runtime paths。
- `config/control_plane/extensions.d/*.object_families.json`：扩展包私有 object families。
- `config/control_plane/extensions.d/*.diagnostic_surface.json`：`control-plane diagnostics --extension <id>` 需要的动作字典与阻断码说明。

## 默认模型与默认分发渠道

默认绑定固定为两层，不新增逐模块或逐作业私有接口。

模型默认值：

```json
{
  "controlPlane": {
    "agent": {
      "defaultModelProfileRef": "<extension-default-model-profile>"
    }
  }
}
```

分发渠道默认值：

```json
{
  "operations": {
    "send_default": {
      "jobBindings": {
        "my_dispatch_job": {
          "targetBindingRef": "dispatch_target_default"
        }
      }
    }
  }
}
```

部署覆盖方式：

- 模型覆盖：修改扩展包自己的 `agent/control_plane/models/*.json`，或通过对应 deploy env 输入覆盖 provider/modelRef。
- 渠道覆盖：扩展包自己的 `agent/control_plane/targets/*.json` 仅维护 target binding；实际 endpoint、token 与路由开关按扩展自有 dispatch registry 及 `deploy/targets.d/<target_id>.env` 契约覆盖。

模型执行通道以 model profile 的 `channel` 为真源：

- `channel.kind=http` 支持 `openai-chat-completions`、`anthropic-messages` 与 `ollama-chat`。
- `channel.kind=local_process` 支持通过 `localProcess.command` 或 `localProcess.commandEnv` 调用本地推理进程。
- agent 实现必须通过 `openclaw.lib.models.generate_text` 调用模型；该入口统一处理 env 解析、认证必填、成本策略校验、预算闸门、并发/RPM 闸门、脱敏审计与协议分发。
- model profile 必须声明分层 `costPolicy`，计量计费模型必须维护真实输入/输出费率、价格来源、核验日期、字符/token 估算和预算硬限；0 费率只允许用于明确声明为 `self_hosted` 或 `not_applicable` 的 profile。
- extension 若新增模型 env，应通过 `surfaceFragments.deployEnvSchemaPath` 或 model profile 派生 env 需求进入部署校验链路。

## 挂载、验证与运行

基座与扩展同步升级时，正式部署以 stack release 为准。组合层必须按完整 composition 集合把指定版本扩展物化到 `agent/extensions/<extension-id>/`，并按 `config/control_plane/repo_combination_profiles.json` 刷新 `agent/extensions/index.json`、`config/control_plane/profile_registry.tsv`、`agent/extensions/provenance.json`、`agent/extensions/lock.json` 与仓库根 `openclaw-stack.lock.json`，再进入部署门禁。升级、回滚和 evidence 记录流程见 [`../operations/stack-upgrade-runbook.md`](../operations/stack-upgrade-runbook.md)。

受管扩展 profile 可用时，优先按 profile 验证：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane summary --control-plane-profile <profile-id>
```

查看显式登记、有效自动发现、无效候选与冲突诊断：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane config profiles --format json
```

查看和校验扩展 runtime venv：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime extension-env ensure --extension <extension-id> --offline --json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime extension-env status --extension <extension-id> --json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime extension-env verify --extension <extension-id>
```

owner-aware surface 固定这样读取：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane diagnostics show-index --extension <extension-id> --control-plane-profile <profile-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops list-entries --control-plane-profile <profile-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch observability render-objects --extension <extension-id> --control-plane-profile <profile-id>
```

直接验证未形成有效 profile 的仓内合同 service 配置文件时，再显式传入 `--config-path /path/to/<service>.json`。

如果扩展包声明了 scheduler job、module 或 group，再继续跑扩展包自己的链路回归；主仓库默认 smoke 不覆盖扩展包自己的回归范围。

平台合同测试中的合成受管扩展 fixture 只用于覆盖 runtime paths、testing manifest、diagnostic surface、group、job、model 与 target 的最小平台合同。它不是正式业务扩展，也不进入受管扩展索引或 profile registry。仓库真实状态测试只验证受管扩展索引与 profile registry 的一致性。

## 下一步

- 需要项目级结构边界：回 [control-plane-baseline.md](control-plane-baseline.md)。
- 需要看 agent plane 治理入口：回 [agent-governance.md](agent-governance.md) 和 `agent/README.md`。
- 需要为具体业务链路维护包内说明：在扩展包自己的 `README.md` 和 `docs/` 中维护，不写入主仓库默认运行面。
