# Python 包布局合同

本文定义 `python/openclaw/` 的唯一目录治理口径。

## 本页解决什么问题

- 哪些目录必须使用子包分层，不得在根层摊平。
- 哪些根层文件允许作为入口、汇总或中性合同面。
- 子包内文件的短名规范，避免 `registry_loader_*`、`deploy_env_*` 一类职责前缀进入根层。
- 哪些结构预算会被 doctor / test 守卫持续校验。

## 适用范围

- `python/openclaw/control_plane`
- `python/openclaw/lib`
- `python/openclaw/setup`
- `python/openclaw/doctor`
- `python/openclaw/docs`
- `python/openclaw/guards`
- `python/openclaw/images`
- `python/openclaw/internal_api`
- `python/openclaw/release`
- `python/openclaw/runtime`
- `python/openclaw/scheduler`
- `python/openclaw/specs`
- `python/openclaw/testing`
- `python/openclaw/tests`

`agent/`、`config/`、`scripts/` 与顶层 `docs/` 只做引用联动，不作为本合同的主治理面。

## 根层规则

1. 根层只保留入口、汇总、neutral contract 或跨域不可再分的少量模块。
2. 同职责文件族一旦达到“前缀成组”状态，必须进入子包，不得在根层平铺。
3. 子包内统一使用短文件名，不保留重复前缀。
4. shell wrapper、CLI registry、配置规则、文档真源一律指向正式模块路径，不保留 Python 模块 alias。
5. `python/openclaw/` 顶层不得保留占位子包 `domains/`、`extensions/`、`modules/`；业务域与扩展实现必须落在正式 extension 包或既有治理子树内。
6. 仓库级 Python bootstrap 真源固定为 `config/governance/support/repo_python_bootstrap.env`、`lib/repo/bootstrap.py` 与 `lib/runtime/execution.py`；bootstrap 环境、`python/sitecustomize.py` 与仓库根 `openclaw/` 导入桥共同禁写字节码并传递 `PYTHONDONTWRITEBYTECODE=1`，仓库级 Python 入口不得生成 `__pycache__` 或 `.pyc` 工作区残留；仓库根 `openclaw/` 包负责把导入入口接到 `python/` 下的正式包目录，`openclaw.testing.repo_host` 模块负责命名 suite 与 repo unittest 参数装配。
7. `python/openclaw/` 顶层只允许登记的正式子包与 `__init__.py`、`cli.py`、`cli_registry.py`，新增顶层子包必须同步更新本合同与守卫。

## 正式布局

### `control_plane`

- 根层保留：`cli.py`、`surfaces.py`、`schema.py`、`manifest_models.py`、`artifact_policies.py` 等入口或中性合同面。
- 必需子包：
  - `agent/`
  - `api/`
  - `cli_support/`
  - `dispatch/`
  - `extensions/`
  - `jobs/`
  - `module_scheduler/`
  - `modules/`
  - `registry/`
  - `registry_loader/`
  - `registry_validation/`
  - `runtime/`
  - `stack/`

### `lib`

- 根层只保留 `__init__.py`。
- 必需子包：
  - `channels/`
  - `cli/`
  - `control_plane/`
  - `dispatch/`
  - `http/`
  - `io/`
  - `models/`
  - `repo/`
  - `runtime/`
  - `summary/`
  - `testing/`
- `lib/repo/bootstrap.py` 是仓库级路径引导真源，读取 `config/governance/support/repo_python_bootstrap.env`，统一生成 managed extension python roots 与 canonical repo `python/` 顺序。
- `lib/repo/install_defaults.py` 是安装默认值的内部真源，集中读取 `governance.install_defaults` 与 host state root 默认路径；repo 与 runtime 层需要这些值时统一依赖该 helper。
- `lib/repo/static_truth.py` 保留对外查询入口，内部委托 repo helper；`lib/runtime/path_resolver.py` 只解析路径占位符，不反向依赖 `static_truth`。
- `lib/runtime/execution.py` 是统一执行面真源，负责动态 callable 导入、模块 `main(argv)` 调用与 subprocess 运行环境构造。

### `setup`

- 根层只保留 `__init__.py`。
- 必需子包：
  - `deploy_env/`
  - `flow/`
  - `network/`
  - `surface/`

### `doctor`

- 根层只保留 `__init__.py`。
- 必需子包：
  - `agent_governance/`
  - `agent_modules/`
  - `platform/`
  - `release/`

### `docs`

- 根层只保留 `__init__.py`。
- 必需子包：
  - `renderers/`
  - `support/`
  - `validators/`

### `tests`

- 根层文件预算不包含 `test_*.py`。
- 必需子包：
  - `control_plane/`
  - `doctor/`
  - `extensions/`
  - `fixtures/`
  - `governance/`
  - `runtime/`
  - `setup/`
  - `support/`
  - `testing/`

### 其他根层子包

- `guards/`、`images/`、`internal_api/`、`release/`、`runtime/`、`scheduler/`、`specs/` 与 `testing/` 是登记的根层入口或合同面。
- 这些目录不得扩展为业务域实现承载面；业务扩展固定落在受管显式 extension 包内。

## 命名约束

- `registry_loader/*.py` 内只保留短名：`collections.py`、`config.py`、`runtime.py`、`virtual_surfaces.py`。
- `deploy_env/` 的 dispatch registry 相关逻辑固定收在 `deploy_env/dispatch_registry/`，不以根层 `deploy_env_*` 文件存在。
- `docs/validators/` 的校验模块固定使用职责短名，不使用 `documentation_*` 根层命名。
- `doctor/agent_modules/` 的 doctor 模块固定使用职责短名，不使用 `check_agent_module_*` 根层命名。

## 守卫合同

- `doctor/platform/architecture_import_guards.py` 必须同时校验：
  - 受限模块导入边界保持关闭。
  - 目标目录根级文件预算未超限。
  - 顶层 `python/openclaw/` 子包白名单未漂移。
  - 必需子包存在。
  - 职责前缀不得进入根层。
  - 核心实现层不得泄漏具体业务扩展名：守卫从 `agent/extensions/index.json` 与扩展 Python 包目录派生受管扩展 ID、业务域名和业务包名；非测试 `python/openclaw/`、核心 `scripts/`、base 与 agent_platform 配置只允许平台中性合同。
- 业务扩展目录、业务扩展自测、目标态文档与明确的 absent-surface 规则是合法真源，不作为核心实现层业务名泄漏处理；`agent/extensions/<extension-id>/tests/` 内的 package marker 只用于字节码守卫，不属于 agent authoring 面。
- 对应回归测试固定落在 `python/openclaw/tests/governance/test_package_layout.py`。

## 相关入口

- 路径治理与运行视角：[`path-governance.md`](path-governance.md)
- 控制面基线：[`control-plane-baseline.md`](control-plane-baseline.md)
- Agent 治理：[`agent-governance.md`](agent-governance.md)
