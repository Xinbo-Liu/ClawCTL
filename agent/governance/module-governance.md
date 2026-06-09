# 模块治理

## 一、模块结构

一个正式模块由扩展包内两部分构成：

1. `agent/extensions/<extension-id>/agent/modules/<module_ref>/`：模块主清单、局部说明、局部能力边界与薄启动入口。
2. `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/`：模块私有 Python 真源。

两部分共同构成一个模块，不允许为同一模块建立第三套并列真源。

## 二、模块必须承载的资产

### 1. `agent/extensions/<extension-id>/agent/modules/<module_ref>/`

| 资产                                              | 责任                                                                        |
|-------------------------------------------------|---------------------------------------------------------------------------|
| `module.json`                                   | 模块身份、ownerDomain、contract、operations、runtime、assembly 与 controlPlane 派生真源 |
| `README.md`                                     | 模块职责、输入输出、运行方式与依赖说明                                                       |
| `skills.md` / `permissions.json` / `tools.json` | 模块局部能力边界                                                                  |
| `bin/<module_ref>`                              | 薄启动入口；统一桥接到主仓库 runtime 入口                                                 |

### 2. `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/`

| 资产           | 责任                 |
|--------------|--------------------|
| `main.py`    | 模块主运行入口或 CLI 入口    |
| 其他 Python 文件 | 单模块私有编排、视图、校验与业务实现 |

### 3. `agent/extensions/<extension-id>/tests/`

| 资产                                   | 责任                             |
|--------------------------------------|--------------------------------|
| `tests/modules/<module_ref>/`        | 模块 smoke / regression 测试       |
| `tests/unit/` / `tests/regression/`  | 扩展级单元、回归与业务链路测试                |
| `tests/support/` / `tests/fixtures/` | 扩展测试私有辅助代码与 fixture，不进入主仓库测试真源 |

`tests/` 根目录和包含 Python 测试或辅助源码的直接子目录必须提供 `__init__.py` 字节码守卫；守卫必须禁写 bytecode 并清理本目录 `__pycache__`。这些 package marker 只服务测试导入与缓存治理，不改变 agent authoring 边界。

## 三、模块与领域 / 平台的关系

- 模块可以依赖 `agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/` 的共享领域代码。
- 模块薄启动器固定放在 `agent/extensions/<extension-id>/agent/modules/<module_ref>/bin/`，并桥接到模块私有 Python 或扩展包共享 Python domains。
- 模块由 `python/openclaw/control_plane/` 装配与调度。
- 模块不得把自身私有事实写回平台层或其他扩展包作为并列真源。

## 四、固定规则

1. 模块身份、类型、合同、operation 与 implementation 绑定统一由 `module.json` 定义。
2. 单模块私有 Python 只能进入 `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/`；shell / control-check 模块的薄启动器也只能停留在模块目录 `bin/`，实现逻辑进入模块私有 Python 或扩展包共享 Python domains。
3. 跨模块共享逻辑必须进入扩展包内领域共享层，不得通过读取其他模块目录形成隐式耦合。
4. 模块 README 必须能明确说明“做什么、不做什么、依赖什么、输出什么、如何运行”。
