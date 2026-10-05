# targets.d

本目录承载 dispatch target 级别的补充变量；gateway 入口配置统一由 ingress 合同处理。

`.env.example` 模板由 one_click_config 根据当前 active control-plane profile 的 dispatch target registry 生成；生成文件是本地辅助输入，不进入仓库。target 专属 Webhook 与签名密钥填写在同名 `.env`，不要提交到仓库；若某个 env 键同时被扩展 deploy env schema 声明，填写入口是对应扩展 `extension.env`，统一部署输入工具会维护对应 target env。
`one_click_config.sh` 只接受 active profile 已声明的 `<target_id>.env` 与 registry 中的 env 键；切换 profile 前先移走非当前 profile 的 target env。
