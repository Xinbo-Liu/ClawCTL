# 仓库结构治理

## 一、正式面

| 目录                                                                 | 正式职责                   | 允许内容                                                 | 禁止内容                    |
|--------------------------------------------------------------------|------------------------|------------------------------------------------------|-------------------------|
| `agent/extensions/<extension-id>/agent/modules/`                   | 模块声明与局部说明面             | `module.json`、README、skills、permissions、tools、bin    | 平台桥接、其他扩展包私有实现          |
| `agent/extensions/<extension-id>/tests/`                           | 扩展包内部测试真源              | unit、regression、modules、support、fixtures             | 主仓库通用 extension 框架测试    |
| `agent/extensions/<extension-id>/python/<python-package>/modules/` | 模块私有 Python 真源         | 单模块业务实现、模块专属 CLI、模块私有编排                              | 平台桥接、跨模块共享领域代码          |
| `agent/extensions/<extension-id>/python/<python-package>/domains/` | 扩展包内共享领域层              | 共享模型、共享函数、共享验证、共享编排支撑                                | 单模块独占实现、跨扩展包平台能力        |
| `agent/extensions/<extension-id>/agent/control_plane/`             | 扩展包共享 control-plane 对象 | groups、jobs、models、targets                           | base control-plane 基座定义 |
| `python/openclaw/control_plane/`                                   | 平台装配、派生、调度与运行桥         | registry、scheduler bridge、runtime adapters、doctor 支撑 | 模块私有业务实现                |
| `scripts/`                                                         | 平台级命令入口                | 部署、doctor、导出、平台级运行与统一入口                              | 模块私有长期真源脚本              |

## 二、结构结论

### 1. 模块声明与实现根

- `agent/extensions/<extension-id>/agent/modules/<agent_ref>/`：声明、文档、局部能力边界、薄启动入口。
- `agent/extensions/<extension-id>/python/<python-package>/modules/<agent_ref>/`：模块私有 Python 实现。
- `agent/extensions/<extension-id>/tests/modules/<agent_ref>/`：模块 smoke / regression 测试。

### 2. 领域共享层与平台层分层

- `agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/`：承载扩展包内领域共享代码与共享验证资产。
- `python/openclaw/control_plane/`、`scripts/`、`config/`、`docs/`：承载平台级装配、调度、校验与项目级说明。

### 3. `scripts/` 的边界

顶层 `scripts/` 承担平台入口，不承担模块私有长期真源。模块薄启动器必须停留在 `agent/extensions/<extension-id>/agent/modules/<agent_ref>/bin/`，扩展包共享领域代码必须停留在 `agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/`。

## 三、固定规则

1. agent / implementation / skill / permission / tool 只允许由扩展包内模块清单与模块资产直接内存派生，不得回升为人工并列真源。
2. 项目级文档只允许摘要正式事实，不维护非正式材料。
3. 根级 `agent/modules/`、`agent/domains/`、`python/openclaw/modules/`、`python/openclaw/domains/` 不属于正式作者模型。

## 四、结构与脆弱性治理矩阵

