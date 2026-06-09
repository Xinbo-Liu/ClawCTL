# 支持边界说明

本页定义当前主仓库的正式支持边界。

## 正式支持面

- `config/control_plane/service.json` 作为 kernel / base 基线。
- `config/control_plane/profiles/agent_platform.service.json` 作为正式默认运行 profile。
- `config/control_plane/repo_combination_profiles.json` 中登记的受控组合 profile 作为仓内多扩展组合白名单。
- `config/control_plane/extensions.d/agent_platform.json` 作为主仓库内的平台 extension。
- `agent/control_plane/` 中的共享 runtime / registry / object-policy 目录。
- 通用 extension 机制、多 manifest 目录装配、owner-aware surface 读取与 generic dispatch / diagnostics / recovery 命令。

正式运行面只覆盖平台服务对象：

- `openclaw-private-ingress`
- `openclaw-official-gateway`
- `openclaw-internal-api`
- `openclaw-control-plane-scheduler`

## 不属于主仓库支持面的内容

- 任何不属于主仓库正式面的业务链路。
- 依赖主仓库内置业务链路或隐式扩展装配的运行路径。
- 未登记的 CLI、wrapper、alias、standalone docs 或业务模块。

## 扩展边界

- 仓内 extension 与仓内 managed explicit extension 都不属于默认正式运行面。
- managed agents extension 可通过显式 `--control-plane-profile`、有效自动发现 profile 或仓内合同 service 的显式 `--config-path` 接入；没有显式选择时，运行面固定回到 `agent_platform`。
- 仓内登记的受控组合 profile 只加载平台 manifest 与白名单声明的受管扩展合同 manifest 目录。
- 主仓库只保证通用 extension 机制、冲突检测、ownership 解析与运行隔离。
- 仓内扩展自己的业务链路、provider 依赖、模型依赖和值守脚本，不属于主仓库正式支持面；仓外非标准 extension manifest 不属于正式入口。

## Windows 无容器仓库级回归入口

- 仓库根目录的宿主机 Python 入口只保留 `python -m unittest openclaw...` 与 `python -m openclaw.testing.repo_host ...`；仓库根 `openclaw/` 包负责把导入入口接到 `python/` 下的正式包目录。
- 仓库级 Python 入口通过 bootstrap 环境、`python/sitecustomize.py` 与仓库根 `openclaw/` 导入桥共同禁写字节码，并向子进程传递 `PYTHONDONTWRITEBYTECODE=1`；仓库级 Python 回归不得在工作区生成 `__pycache__` 或 `.pyc` 残留。
- Windows 无 Docker / 无容器主机可通过 `python -m openclaw.testing.repo_host` 执行仓库级 Python 回归。
- `openclaw.testing.repo_host` 模块用于 Windows 无容器仓库级 Python 回归；其职责是装配命名 suite 与复用 repo unittest 参数面。
- 该入口覆盖导包、目标单测与静态门禁相关纯 Python 回归，不进入正式发布、运行、setup 或 `one_click_*` 支持面。
- 可执行的仓库级 Python 命令包括：
  - `python -m unittest openclaw.tests.governance.test_package_layout -q`
  - `python -m openclaw.testing.repo_host suite repo-check -q`
  - `python -m openclaw.testing.repo_host unittest python/openclaw/tests/testing/test_repo_unittest.py`
- 与仓库级 Python 回归、静态治理和通用 control-plane 命令直接相关的正式 shell 入口如下：
  - `bash ./scripts/runtime/run_openclaw_python_tool.sh ...`
  - `bash ./scripts/testing/check_repo_test_readiness.sh`
  - `bash ./scripts/testing/run_repo_unittest.sh ...`
  - `bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json]`
