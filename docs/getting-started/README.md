# getting-started 目录说明

`getting-started/` 提供正式部署主线、部署输入、宿主机准备和镜像准备入口。

项目基线统一由 [`../architecture/control-plane-baseline.md`](../architecture/control-plane-baseline.md) 定义，支持边界统一由 [`../architecture/supported-deployment-boundary.md`](../architecture/supported-deployment-boundary.md) 定义。

## 本页解决什么问题

- 首轮部署应该先看哪一页。
- 部署输入、宿主机准备、镜像准备分别去哪里看。
- 部署完成后下一步应该跳到哪里做统一验收。
- 哪些动作分别在目标机、访问端和交付工作机执行。

## 任务入口

- 唯一步骤正文：[`quickstart.md`](quickstart.md)
- 部署输入说明：[`deployment-inputs.md`](deployment-inputs.md)
- 宿主机与 private ingress 前提：[`environment-setup.md`](environment-setup.md)
- 在线 / 离线镜像准备：[`image-preparation.md`](image-preparation.md)

## 执行位置总览

- **目标机**：默认执行宿主机准备、配置生成、一键部署与大部分排查动作。
- **访问端**：只负责 private ingress 的 DNS / hosts 解析验证与浏览器访问。
- **交付工作机**：只在准备离线镜像归档或导出最终交付包时使用。

## 常用跳转

- 部署基线与装配原则：[`../architecture/control-plane-baseline.md`](../architecture/control-plane-baseline.md)
- 支持边界与受支持路径：[`../architecture/supported-deployment-boundary.md`](../architecture/supported-deployment-boundary.md)
- 阶段顺序、`--resume-from` 与统一部署主链：[`quickstart.md`](quickstart.md)
- host 控制面执行介质与宿主机 readiness：[`environment-setup.md`](environment-setup.md)
- 运行验收与证据归档：[`../operations/runtime-service-reference.md`](../operations/runtime-service-reference.md)
- 部署后排障入口：[`../operations/troubleshooting.md`](../operations/troubleshooting.md)

## 下一步

- 要直接跑部署主线：进入 [`quickstart.md`](quickstart.md)
- 要先补部署输入：进入 [`deployment-inputs.md`](deployment-inputs.md)
- 要先做宿主机准备：进入 [`environment-setup.md`](environment-setup.md)
- 要先做镜像准备：进入 [`image-preparation.md`](image-preparation.md)
- 部署完成后做统一验收：进入 [`../operations/runtime-service-reference.md`](../operations/runtime-service-reference.md)
