# base kernel models

该目录是 base kernel 的空 models collection。零 extension 模式下允许目录存在但不提供任何模型；业务模型只能通过 profile + extension 显式装配进入。

## 模型通道基线

模型 profile 的执行入口以 `channel` 为唯一真源：

- `channel.kind=http`：用于 OpenAI compatible、Anthropic Messages、Ollama 等 HTTP 服务。
- `channel.api=openai-chat-completions`：调用 `/v1/chat/completions`。
- `channel.api=anthropic-messages`：调用 `/v1/messages`。
- `channel.api=ollama-chat`：调用 Ollama `/api/chat`，通常 `auth.required=false`。
- `channel.kind=local_process` + `channel.api=local-process-json`：通过本地命令 stdin/stdout 调用传统本地推理进程。

HTTP profile 必须声明 `baseUrlEnv`；生产外部 provider 应声明 `auth.required=true` 与 `apiKeyEnv`。本地进程 profile 可以直接声明 `localProcess.command`，也可以声明 `localProcess.commandEnv`，由部署输入提供具体命令。

模型调用统一经过 `openclaw.lib.models.generate_text`，该入口负责成本策略校验、调用成本估算、预算闸门、并发闸门、RPM 闸门、脱敏审计、HTTP/本地进程协议分发和输出文本抽取。业务 agent 不应直接拼 provider URL、自行读取 API key 或绕过成本治理。

## 成本策略基线

每个 model profile 必须声明分层 `costPolicy`：

- `billingMode` 与 `currency`：说明计费模式与币种。
- `pricingSource`：说明价格来源、URL 与核验日期；计量计费模型必须指向正式价格来源。
- `tokenRates`：维护输入、输出与可选 prompt cache 的每百万 token 费率。
- `estimation`：维护调用前预算估算使用的字符/token 假设。
- `budget`：维护单次调用、每日软限与每日硬限；计量计费模型不能关闭成本闸门。
- `riskPolicy`：显式声明是否允许 0 费率和 usage 缺失时的估算回退。

计量计费模型的输入/输出费率不能填 0。测试 fixture、本地自托管或不适用真实计费的 profile 只有在 `billingMode` 明确为 `self_hosted` 或 `not_applicable`，并声明 `riskPolicy.allowZeroRates=true` 时才允许 0 费率。

## 示例

Ollama HTTP：

```json
{
  "provider": "ollama",
  "modelRef": "ollama/__REQUIRED_MODEL__",
  "modelRefEnv": "OLLAMA_MODEL_REF",
  "channel": {
    "kind": "http",
    "api": "ollama-chat",
    "baseUrlEnv": "OLLAMA_BASE_URL",
    "auth": {
      "kind": "none",
      "required": false
    }
  }
}
```

本地进程：

```json
{
  "provider": "local_llm",
  "modelRef": "local/__REQUIRED_MODEL__",
  "channel": {
    "kind": "local_process",
    "api": "local-process-json",
    "localProcess": {
      "commandEnv": "LOCAL_MODEL_COMMAND"
    }
  }
}
```
