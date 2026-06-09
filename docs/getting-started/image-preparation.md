# 镜像准备说明

本文档只负责 **镜像阶段**：如何判断走在线还是离线、如何准备 `config/runtime/source_strategy.json` 声明的部署镜像合同角色，并说明 compose 运行镜像集合与控制面执行介质的一一边界，以及通过后如何进入部署主链。

这不是首次部署总教程。首次部署主路径统一看 [`quickstart.md`](./quickstart.md)；`one_click_deploy.sh` 的阶段顺序与 `--resume-from` 也统一看 [`quickstart.md`](./quickstart.md)。

## 进入本页前应完成的事项

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh
bash ./scripts/setup/prepare_control_plane_medium.sh
bash ./scripts/setup/one_click_config.sh
```

离线目标机使用离线 readiness 命令：

```bash
bash ./scripts/doctor/check_docker_host_readiness.sh --offline
```

说明：

- `check_docker_host_readiness.sh` 只做宿主机、Docker、Compose、网络与镜像来源只读检查；它不准备 host 控制面执行介质；
- 进入 `one_click_config.sh` 或任意 host 控制面命令前，必须先执行 `prepare_control_plane_medium.sh`；
- `deploy/.env` 必须已经由 `one_click_config.sh` 生成后，才能执行带 `--env-file deploy/.env` 的最终合同检查；
- 宿主机前提未满足时，先回 [`environment-setup.md`](./environment-setup.md)；
- 现场只修宿主机时，不在本页重复处理 Docker 安装，统一回 `prepare_docker_host.sh`；
- 仓库级静态 Python 入口治理由发布门禁覆盖；目标机 readiness 只检查宿主机、Docker/Compose、网络与镜像来源。

## 先判断走哪条路线

### 直接走在线路线

同时满足以下条件时，执行在线路线：

- 目标机能够访问当前 selected runtime source；
- 目标机允许执行 `docker pull`；
- 不需要把镜像归档转交到封闭网络。

中国国内网络首轮部署在进入在线镜像路线前，先完成宿主机网络 profile：

```bash
sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn
bash ./scripts/doctor/check_docker_host_readiness.sh
```

说明：

- `--network-profile cn` 会把 CentOS 7 vault repo 与 Docker Yum repo 固定到 `aliyun_cn`，并继续写入 docker host 真源中的 registry-mirrors；
- Python / Nginx 默认 pin 已固定为 Daocloud tag@digest；Docker daemon registry-mirrors 只作为补充传输加速；
- official Gateway 默认 pin 使用 GHCR canonical 来源；GHCR 在中国国内网络不可达时，先按候选镜像站验证链切换，不直接改 mutable tag。

### 直接走离线路线

满足以下任一条件时，执行离线路线：

- 目标机不能访问当前镜像源；
- 目标机不允许在线拉取镜像；
- 需要把已经验证过的镜像归档送入封闭环境。

## 镜像阶段只允许的运行镜像

当前 compose 运行面只允许 `source_strategy` 中 `compose_runtime.enabled=true` 的镜像；host 控制面另有独立执行介质镜像。当前运行镜像 env key、角色名与 compose selector 以 `source_strategy` 和 `show_deployment_image_status.sh` 输出为准。

其中，official gateway、runtime Python 服务与 private ingress 都按 `source_strategy` 的 compose runtime 声明进入运行面；额外运行视角只允许通过 profile + extension 显式进入。

因此，运行面检查围绕 `source_strategy` 声明的 compose 运行镜像集合展开；控制面镜像准备单独通过 `prepare_control_plane_medium.sh` / `ensure_control_plane_image.sh` 处理。

## 统一先执行的核对命令

无论在线还是离线，先执行：

```bash
bash ./scripts/images/show_deployment_image_status.sh
bash ./scripts/images/check_openclaw_overlay_contract.sh
```

说明：

- `show_deployment_image_status.sh` 用来核对当前 pin、compose 引用与本地镜像可用性；
- `check_openclaw_overlay_contract.sh` 用来确认 compose 没有引入额外镜像链。

## 在线准备路径

### 路径 A：默认一键入口

在线完整部署执行下面这条命令：

```bash
bash ./scripts/setup/one_click_deploy.sh
```

只准备镜像与 compose 渲染、不启动服务时执行：

```bash
bash ./scripts/setup/one_click_deploy.sh --prepare-only
```

### 路径 B：手工分步执行

需要单独完成镜像阶段时，执行顺序固定如下：

```bash
bash ./scripts/images/check_openclaw_release.sh
bash ./scripts/images/pull_images.sh
bash ./scripts/setup/prepare_control_plane_medium.sh
bash ./scripts/runtime/run_openclaw_python_tool.sh runtime mounts sync-compose --env-file deploy/.env --output state/openclaw/control_plane/setup/docker-compose.effective.yml
bash ./scripts/images/show_deployment_image_status.sh
bash ./scripts/images/check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local
bash ./scripts/images/verify_gateway_browser.sh
```

说明：

- `pull_images.sh` 默认使用 `PULL_GATEWAY_CANDIDATE_MODE=auto-switch`；当目标机处于 CN profile 且 official GHCR Gateway 有等值 candidate 时，只改写当前 `deploy/.env` 的 `OPENCLAW_OFFICIAL_GATEWAY_IMAGE=<candidate>:<tag>@<digest>`，并写出 `state/image_pull/gateway_source_selection.json`。`config/image_pins/openclaw.env` 仍保留 canonical official pin。
- 若 `state/image_pull/gateway_source_selection.json` 记录 `envRewritten=true`，必须在 `pull_images.sh` 后重新加载镜像 env 并重新渲染 `state/openclaw/control_plane/setup/docker-compose.effective.yml`；`one_click_deploy.sh` 会自动完成这一步，手工分步执行时必须显式执行上面的 `runtime mounts sync-compose`。
- `check_deployment_image_contract.sh --compose-file ... --require-local` 会比较最终 compose 实际 image refs 与 selected refs，并在 `docker compose up` 前区分 candidate 已拉取但 compose 仍指 canonical、selected ref 未拉取、digest 不一致、registry 不可达，或 verified local refs 的合同 image id 证明缺失/不匹配。
- `PULL_GATEWAY_CANDIDATE_MODE=fail-fast` 会在发现等值 candidate 后失败并保留当前 env；`PULL_GATEWAY_CANDIDATE_MODE=off` 会跳过 candidate 判定。
- `prepare_control_plane_medium.sh` 是 host 控制面执行介质的唯一显式入口；其内部统一委托 `ensure_control_plane_image.sh` 先复用本地 `OPENCLAW_CONTROL_PLANE_IMAGE`，本地缺失时先导入显式指定或自动发现的 `state/image_artifacts/deployment_images_*.tar`，归档未命中时再执行网络拉取。
- 在线目标机切换到离线归档时，host 控制面入口由 `prepare_control_plane_medium.sh` 统一完成控制面镜像准备。

执行完成后，再进入部署主链：

```bash
bash ./scripts/setup/one_click_deploy.sh --resume-from docker_compose_config
```

### 在线路线通过标准

同时满足以下条件，即判定在线镜像准备通过：

1. `pull_images.sh` 成功；
2. `show_deployment_image_status.sh` 能看到部署镜像合同角色都已本地可用，且 verified local refs 通过 image id 校验；
3. `check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local` 通过；
4. `verify_gateway_browser.sh` 通过；
5. `check_openclaw_overlay_contract.sh` 通过。

## 离线准备路径

离线路径分为 **联网导出机** 和 **目标机** 两段。

### 第一步：在联网机器导出归档

```bash
bash ./scripts/images/export_deployment_images.sh
ls -lh state/image_artifacts/deployment_images_*.tar
```

说明：

- 导出的 `deployment_images_*.tar` 默认是 OpenClaw deployment image bundle，内含 `deployment-images.contract.json`、`deployment-images.docker.tar` 与 `deployment-images.sha256`；
- 合同清单记录 `source_strategy` 声明的部署镜像角色、env var、pin ref、source tag、managed role tag、digest、image id 与导出时间；
- 导出会生成少量受管 tag，目的是让 `docker load` 后的本地镜像拥有可解释、可清理的部署角色名；compose 运行态只接受同时具备 pin、managed role tag 与合同 image id 证明，且当前 Docker image id 仍匹配的 local ref，这些 tag 可由 `show_deployment_image_status.sh` 解释，并由 `cleanup_image_aliases.sh` 保留；
- 导出完成后，记录归档真实路径，再复制到目标机。

### 第二步：在目标机导入归档

通用命令模板：`bash ./scripts/images/load_deployment_images.sh "${imageArchivePath}"`。

示例路径：`<local-path>`。不显式传路径时，脚本自动选择 `state/image_artifacts/` 下最新的 `deployment_images_*.tar`；在执行 `docker load` 前，先按当前 pin 校验归档内是否覆盖部署镜像合同角色。

归档会按 `deployment-images.contract.json` 校验并写出 verified local refs，包含每个角色的 pin ref、managed role tag 与 image id。compose 运行态只接受 image id 匹配的 managed role tag；pin ref 仍作为供应链合同保留。

```bash
bash ./scripts/images/load_deployment_images.sh "${imageArchivePath}"
bash ./scripts/images/load_deployment_images.sh <local-path>
bash ./scripts/images/show_deployment_image_status.sh
bash ./scripts/images/check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local
bash ./scripts/images/verify_gateway_browser.sh
```

通过后进入离线部署：

```bash
bash ./scripts/setup/one_click_deploy.sh --offline --image-archive <local-path>
```

### 离线路线通过标准

同时满足以下条件，即判定离线镜像准备通过：

1. `load_deployment_images.sh` 成功，或 `prepare_control_plane_medium.sh` 已经通过同一份部署镜像归档把 `OPENCLAW_CONTROL_PLANE_IMAGE` 导入本地；
2. `show_deployment_image_status.sh` 显示部署镜像合同角色都已本地可用，且 verified local refs 通过 image id 校验；
3. `check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local` 通过；
4. `verify_gateway_browser.sh` 通过。

## Candidate 验证与默认 pin 提升

### 先做候选验证

当上游 stable / correction tag 变化，或者需要验证新 digest 时，先执行：

```bash
bash ./scripts/images/check_openclaw_release.sh
bash ./scripts/images/check_openclaw_digest.sh
bash ./scripts/images/update_openclaw_pin.sh --ref <image:tag@sha256:...> --mode candidate
```

切换到已登记的 Gateway 候选仓库时，使用候选仓库派生模式：

```bash
bash ./scripts/images/update_openclaw_pin.sh --candidate-repo ghcr.nju.edu.cn/openclaw/openclaw --mode candidate
```

该命令用于中国国内网络下 GHCR 不可达但候选镜像站可达的场景；执行后必须继续跑本页声明的 candidate 验证顺序，确认 selected runtime source 的 tag@digest 与当前 pin 闭合。

候选仓库派生模式的验证顺序先执行 `check_openclaw_digest.sh`，再进入 pull、状态、合同、浏览器与部署验证。

完整验证顺序统一看本页与 [`quickstart.md`](quickstart.md)。

默认首装的 release gate 使用 `relaxed_install` 策略：当前 pin tag@digest 可解析、digest 与声明 pin 一致且官方运行合同可验证时，上游 latest 更高只记 WARN。发布门禁、升级验证或 candidate 提升验证才使用 `bash ./scripts/setup/one_click_test_basic.sh --strict-release-check`。

### 通过后再 promote

只有 candidate 验证确认 selected runtime source 的目标 tag@digest 可用后，才允许提升默认 pin：

```bash
bash ./scripts/images/update_openclaw_pin.sh --ref <image:tag@sha256:...> --mode promote
bash ./scripts/setup/one_click_config.sh
```

Python / Nginx 运行时镜像同理：

```bash
bash ./scripts/images/update_runtime_pin.sh --mode candidate --runtime-python-ref <image:tag@sha256:...>
bash ./scripts/images/update_runtime_pin.sh --mode promote --control-plane-ref <image:tag@sha256:...> --runtime-python-ref <image:tag@sha256:...>
bash ./scripts/setup/one_click_config.sh
```

## 常见故障处理

### 在线拉取失败

```bash
bash ./scripts/images/show_deployment_image_status.sh
bash ./scripts/images/check_openclaw_release.sh
bash ./scripts/doctor/check_docker_host_readiness.sh
```

中国国内网络先确认宿主机已经执行 `sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn`；默认在线拉取会在等值 candidate 可用时自动切换当前 `deploy/.env`，并写出 `state/image_pull/gateway_source_selection.json`。若 `check_docker_host_readiness.sh` 报 selected source 不可达但 candidate 可用，直接复跑 `pull_images.sh`；若 selected / candidate source 都不可达，不要继续硬拉，直接切换到离线路线。

### 覆盖合同失败

```bash
bash ./scripts/images/check_deployment_image_contract.sh --env-file deploy/.env --compose-file state/openclaw/control_plane/setup/docker-compose.effective.yml --require-local
bash ./scripts/runtime/show_runtime_compose_config.sh
```

先确认 compose 只引用 `source_strategy` 声明的 compose runtime 镜像集合，并确认控制面执行介质由 `OPENCLAW_CONTROL_PLANE_IMAGE` 独立治理。若提示 candidate 已拉取但 compose 仍指 canonical，重新加载镜像 env 并重渲染 effective compose；若提示 final compose image ref 本地不存在，先补拉 selected ref 或导入离线归档，不要等到 `docker_compose_up` 才失败。

### 浏览器校验失败

```bash
bash ./scripts/images/verify_gateway_browser.sh
bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway 2>/dev/null || true
```

先区分是镜像内容问题，还是当前 gateway 容器尚未启动。

## 本页之后去哪里

- 回正式主链：[`quickstart.md`](./quickstart.md)
- 看阶段顺序与 `--resume-from`：[`quickstart.md`](./quickstart.md)
- 看 pin 更新后的验证顺序：[`image-preparation.md`](image-preparation.md)
- 看运行来源分层：[`runtime-service-reference.md`](../operations/runtime-service-reference.md#运行镜像来源与-source-strategy)
