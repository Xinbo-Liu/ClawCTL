#!/usr/bin/env python3
"""模型成本策略、调用估算与预算闸门。"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any

from openclaw.lib.io.state import read_json_if_exists, with_lock_dir, write_json_atomic
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.time import format_datetime_in_app_tz


class ModelCostPolicyError(RuntimeError):
    """模型成本策略或预算闸门失败。"""


METERED_BILLING_MODES = {"pay_as_you_go"}
ZERO_RATE_BILLING_MODES = {"self_hosted", "not_applicable"}
SUPPORTED_BILLING_MODES = METERED_BILLING_MODES | ZERO_RATE_BILLING_MODES | {"subscription_quota"}
SUPPORTED_ENFORCEMENT_MODES = {"off", "audit", "hard"}
MONEY_QUANT = Decimal("0.000000001")


@dataclass(frozen=True)
class ModelCostEstimate:
    """一次模型调用的成本估算或实际用量成本。"""

    profile_ref: str
    billing_mode: str
    currency: str
    basis: str
    input_tokens: int
    output_tokens: int
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal
    pricing_source_kind: str
    pricing_source_url: str

    def to_audit_payload(self) -> dict[str, Any]:
        return {
            "profileRef": self.profile_ref,
            "billingMode": self.billing_mode,
            "currency": self.currency,
            "basis": self.basis,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "inputCost": _money_text(self.input_cost),
            "outputCost": _money_text(self.output_cost),
            "totalCost": _money_text(self.total_cost),
            "pricingSourceKind": self.pricing_source_kind,
            "pricingSourceUrl": self.pricing_source_url,
        }


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return token or "unknown"


def _model_runtime_state_dir() -> Path:
    raw = str(os.environ.get("OPENCLAW_MODEL_RUNTIME_STATE_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    state_root = str(os.environ.get("OPENCLAW_STATE_DIR") or "").strip()
    if state_root:
        return Path(state_root).resolve() / "model_runtime"
    return resolve_repo_root(Path(__file__)) / "state" / "openclaw" / "model_runtime"


def _cost_state_path(profile_ref: str, day: str) -> Path:
    return _model_runtime_state_dir() / "cost" / f"{_safe_token(profile_ref)}.{day}.json"


def _cost_lock_path(profile_ref: str, day: str) -> Path:
    return _model_runtime_state_dir() / "cost" / f".{_safe_token(profile_ref)}.{day}.lock"


def _today_text() -> str:
    return format_datetime_in_app_tz()[:10]


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _decimal(value: object, *, label: str, minimum: Decimal | None = None) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ModelCostPolicyError(f"{label} 必须为数字") from exc
    if minimum is not None and parsed < minimum:
        raise ModelCostPolicyError(f"{label} 不能小于 {minimum}")
    return parsed


def _positive_decimal(value: object, *, label: str) -> Decimal:
    parsed = _decimal(value, label=label, minimum=Decimal("0"))
    if parsed <= 0:
        raise ModelCostPolicyError(f"{label} 必须大于 0")
    return parsed


def _positive_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _money_text(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANT), "f")


def _profile_ref(profile_or_payload: Any) -> str:
    if isinstance(profile_or_payload, dict):
        return str(profile_or_payload.get("id") or profile_or_payload.get("profileRef") or "").strip()
    return str(getattr(profile_or_payload, "profile_id", "") or "").strip()


def _profile_capabilities(profile_or_payload: Any) -> dict[str, Any]:
    if isinstance(profile_or_payload, dict):
        return _dict(profile_or_payload.get("capabilities"))
    return _dict(getattr(profile_or_payload, "capabilities", {}))


def _profile_cost_policy(profile_or_payload: Any) -> dict[str, Any]:
    if isinstance(profile_or_payload, dict):
        return _dict(profile_or_payload.get("costPolicy"))
    return _dict(getattr(profile_or_payload, "cost_policy", {}))


def _pricing_source(policy: dict[str, Any]) -> dict[str, Any]:
    return _dict(policy.get("pricingSource"))


def _token_rates(policy: dict[str, Any]) -> dict[str, Any]:
    return _dict(policy.get("tokenRates"))


def _estimation(policy: dict[str, Any]) -> dict[str, Any]:
    return _dict(policy.get("estimation"))


def _budget(policy: dict[str, Any]) -> dict[str, Any]:
    return _dict(policy.get("budget"))


def _risk_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return _dict(policy.get("riskPolicy"))


def validate_model_cost_policy(profile_or_payload: Any) -> None:
    """校验模型成本策略语义，补足 JSON Schema 无法表达的跨字段约束。"""
    profile_ref = _profile_ref(profile_or_payload) or "unknown"
    policy = _profile_cost_policy(profile_or_payload)
    if not policy:
        raise ModelCostPolicyError(f"model {profile_ref} costPolicy 不能为空")

    billing_mode = str(policy.get("billingMode") or "").strip()
    if billing_mode not in SUPPORTED_BILLING_MODES:
        raise ModelCostPolicyError(f"model {profile_ref} costPolicy.billingMode 不受支持：{billing_mode}")
    currency = str(policy.get("currency") or "").strip()
    if not currency:
        raise ModelCostPolicyError(f"model {profile_ref} costPolicy.currency 不能为空")

    pricing = _pricing_source(policy)
    pricing_kind = str(pricing.get("kind") or "").strip()
    if not pricing_kind:
        raise ModelCostPolicyError(f"model {profile_ref} costPolicy.pricingSource.kind 不能为空")
    if billing_mode in METERED_BILLING_MODES:
        if not str(pricing.get("url") or "").strip():
            raise ModelCostPolicyError(f"model {profile_ref} 计量计费模型必须声明 costPolicy.pricingSource.url")
        if not str(pricing.get("checkedAt") or "").strip():
            raise ModelCostPolicyError(f"model {profile_ref} 计量计费模型必须声明 costPolicy.pricingSource.checkedAt")

    rates = _token_rates(policy)
    input_rate = _decimal(rates.get("inputPerMillionTokens"), label=f"model {profile_ref} inputPerMillionTokens", minimum=Decimal("0"))
    output_rate = _decimal(rates.get("outputPerMillionTokens"), label=f"model {profile_ref} outputPerMillionTokens", minimum=Decimal("0"))
    risk = _risk_policy(policy)
    allow_zero_rates = bool(risk.get("allowZeroRates"))
    if billing_mode in METERED_BILLING_MODES and (input_rate <= 0 or output_rate <= 0):
        raise ModelCostPolicyError(f"model {profile_ref} 计量计费模型不能使用 0 费率")
    if (input_rate <= 0 or output_rate <= 0) and not allow_zero_rates:
        raise ModelCostPolicyError(f"model {profile_ref} 使用 0 费率时必须显式 riskPolicy.allowZeroRates=true")
    if allow_zero_rates and billing_mode not in ZERO_RATE_BILLING_MODES:
        raise ModelCostPolicyError(f"model {profile_ref} 只有 self_hosted/not_applicable 才允许 0 费率")

    estimate = _estimation(policy)
    _positive_decimal(estimate.get("inputCharsPerToken"), label=f"model {profile_ref} estimation.inputCharsPerToken")
    _positive_decimal(estimate.get("outputCharsPerToken"), label=f"model {profile_ref} estimation.outputCharsPerToken")

    budget = _budget(policy)
    enforcement = str(budget.get("enforcement") or "").strip()
    if enforcement not in SUPPORTED_ENFORCEMENT_MODES:
        raise ModelCostPolicyError(f"model {profile_ref} costPolicy.budget.enforcement 不受支持：{enforcement}")
    if billing_mode in METERED_BILLING_MODES and enforcement == "off":
        raise ModelCostPolicyError(f"model {profile_ref} 计量计费模型不能关闭成本闸门")
    for key in ("maxEstimatedCostPerCall", "dailySoftLimit", "dailyHardLimit"):
        value = _decimal(budget.get(key), label=f"model {profile_ref} budget.{key}", minimum=Decimal("0"))
        if billing_mode in METERED_BILLING_MODES and key != "dailySoftLimit" and value <= 0:
            raise ModelCostPolicyError(f"model {profile_ref} 计量计费模型必须声明正数 budget.{key}")
    if _decimal(budget.get("dailyHardLimit"), label=f"model {profile_ref} budget.dailyHardLimit", minimum=Decimal("0")) < _decimal(
        budget.get("dailySoftLimit"),
        label=f"model {profile_ref} budget.dailySoftLimit",
        minimum=Decimal("0"),
    ):
        raise ModelCostPolicyError(f"model {profile_ref} budget.dailyHardLimit 不能小于 dailySoftLimit")
    for key in ("maxEstimatedInputTokensPerCall", "maxEstimatedOutputTokensPerCall"):
        if _positive_int(budget.get(key), 0) <= 0 and billing_mode in METERED_BILLING_MODES:
            raise ModelCostPolicyError(f"model {profile_ref} 计量计费模型必须声明正数 budget.{key}")


def _tokens_from_chars(chars: int, chars_per_token: Decimal) -> int:
    if chars <= 0:
        return 0
    value = (Decimal(chars) / chars_per_token).to_integral_value(rounding=ROUND_CEILING)
    return max(1, int(value))


def _rate_cost(tokens: int, rate_per_million: Decimal) -> Decimal:
    if tokens <= 0:
        return Decimal("0")
    return (Decimal(tokens) * rate_per_million) / Decimal("1000000")


def _build_cost_estimate(
    profile_or_payload: Any,
    *,
    basis: str,
    input_tokens: int,
    output_tokens: int,
) -> ModelCostEstimate:
    validate_model_cost_policy(profile_or_payload)
    policy = _profile_cost_policy(profile_or_payload)
    rates = _token_rates(policy)
    pricing = _pricing_source(policy)
    input_rate = _decimal(rates.get("inputPerMillionTokens"), label="inputPerMillionTokens", minimum=Decimal("0"))
    output_rate = _decimal(rates.get("outputPerMillionTokens"), label="outputPerMillionTokens", minimum=Decimal("0"))
    input_cost = _rate_cost(max(0, input_tokens), input_rate)
    output_cost = _rate_cost(max(0, output_tokens), output_rate)
    return ModelCostEstimate(
        profile_ref=_profile_ref(profile_or_payload) or "unknown",
        billing_mode=str(policy.get("billingMode") or "").strip(),
        currency=str(policy.get("currency") or "").strip(),
        basis=basis,
        input_tokens=max(0, input_tokens),
        output_tokens=max(0, output_tokens),
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
        pricing_source_kind=str(pricing.get("kind") or "").strip(),
        pricing_source_url=str(pricing.get("url") or "").strip(),
    )


def estimate_requested_model_call_cost(
    profile_or_payload: Any,
    *,
    prompt: str,
    system_prompt: str | None,
    max_tokens: int | None,
) -> ModelCostEstimate:
    """按请求上界估算单次调用成本，用于调用前预算闸门。"""
    validate_model_cost_policy(profile_or_payload)
    policy = _profile_cost_policy(profile_or_payload)
    estimate = _estimation(policy)
    budget = _budget(policy)
    capabilities = _profile_capabilities(profile_or_payload)
    chars_per_token = _positive_decimal(estimate.get("inputCharsPerToken"), label="estimation.inputCharsPerToken")
    input_chars = len(str(prompt or "")) + len(str(system_prompt or ""))
    input_tokens = _tokens_from_chars(input_chars, chars_per_token)
    output_tokens = _positive_int(max_tokens, 0)
    if output_tokens <= 0:
        output_tokens = _positive_int(budget.get("maxEstimatedOutputTokensPerCall"), 0)
    if output_tokens <= 0:
        output_tokens = _positive_int(capabilities.get("maxTokens"), 1024)
    return _build_cost_estimate(
        profile_or_payload,
        basis="requested_upper_bound",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _usage_token(payload: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        value = payload.get(name)
        parsed = _positive_int(value, 0)
        if parsed > 0:
            return parsed
    return 0


def _response_usage_tokens(raw_response: dict[str, Any]) -> tuple[int, int]:
    usage = raw_response.get("usage") if isinstance(raw_response.get("usage"), dict) else {}
    input_tokens = _usage_token(usage, ("input_tokens", "prompt_tokens", "prompt_eval_count"))
    output_tokens = _usage_token(usage, ("output_tokens", "completion_tokens", "eval_count"))
    if input_tokens or output_tokens:
        return input_tokens, output_tokens
    return _usage_token(raw_response, ("prompt_eval_count",)), _usage_token(raw_response, ("eval_count",))


def estimate_model_response_cost(
    profile_or_payload: Any,
    *,
    prompt: str,
    system_prompt: str | None,
    output_text: str,
    raw_response: dict[str, Any],
) -> ModelCostEstimate:
    """按 provider usage 记录实际成本；缺失 usage 时退回字符估算并在 basis 中标明。"""
    policy = _profile_cost_policy(profile_or_payload)
    input_tokens, output_tokens = _response_usage_tokens(raw_response if isinstance(raw_response, dict) else {})
    if input_tokens or output_tokens:
        return _build_cost_estimate(
            profile_or_payload,
            basis="provider_usage",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    risk = _risk_policy(policy)
    if not bool(risk.get("allowEstimatedUsage")):
        raise ModelCostPolicyError(f"model {_profile_ref(profile_or_payload)} 响应缺少 usage，且未允许估算用量")
    estimate = _estimation(policy)
    input_chars_per_token = _positive_decimal(estimate.get("inputCharsPerToken"), label="estimation.inputCharsPerToken")
    output_chars_per_token = _positive_decimal(estimate.get("outputCharsPerToken"), label="estimation.outputCharsPerToken")
    input_tokens = _tokens_from_chars(len(str(prompt or "")) + len(str(system_prompt or "")), input_chars_per_token)
    output_tokens = _tokens_from_chars(len(str(output_text or "")), output_chars_per_token)
    return _build_cost_estimate(
        profile_or_payload,
        basis="estimated_response_chars",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def enforce_model_cost_budget(profile_or_payload: Any, estimate: ModelCostEstimate) -> None:
    """按 profile 成本策略执行调用前预算闸门。"""
    validate_model_cost_policy(profile_or_payload)
    policy = _profile_cost_policy(profile_or_payload)
    budget = _budget(policy)
    enforcement = str(budget.get("enforcement") or "").strip()
    if enforcement in {"off", "audit"}:
        return
    profile_ref = estimate.profile_ref
    max_input = _positive_int(budget.get("maxEstimatedInputTokensPerCall"), 0)
    max_output = _positive_int(budget.get("maxEstimatedOutputTokensPerCall"), 0)
    if max_input > 0 and estimate.input_tokens > max_input:
        raise ModelCostPolicyError(f"模型调用输入 token 估算超出预算：profile={profile_ref} {estimate.input_tokens}>{max_input}")
    if max_output > 0 and estimate.output_tokens > max_output:
        raise ModelCostPolicyError(f"模型调用输出 token 上界超出预算：profile={profile_ref} {estimate.output_tokens}>{max_output}")
    per_call_limit = _decimal(budget.get("maxEstimatedCostPerCall"), label=f"model {profile_ref} budget.maxEstimatedCostPerCall", minimum=Decimal("0"))
    if per_call_limit > 0 and estimate.total_cost > per_call_limit:
        raise ModelCostPolicyError(
            f"模型调用成本估算超出单次预算：profile={profile_ref} "
            f"estimate={_money_text(estimate.total_cost)} {estimate.currency} limit={_money_text(per_call_limit)}"
        )
    daily_hard = _decimal(budget.get("dailyHardLimit"), label=f"model {profile_ref} budget.dailyHardLimit", minimum=Decimal("0"))
    if daily_hard <= 0:
        return
    day = _today_text()
    state_path = _cost_state_path(profile_ref, day)
    with with_lock_dir(_cost_lock_path(profile_ref, day)):
        payload = read_json_if_exists(state_path, default={})
        accrued = _decimal(_dict(payload).get("actualCostTotal") or "0", label=f"model {profile_ref} daily actual cost", minimum=Decimal("0"))
        if accrued + estimate.total_cost > daily_hard and enforcement == "hard":
            raise ModelCostPolicyError(
                f"模型调用成本估算超出每日硬预算：profile={profile_ref} "
                f"accrued={_money_text(accrued)} estimate={_money_text(estimate.total_cost)} "
                f"limit={_money_text(daily_hard)} {estimate.currency}"
            )


def record_model_call_cost(profile_or_payload: Any, actual: ModelCostEstimate, *, status: str) -> None:
    """把调用成本计入 profile 当日状态文件。"""
    policy = _profile_cost_policy(profile_or_payload)
    budget = _budget(policy)
    if str(budget.get("enforcement") or "").strip() == "off" and actual.total_cost <= 0:
        return
    profile_ref = actual.profile_ref
    day = _today_text()
    state_path = _cost_state_path(profile_ref, day)
    with with_lock_dir(_cost_lock_path(profile_ref, day)):
        payload = _dict(read_json_if_exists(state_path, default={}))
        actual_total = _decimal(payload.get("actualCostTotal") or "0", label=f"model {profile_ref} actualCostTotal", minimum=Decimal("0"))
        input_tokens = _positive_int(payload.get("inputTokens"), 0) + actual.input_tokens
        output_tokens = _positive_int(payload.get("outputTokens"), 0) + actual.output_tokens
        call_count = _positive_int(payload.get("callCount"), 0) + 1
        error_count = _positive_int(payload.get("errorCount"), 0) + (0 if status == "ok" else 1)
        daily_soft = _decimal(budget.get("dailySoftLimit") or "0", label=f"model {profile_ref} budget.dailySoftLimit", minimum=Decimal("0"))
        daily_hard = _decimal(budget.get("dailyHardLimit") or "0", label=f"model {profile_ref} budget.dailyHardLimit", minimum=Decimal("0"))
        next_total = actual_total + actual.total_cost
        write_json_atomic(
            state_path,
            {
                "schemaVersion": 1,
                "profileRef": profile_ref,
                "date": day,
                "currency": actual.currency,
                "actualCostTotal": _money_text(next_total),
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "callCount": call_count,
                "errorCount": error_count,
                "dailySoftLimit": _money_text(daily_soft),
                "dailyHardLimit": _money_text(daily_hard),
                "softLimitExceeded": bool(daily_soft > 0 and next_total > daily_soft),
                "lastStatus": status,
                "lastBasis": actual.basis,
                "lastCallId": uuid.uuid4().hex,
                "updatedAt": format_datetime_in_app_tz(),
            },
        )