| 治理面                    | 正式约束                                                         | 真源或守卫                                                                                                                                                                                                                             | 处置规则                                    |
|------------------------|--------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------|
| 作者模型根面                 | 根级 agent authoring 目录和 `python/openclaw/` 下业务域占位包不得存在        | `agent/governance/baseline.md`、`docs/architecture/python-package-layout.md`、`check_architecture_import_guards.sh`                                                                                                                 | 删除非正式根面，把正式模块与领域代码放回扩展包内真源              |
| profile 与 extension 入口 | 默认运行入口、显式 profile registry、受控组合 profile、有效自动发现和 extension manifest 不得重复登记 | `config/control_plane/profile_registry.tsv`、`config/control_plane/repo_combination_profiles.json`、`agent/extensions/<extension-id>/config/control_plane/profiles/*.service.json`、`config/control_plane/profiles/agent_platform.service.json`、`config/control_plane/extensions.d/*.json` | 更新登记或发现真源，删除硬编码 profile 常量、无校验目录扫描与并列索引 |
| 模块声明与实现根               | 模块合同由模块主清单与局部资产派生，README、job、脚本帮助不得重复维护合同                    | `agent/extensions/<extension-id>/agent/modules/<module_ref>/module.json` 与同目录资产                                                                                                                                                   | 保留模块主清单与局部资产，派生视图从真源读取                  |
| 模块 bin 脚本              | 受管模块 `bin/*` 必须被正式真源消费                                       | `check_agent_runtime_script_orphans.sh`                                                                                                                                                                                           | 删除孤儿脚本，或补齐模块清单、job、工具面中的正式引用            |
| 模板化可选面                 | 模块目录不得保留未消费的模板壳、空 README 或可选目录                               | `check_agent_module_optional_surface.sh`                                                                                                                                                                                          | 删除未消费可选面；需要保留时补齐正式引用和职责说明               |
| job 冗余面                | job manifest 不维护可推导默认字段或重复运行面                                | `check_agent_job_surface.sh`                                                                                                                                                                                                      | 运行 job surface prune，把默认值收回派生逻辑         |
| Python 根层摊平            | 同职责文件族进入对应子包并使用职责短名                                          | `docs/architecture/python-package-layout.md`、`architecture_import_guards.py`                                                                                                                                                      | 进入对应子包并使用职责短名，禁止模块 alias 和桥接入口         |
| Python 反向依赖            | `lib` 与平台层不得反向导入业务域、扩展包或模块面                                  | `architecture_import_guards.py`                                                                                                                                                                                                   | 移除反向依赖，把业务实现留在扩展包，平台层只保留装配合同            |
| Python 启动脆弱点           | 模块冷启动不得依赖 import 顺序、副作用或包初始化偶然性                              | `check_cold_start_imports.sh`                                                                                                                                                                                                     | 修正导入边界、延迟副作用、补齐独立冷启动导入检查                |
| Python 路径引导            | 仅统一 bootstrap 与 repo root resolver 可以管理 Python 路径            | `architecture_import_guards.py`、`config/governance/support/repo_python_bootstrap.env`                                                                                                                                             | 使用统一 bootstrap 与 resolver，删除本地路径拼装      |
| Shell Python 路径        | shell wrapper 不复制 Python path 组装逻辑或绕过仓库合同                    | `scripts/lib/repo_python_env.sh`、`check_shell_pythonpath_contract.sh`                                                                                                                                                             | wrapper 统一读取 repo Python bootstrap 合同   |
| 运行态路径                  | 文档、脚本与代码中的 runtime env、host 路径与 profile 引用必须能回到结构化真源         | `config/runtime/paths.json`、`python/openclaw/runtime/path_lint.py`、`config/control_plane/profile_registry.tsv` 与有效自动发现结果                                                                                                          | 注册逻辑对象并通过 resolver 使用，删除无真源路径           |
| 本地工作区残留                | `.idea`、`tmp`、缓存、派生物和运行态输出不得污染交付面                            | `config/governance/support/local_workspace_policy.json`、`check_local_workspace_hygiene.sh`                                                                                                                                        | 默认只清理可丢弃目标，受管状态和导出证据只显式点名处理             |
| 生成文档漂移                 | 派生文档、脚本索引和 runtime 参考页必须与配置真源一致                              | `config/governance/docs/*.json`、`check_generated_docs_sync.sh`、docs validators                                                                                                                                                    | 先修真源或 renderer，再重新生成派生文档                |
| 禁止存在面                  | 扩展专属 profile、源码、脚本和并列装配目录不得回到主仓库根面                           | `config/governance/validation/absent_surfaces.json`、`check_delivery_cleanliness.sh`                                                                                                                                               | 更新结构化禁止存在事实并删除并列资产，不新增并列说明面             |
| 发布门禁漂移                 | README、脚本或测试不得手写另一套 release gate 顺序                          | `python/openclaw/doctor/release/repo_release_gate_support.py`                                                                                                                                                                     | 只更新门禁真源，由 shell 帮助面和 JSON 输出派生          |
