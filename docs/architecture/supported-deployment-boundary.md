# 支持边界说明

本页定义当前主仓库的正式支持边界。

## 正式支持面

- `config/control_plane/service.json` 作为 kernel / base 基线。
- `config/control_plane/profiles/agent_platform.service.json` 作为正式默认运行 profile。
- `config/control_plane/repo_combination_profiles.json` 中登记的仓内组合 profile 作为多扩展组合白名单。
- `config/control_plane/extensions.d/agent_platform.json` 作为主仓库内的平台 extension。
- `agent/control_plane/` 中的共享 runtime / registry / object-policy 目录。
- 通用 extension 机制、多 manifest 目录装配、owner-aware surface 读取与 generic dispatch / diagnostics / recovery 命令。

默认基座运行面覆盖以下平台服务对象：

- `openclaw-private-ingress`
- `openclaw-official-gateway`
- `openclaw-internal-api`
- `openclaw-control-plane-scheduler`

## 不属于主仓库支持面的内容

- 任何不属于主仓库正式面的业务链路。
- 依赖主仓库内置业务链路或隐式扩展装配的运行路径。
- 未登记的 CLI、wrapper、alias、standalone docs 或业务模块。

## 扩展边界

- 默认正式运行面启用平台 extension `agent_platform`；仓内业务 extension 与 managed explicit extension 需显式选择。
- managed agents extension 可通过显式 `--control-plane-profile`、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入；没有显式选择时，运行面固定回到 `agent_platform`。
- 仓内登记的组合 profile 只加载平台 manifest 与白名单声明的受管扩展合同 manifest 目录。
- 基座负责通用 extension 机制、冲突检测、ownership 解析与运行隔离；所选扩展可声明自己的 runtime service、对象和验收入口。
- 扩展业务链路、provider 依赖、模型依赖和业务验收由扩展文档定义，不由基座的公共机制保证业务结果。仓外非标准 extension manifest 不属于正式入口。

## 仓库级回归与 Python 执行边界

- 仓库级正式回归、release gate、docs 静态检查与 control-plane Python 命令统一通过 shell wrapper 进入控制面容器或 runtime 容器。
- 仓库根 `openclaw/` 导入桥只服务容器化入口和测试进程的包解析，不提供宿主机 Python 命令支持面。
- `python/sitecustomize.py` 与仓库 bootstrap 环境继续负责禁写字节码，并向子进程传递 `PYTHONDONTWRITEBYTECODE=1`；仓库级回归不得在工作区生成 `__pycache__` 或 `.pyc` 残留。
- 与仓库级回归、静态治理和通用 control-plane 命令直接相关的正式 shell 入口如下：
  - `bash ./scripts/runtime/run_openclaw_python_tool.sh ...`
  - `bash ./scripts/testing/check_repo_test_readiness.sh`
  - `bash ./scripts/testing/run_repo_unittest.sh ...`
  - `bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json]`
