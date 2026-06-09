# 第三方组件与许可声明

本文档用于明确本仓库当前已识别的第三方组件、镜像与依赖边界。凡不属于著作权人原创部分的内容，均不得在软著登记、商业授权或对外宣传中表述为“全部自主原创”。

## 一、适用原则

1. 本仓库原创部分与第三方组件严格区分。
2. 第三方组件的著作权、商标权、许可权与其他权利归各自权利人所有。
3. 使用、复制、分发、部署或再交付第三方组件时，分别遵守其上游许可证、镜像条款或服务条款。
4. 本项目对第三方组件的引用，不构成对第三方权利的转让、排他主张或再许可。

## 二、当前已识别的第三方组件

### 1. OpenClaw 官方 Gateway 镜像

- 识别方式：`config/image_pins/openclaw.env`、`deploy/docker-compose.yml`
- 当前引用：`ghcr.io/openclaw/openclaw:2026.6.1@sha256:b12f76a7947e4cdd328bf3ea1045d41a5494b33852c911e9bc4fdd03dde469d5`
- 上游仓库：`openclaw/openclaw`
- 上游许可：MIT License
- 本仓库处理口径：本项目仅引用其官方 Gateway 运行时镜像，不主张该镜像及其上游源代码的著作权；上游 MIT 许可文本见 `LICENSES/openclaw-MIT.txt`。

### 2. Python 运行时镜像

- 识别方式：`config/image_pins/runtime.env`、`deploy/docker-compose.yml`
- 当前引用：`docker.m.daocloud.io/library/python:3.11.15-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47`
- 上游来源：Docker Official Image / Python
- 许可说明：Docker Hub 官方说明指出，该官方镜像中的软件可能同时受其他许可证约束，镜像使用者需自行确认其中全部软件的许可合规性。
- 本仓库处理口径：本项目仅将其作为运行时载体引用，不主张镜像及其内含软件的著作权。

### 3. Nginx 运行时镜像

- 识别方式：`config/image_pins/runtime.env`、`deploy/docker-compose.yml`
- 当前引用：`docker.m.daocloud.io/library/nginx:1.28.3-alpine-slim@sha256:b33eedfdf089be1f83759ced27b4deec5b6f1b6fc2a6819ebce0ae351a4406e5`
- 上游来源：Docker Official Image / Nginx
- 上游许可说明：Docker Hub 官方页面记载 Nginx 采用 2-clause BSD-like license；镜像中仍可能包含受其他许可证约束的软件。
- 本仓库处理口径：本项目仅将其作为 ingress 运行时镜像引用，不主张镜像及其内含软件的著作权。

### 4. jsonschema

- 识别方式：`python/openclaw/control_plane/schema.py`
- 当前仓库使用方式：作为 Python 运行依赖，用于 JSON Schema 校验。
- 上游许可：MIT License
- 本仓库处理口径：不将该依赖纳入原创部分权利主张范围。

### 5. Playwright

- 识别方式：`python/openclaw/images/browser_runtime_checks.py`、`config/upstream/overlay_contract.json`
- 当前仓库使用方式：作为浏览器运行时检查与自动化依赖。
- 上游许可：Apache License 2.0
- 本仓库处理口径：不将该依赖纳入原创部分权利主张范围。

## 三、软著登记口径

申请软件著作权登记时，原创主张范围固定为：

- `python/` 下的原创控制平面代码；
- `scripts/` 下的原创运行、部署、检查与导出脚本；
- `config/` 下的原创配置真源与合同文件；
- `python/openclaw/runtime/` 与 `config/runtime/` 下的原创路径治理代码与清单；
- `deploy/` 下的原创编排与模板文件；
- `docs/` 下的原创说明文档；
- 其他明确由著作权人独立完成并已固定在仓库中的原创内容。

下列内容不纳入本项目原创著作权范围：

- OpenClaw 官方 Gateway 镜像及其上游源码；
- Python 与 Nginx 官方运行时镜像及镜像中内含软件；
- jsonschema、Playwright 及其他第三方库；
- 第三方名称、商标、Logo、图标、文案与品牌资产。

## 四、商业授权口径

本项目商业授权只覆盖著作权人对原创部分享有处分权的内容。第三方组件继续按其上游许可条款使用，商业授权文件中不得写成“连同第三方组件一并排他授权”。
