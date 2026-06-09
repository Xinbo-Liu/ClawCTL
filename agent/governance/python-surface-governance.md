# Python 面治理

## 一、正式结论

### 1. `agent/extensions/<extension-id>/python/<python-package>/modules/` 是正式模块私有 Python 根目录

单模块私有 Python 逻辑统一进入：

```text
agent/extensions/<extension-id>/python/<python-package>/modules/<agent_ref>/
```

### 2. `agent/extensions/<extension-id>/python/<python-package>/domains/` 是扩展包共享领域 Python 根目录

凡是同一扩展包内跨模块复用的领域逻辑，都必须进入 `agent/extensions/<extension-id>/python/<python-package>/domains/<domain_ref>/`。

### 3. `python/openclaw/control_plane/` 是平台桥接与派生逻辑根目录

平台装配、registry、scheduler、runtime adapters 与 doctor 支撑固定落在主仓库 `python/openclaw/control_plane/`。

## 二、准入规则

### 1. 进入模块私有 Python 目录的条件

满足以下任一条件，即必须进入模块私有 Python 目录：

- 只服务于单个模块的业务实现；
- 只服务于单个模块的 CLI、orchestration 或 artifact 生产；
- 不应被其他模块共享。

### 2. 进入共享领域 Python 目录的条件

满足以下全部条件，才允许进入扩展包共享领域 Python 目录：

- 被同一扩展包内多个模块复用；
- 语义上属于同一 ownerDomain；
- 不需要提升为平台级通用能力。

## 三、禁止项

- 为单模块私有 Python 新建第二套并列根目录。
- 为领域共享 Python 建立根级 `python/openclaw/domains/`。
- 在 `agent/extensions/<extension-id>/agent/modules/<agent_ref>/` 或其他专题根目录下再落一份模块私有 Python 真源。

## 四、补充约束

1. 模块私有 Python 真源固定在 `agent/extensions/<extension-id>/python/<python-package>/modules/<agent_ref>/`，模块目录说明与 Python 入口必须保持一致。
2. 仓库不允许建立第二类模块 Python 真源目录。
3. 不得把模块 Python 真源下沉到 `agent/extensions/<extension-id>/agent/modules/<agent_ref>/python/` 或其他第二根目录。
