# Agent 模块治理

## 固定边界

- 主仓库 formal module 通过仓内 extension 提供。
- formal module 只能落在 `agent/extensions/<extension-id>/agent/modules/<module_ref>/` 与 `agent/extensions/<extension-id>/python/<python-package>/modules/<module_ref>/`。
- 模块运行视图从 `module.json` 派生，不在 README、job JSON 或脚本帮助中维护并列快照。
- 仓库内正式面不包含默认业务模块包。

## 模块最小合同

- `module.json`
- `README.md`
- `skills.md`
- `permissions.json`
- `tools.json`
- `bin/`
- `tests/test_smoke.py`

## 禁止事项

1. 在主仓库正式面中引入脱离 extension 包边界的业务模块，或把扩展包模块复制到 `agent/control_plane/` / `python/openclaw/` 等共享根。
2. 在模块 README 中加入脱离扩展包真实路径的非正式根目录引用。
3. 在 `agent/control_plane/jobs/*.json` 中重新声明本该从 `module.json` 派生的合同字段。
