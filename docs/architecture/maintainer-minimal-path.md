# 新维护者最小理解路径

本页用于在最短路径内理解项目运行面、扩展边界、配置流、升级流和验收流。读完本页后，再按具体任务进入架构、部署或运维专题页。

## 运行面

- 默认运行面是 `base + agent_platform`，只包含基座和平台扩展能力。
- `base` 负责控制面内核配置、镜像、ingress、internal API 与 scheduler 运行条件。
- 平台扩展负责 registry、runtime paths、object families、dispatch operations 与治理表面。
- 业务能力不进入基座默认运行面，必须由显式扩展 profile、有效自动发现 profile 或仓内合同 `--config-path` 接入。
- 外部入口统一经过 private HTTPS ingress，再转发到 Gateway 或 internal API。
- 运行证据由控制面容器生成和读取，不以宿主机 Python 作为正式执行面。

## 扩展边界

- 扩展目录固定为 `agent/extensions/<extension-id>/`，目录名、manifest、profile 和 Python 包必须同属一个 owner。
- 扩展对象的 `activation.enabledExtensionIds` 只声明自身 extension id，组合 profile 不改变对象归属。
- 业务扩展只能读写自身 runtime paths、workspace、outbox、evidence 和对象族。
- 跨扩展依赖必须通过 manifest required dependency 与公开 provider、CLI、registry 或 API 建立。
- 公共 provider 扩展只承载 transport/provider 能力，不承载业务状态、业务命令或业务验收语义。
- 基座只认识扩展机制和 manifest 合同，不引用具体业务扩展或公共 provider 扩展实现。

## 配置流

- 人工输入入口是 `deploy/site.env`、扩展 `agent/extensions/<extension-id>/deploy/extension.env` 和 `deploy/targets.d/*.env`。
- 配置键按 owner 路由，未知键、错位键和 schema 拒绝键由 env schema 阻断。
- `one_click_config.sh` 负责渲染部署 env、扩展 env、target env 与有效 compose。
- profile 选择来自 `OPENCLAW_CONTROL_PLANE_PROFILE` 或显式 `OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH`。
- service config 解析统一通过控制面配置解析面完成，脚本不复制 profile/path 推导逻辑。
- 凭据只进入部署机 owner-only env、runtime env 或容器环境，不写入仓库文档和测试快照。

## 升级流

- 正式服务/插件更新主链是 `scripts/setup/one_click_upgrade.sh`。
- 升级前先检查部署用户、Docker、控制面镜像、active profile、受保护路径和扩展声明。
- 源码同步保护 `deploy/.env`、`deploy/site.env`、`deploy/targets.d`、`state`、`logs`、证书和扩展 env。
- 同步后执行 stack verify、extension env ensure/verify、extensions doctor 和服务重建。
- 服务启动后执行 service status、internal API runtime、full test 和 runtime acceptance。
- 需要真实外部验收时，通过 `--require-live-verification <extension-id>` 显式启用扩展 live acceptance。

## 验收流

- 基座验收覆盖 registry validate、stack verify、host Python 治理、repo unittest 和 release gate。
- 扩展验收由扩展 testing manifest 声明，基座只调度命令并收集脱敏 evidence。
- full test 不触发真实外部发送，外部闭环必须由显式 live acceptance 开启。
- runtime acceptance 读取控制面证据、运行账本、服务健康和 dispatch 运行状态。
- 扩展冒烟测试按 extension id 单独执行，失败时只修对应 owner 的实现或合同。
- 任一门禁失败都先定位根因，修复后重跑相关完整门禁，再进入提交或部署。
