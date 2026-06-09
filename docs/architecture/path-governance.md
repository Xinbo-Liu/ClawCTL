# 路径治理标准

> 本文档是路径治理规则、基础视角映射与排障入口的唯一主文档。

## 文档目的

本文档说明同一逻辑对象在 host、gateway、scheduler 等运行视角下为什么会映射到不同路径，以及仓库如何保证这些路径长期一致。

## 为什么需要独立治理路径

正式运行视角固定为三个：

- host
- gateway
- scheduler

同一份数据在不同运行视角下看到的根目录不同。如果让业务代码或脚本自行拼接路径，最终一定会出现：

- 文档与运行态口径不一致；
- 宿主机路径与容器路径混用；
- 新增目录后没有统一注册；
- 排障时不知道应该查看哪个视角。

因此，路径真源统一定义在 `config/runtime/paths.json` 与对应 resolver。额外视角只能通过 active profile + extension 的 fragment 追加，不能把扩展包目录默认写回基座真源。

## 统一入口

仓库内所有运行态路径都围绕同一套 manifest 与主包实现：

- `config/runtime/paths.json`：逻辑路径对象真源。
- `python/openclaw/lib/runtime/path_resolver.py`：Python 侧 canonical 路径解析实现。
- `python/openclaw/runtime/generated_paths/`：运行态 env、`openclaw.json`、Gateway agent workspace 核心文件、路径索引与派生产物的 canonical 生成/校验 package。
- `python/openclaw/runtime/path_resolve_cli.py`：内部路径解析 CLI 实现。
- `python/openclaw/runtime/path_lint.py`：路径治理静态契约 lint 实现。
- `python/openclaw/runtime/workspace_templates.py`：workspace 模板同步与漂移校验实现。
- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths ...`：runtime path 的唯一 shell 入口，也是活动文档与人工排障默认入口。
- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime workspace [--check]`：workspace 模板同步与校验的主包入口。

常用示例：

- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths resolve state_root --view host`
- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths show-index`
- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths check-generated`
- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths render-generated`
- `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime workspace --check`

## 基座视角规则

| 视角        | 典型根目录                       | 说明                                                   |
|-----------|-----------------------------|------------------------------------------------------|
| host      | `<current-host-state-root>` | 宿主机脚本、打包、文档与人工排障视角                                   |
| gateway   | `/home/node/.openclaw`      | official Gateway 运行视角，承载 Gateway 自身运行态派生产物与控制面只读展示投影 |
| scheduler | `/home/openclaw/.openclaw`  | 唯一业务执行视角，承载 scheduler、runner 与内网业务执行能力               |

固定规则：

1. 文档中写 `<current-host-state-root>/...` 时，必须明确它是宿主机视角。
2. 业务代码、容器脚本与跨视角逻辑不得自行拼接 gateway、scheduler 或 extension 视角绝对路径，统一通过 manifest / resolver 解析。
3. Gateway 只持有 Gateway 自身运行态路径，以及从当前 active control-plane profile 派生的 agents、workspace 核心文件与 cron 只读展示投影；业务执行固定由 scheduler 承担。
4. state 根目录固定为正式 host state root，基座不得恢复第二套默认 control-plane 路径真源。

## 核心逻辑对象

基座常见对象包括：

- `state_root`
- `gateway_host_state_dir`
- `control_plane_host_state_dir`
- `dispatch_config_dir`
- `runtime_host_env`
- `runtime_gateway_env`
- `runtime_scheduler_env`
- `runtime_internal_api_env`
- `extension_envs_dir`
- `extension_wheelhouse_dir`
- `path_index_json`
- `path_index_markdown`

`extension_envs_dir` 属于扩展运行态 venv 派生产物，不进入仓库交付包。`extension_wheelhouse_dir` 是部署机上的离线安装缓存，由扩展包内的 `offline_wheelhouse/` 仓内真源显式同步生成，只供 sync / prepare / verify 与扩展 agent runtime 使用。扩展 profile 启用后，才会追加扩展包或业务域自己的 workspace、产物目录、健康文件与专用运行视角。全量对象以 `config/runtime/paths.json` 与当前 active profile 合并后的 `<current-host-state-root>/control_plane/path-index.*` 为准。

## 新增路径的标准动作

新增任何目录、日志或运行态文件时，按以下顺序处理：

1. 在 `config/runtime/paths.json` 或对应 extension fragment 中注册逻辑对象，并声明 `logical_group`。
2. 如果新对象共享新的宿主机或容器前缀，先判断是否需要抽成 grouped root，再注册 entry。
3. 执行 `bash ./scripts/runtime/run_openclaw_python_tool.sh runtime paths render-generated --repo-root "$PWD"`。
4. 执行 lint / smoke，确认代码、生成物与文档一致。
5. 如果需要对外说明，明确它属于 host、gateway、scheduler 或 extension-contributed view 中的哪个视角，以及属于哪个逻辑分组。

## 明确禁止

- 新增第二个 state 根目录。
- 在文档中把 host 路径写成容器内路径。
- 在 Bash 中复制 Python resolver 逻辑。
- 为某一个运行面单独维护私有路径表。
- 把 Gateway 再写成 `control-plane`，或把 scheduler 与 Gateway 视角重新混用。
- 把扩展包视角默认写回基座 manifest，再依赖 subtractive filtering 过滤掉。

## 相关入口

- 部署输入与路径人工入口：[`../getting-started/deployment-inputs.md`](../getting-started/deployment-inputs.md)
- 路径逻辑分组与运行合同总基准：[`control-plane-baseline.md`](control-plane-baseline.md)
- Python 包目录治理合同：[`python-package-layout.md`](python-package-layout.md)
- 运行排障：[`../operations/troubleshooting.md`](../operations/troubleshooting.md)
