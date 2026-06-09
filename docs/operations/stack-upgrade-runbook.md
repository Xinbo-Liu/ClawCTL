# Stack Release 升级手册

本页定义基座仓库与扩展仓库同步升级时的正式组合、验证、部署与回滚流程。

## 本页解决什么问题

- 基座和扩展各自演进时，如何形成一个可部署组合。
- `openclaw-stack.lock.json`、`agent/extensions/lock.json` 与 profile registry 各自承担什么职责。
- 候选组合进入目标机前必须通过哪些门禁。
- 基座升级、扩展升级、协同升级和紧急回滚分别如何执行。

## 版本真源

- 基座版本真源是 `config/control_plane/platform_version.json` 与 `pyproject.toml`。
- 扩展版本真源是扩展 manifest 的 `version`、`compat.controlPlane`、`dependencies` 与 `migrations`。
- 扩展源码完整性由 `agent/extensions/lock.json` 记录，字段包含 manifest version、content hash、依赖解析和 migration 记录。
- 可部署组合由仓库根 `openclaw-stack.lock.json` 固定，字段包含基座 repo/commit/version/hash、扩展 repo/commit/version/hash、profile、deploy env schema hash 与生成物摘要。
- 基座 `releaseBundleHash` 只覆盖平台源码文件集合；`deploy/.env`、`deploy/site.env`、`deploy/targets.d/`、证书、secrets、logs、state 与扩展物化目录属于运行态/扩展态，不进入基座 hash，也不能作为刷新 stack lock 的隐式来源。
- `agent/extensions/provenance.json` 是组合来源证明，由 `materialize`、带 `--source-metadata` 的 lock 刷新或 Git worktree 内的 `stack lock` 写出；`openclaw-stack.lock.json` 只记录该文件摘要，strict 验证同时核对 provenance 内容与 stack lock，不允许 stack lock 里的 repo/commit 字段自证来源。
- 正式部署只接受固定 commit 或 tag 的 stack lock；浮动分支只允许在隔离验证环境使用。

## 候选组合生成

扩展仓库交付到组合层时，扩展目录仍物化到运行合同路径：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane stack materialize --composition stack.composition.json --replace
```

`stack.composition.json` 的 `extensions[]` 是目标组合的完整扩展集合。每个扩展条目必须声明 `id`，并声明本地 `sourcePath` 或远端 `repo + commit`。若组合生成时发现 `agent/extensions/<extension-id>/` 下存在未列入 composition 的扩展，命令会中止，避免未登记扩展目录进入 stack lock。

从 Git 远端生成候选时，`materialize` 会把实际 checkout 后的完整 commit SHA 写入 `agent/extensions/provenance.json` 与 `openclaw-stack.lock.json`。composition 若声明浮动 `base.commit`，只能在当前 Git worktree 内解析为完整 HEAD 后记录；若声明完整基座 SHA，后续 strict gate 会校验该 commit 的基座文件内容哈希与 stack lock 的 release bundle hash 一致。只读预览命令为：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane stack materialize --composition stack.composition.json --dry-run
```

仓内已物化扩展需要刷新组合元数据时使用：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane stack materialize --refresh-current
```

该命令刷新 `agent/extensions/index.json`、`config/control_plane/profile_registry.tsv`、`agent/extensions/lock.json` 与 `openclaw-stack.lock.json`，并保留已有来源证明。只需要重写 stack lock 时使用：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane stack lock
```

在 Git worktree 内，`stack lock` 会以当前 Git HEAD 刷新 bundled extension 的 provenance；在不带 `.git` 的物化目录内，若源码同步阶段已经解析出目标基座 commit，必须通过来源元数据刷新来源证明与 stack lock，不能只改 `openclaw-stack.lock.json`：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane stack lock --source-metadata state/openclaw/control_plane/upgrade/source_sync_metadata.json --update-source-provenance
```

## 候选组合门禁

候选组合进入目标机前必须通过：

```bash
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane stack verify --strict-release --json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane config profiles --format json
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane extensions doctor
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane validate registry --control-plane-profile <profile-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane summary overview --control-plane-profile <profile-id>
bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime --config-path <service-config> extension-env ensure --enabled --offline --json
bash ./scripts/doctor/run_repo_release_gate.sh
```

`--strict-release` 要求基座工作树干净、`base.commit` 可在本地 Git 历史中读取且其基座文件内容哈希等于 stack lock 的 release bundle hash、基座和每个扩展都具备固定 repo 与完整 40 位 Git commit SHA，并要求 `agent/extensions/provenance.json` 存在、摘要和内容均与 stack lock 一致。当前 HEAD 与 stack lock 的 `base.commit` 只有在 `source_metadata` 或已核验 provenance 提供相同 `releaseBundleHash` 时才允许不同；同一条件下，来源路径和内容哈希均未变化的 bundled extension `commit` 字段按 stack lock 归一化比较。这只覆盖 `openclaw-stack.lock.json`、`agent/extensions/provenance.json` 等不进入发布内容 hash 的锁/溯源提交。正式部署门禁必须启用该开关。

## 部署升级顺序

正式目标机升级默认使用统一入口：

```bash
bash ./scripts/setup/one_click_upgrade.sh --repo-url <git-url> --ref main
```

该入口会先备份受保护文件，开启 scheduler maintenance，修复脚本执行位，同步源码，生成 effective compose，自动 ensure active profile 的扩展离线 wheelhouse 与 venv，启动服务并等待所有 runtime services 进入 `running healthy`，再解除 maintenance 执行 run_all_once、full test 与 runtime evidence 导出。源码同步会写出 `state/openclaw/control_plane/upgrade/source_sync_metadata.json`，其中包含完整 40 位目标 commit 与目标源码的 `releaseBundleHash`。`stack verify --strict-release` 会带上这份来源元数据：真实 base release 内容漂移会阻断；仅 `openclaw-stack.lock.json`、`agent/extensions/provenance.json` 等被排除文件变化导致目标 commit 前进时，只要锁内 `releaseBundleHash`、当前源码 hash 和来源 metadata hash 三方一致，则视为同一基座内容。失败时 scheduler maintenance 保持 enabled，恢复命令会写入升级报告。若 strict stack verify 发现 lock drift、来源 hash 不一致、provenance 内容漂移或无法证明 release 等价，默认阻断；确认当前源码组合可作为新基线时，显式追加 `--refresh-stack-lock`，升级入口会先执行 `control-plane extensions lock` 刷新 `agent/extensions/lock.json`，再同步刷新 `agent/extensions/provenance.json` 与 `openclaw-stack.lock.json` 后继续。

手工拆解时固定顺序：

1. 冻结 scheduler 调度入口，停止自动作业推进。
2. 备份 state、当前 `openclaw-stack.lock.json`、`agent/extensions/provenance.json`、`agent/extensions/lock.json`、active extension env manifest、`deploy/site.env`、启用扩展的 `agent/extensions/<extension-id>/deploy/extension.env` 与 `deploy/targets.d/*.env`。
3. 部署基座文件和 stack lock 固定的扩展物化源码。
4. 执行 `control-plane stack verify --strict-release --json`；需要刷新基线时先执行 `control-plane extensions lock`，再执行带 `--source-metadata ... --update-source-provenance` 的 stack lock 刷新。
5. 执行 `extension-env ensure --enabled --offline --json`，同步扩展 wheelhouse、准备扩展 venv 并 verify。
6. 执行启用扩展的 migration。
7. 启动 runtime services，并用 `show_runtime_service_status.sh` 确认所有启用 target 均为 `running healthy`。
8. 恢复 scheduler 调度入口。
9. 执行 `run_control_plane_run_all_once.sh`、默认 full test 与 runtime evidence 导出。
10. 记录 stack lock、profile 列表、extension lock、migration applied ids、active venv manifest、服务健康状态与 release gate 结果。

## 升级类型

- 基座 patch 升级：扩展版本不变，只验证 compat、registry、runtime 与 release gate。
- 扩展 patch 升级：基座版本不变，只替换对应扩展 commit，重建 extension lock、stack lock 与扩展 venv。
- 协同 minor 升级：基座和一个或多个扩展同时升级，必须通过完整 stack 候选组合门禁。
- 破坏性 major 升级：基座提供升级约束；扩展仓先适配 next base；组合层切换到已适配组合。
- 紧急回滚：使用上一个 stack lock 重新物化组合；不可逆 migration 只能通过升级前 state 备份恢复或执行显式 forward-fix migration。

## 故障分流

- `stack verify` 失败：先修正 stack lock、来源证明、profile registry 或 compat，不进入部署。
- `extensions doctor` 失败：先执行 extension lock 或 migration 收口，再继续 wheelhouse 与 venv。
- `extension-env ensure` 失败：检查扩展 `requirements.lock`、`offline_wheelhouse/manifest.json`、wheel hash、Python 版本与 platform tag；失败时保留当前 active venv，不恢复 scheduler。
- runtime doctor 失败：保留 scheduler 冻结状态，先按 `troubleshooting.md` 定位 control-plane、internal-api、dispatch 或扩展 smoke 问题。

## 下一步

- 需要确认扩展目录合同：进入 [`../architecture/explicit-extension-packages.md`](../architecture/explicit-extension-packages.md)。
- 需要运行验收与 evidence：进入 [`runtime-service-reference.md`](runtime-service-reference.md)。
- 需要故障分流：进入 [`troubleshooting.md`](troubleshooting.md)。
