"""Antigravity safetySettings 兼容性控制。

本模块只负责最终请求边界上的发送决策：是否向 Google Antigravity / Cloud Code
发送 safetySettings。它不会改写 ``normalize_antigravity_request`` 生成的 category
列表，只应用配置的 threshold 和按模型排除规则。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence

from log import log

VALID_SAFETY_THRESHOLDS = {"OFF", "BLOCK_NONE"}
RULE_MODE_EXCLUDE_ALL = "exclude_all"
RULE_MODE_FILTER_CATEGORIES = "filter_categories"
VALID_RULE_MODES = {RULE_MODE_EXCLUDE_ALL, RULE_MODE_FILTER_CATEGORIES}


def normalize_model_id(model: Any) -> str:
    """规范化 Antigravity 模型 ID，用于规则精确匹配。"""
    return str(model or "").strip().lower()


def canonicalize_model_option(model: Any) -> str:
    """将 UI/本地别名统一为最终发送时使用的模型 ID。

    fetchAvailableModels 返回的模型通常已经是 canonical ID。这里仅移除本地功能前缀，
    并规范化模型列表中的 Claude 便捷别名；Gemini 的 provider route 映射仍由聊天转换器负责。
    """
    from src.utils import get_base_model_from_feature_model

    value = normalize_model_id(get_base_model_from_feature_model(str(model or "").strip()))
    if not value:
        return ""

    if "claude" in value:
        if "opus" in value:
            return "claude-opus-4-6-thinking"
        if "sonnet" in value:
            return "claude-sonnet-4-6"
        if "haiku" in value:
            return "gemini-2.5-flash"
        return "claude-sonnet-4-6"

    return value


def get_existing_safety_settings_for_model(model: Any) -> List[Dict[str, Any]]:
    """返回 normalizer 当前实际使用的 safetySettings 列表副本。"""
    from src.converter.antigravity_fix import DEFAULT_SAFETY_SETTINGS, LITE_SAFETY_SETTINGS

    canonical_model = normalize_model_id(model)
    source = (
        LITE_SAFETY_SETTINGS
        if "gemini-2.5-flash-lite" in canonical_model
        else DEFAULT_SAFETY_SETTINGS
    )
    return deepcopy(source)


def get_existing_safety_categories_for_model(model: Any) -> List[str]:
    """返回仓库现有按模型 safetySettings 列表中的 category 名称。"""
    return [
        str(item.get("category") or "").strip()
        for item in get_existing_safety_settings_for_model(model)
        if isinstance(item, dict) and str(item.get("category") or "").strip()
    ]


def get_all_existing_safety_categories() -> List[str]:
    """按稳定顺序返回 gcli2api 当前定义的全部 category。"""
    from src.converter.antigravity_fix import DEFAULT_SAFETY_SETTINGS, LITE_SAFETY_SETTINGS

    result: List[str] = []
    seen = set()
    for item in [*DEFAULT_SAFETY_SETTINGS, *LITE_SAFETY_SETTINGS]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        if category and category not in seen:
            seen.add(category)
            result.append(category)
    return result


def apply_threshold(
    settings: Sequence[Dict[str, Any]], threshold: str
) -> List[Dict[str, Any]]:
    """复制 settings，仅修改 threshold，不改变 category。"""
    normalized_threshold = str(threshold or "").strip().upper()
    if normalized_threshold not in VALID_SAFETY_THRESHOLDS:
        normalized_threshold = "BLOCK_NONE"

    result: List[Dict[str, Any]] = []
    for setting in settings:
        if not isinstance(setting, dict):
            continue
        item = deepcopy(setting)
        item["threshold"] = normalized_threshold
        result.append(item)
    return result


def find_model_rule(rules: Any, model: str) -> Optional[Dict[str, Any]]:
    """按 canonical model ID 精确查找规则，不做子串或通配符匹配。"""
    if not isinstance(rules, list):
        return None

    target = normalize_model_id(model)
    if not target:
        return None

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if normalize_model_id(rule.get("model")) == target:
            return rule
    return None


def apply_model_rule(
    settings: Sequence[Dict[str, Any]], rule: Optional[Dict[str, Any]]
) -> Optional[List[Dict[str, Any]]]:
    """应用单个模型规则。

    返回 ``None`` 表示应完全移除 safetySettings 字段。过滤后为空时也统一返回 ``None``，
    避免发送 ``safetySettings: []`` 来代替真正的字段省略。
    """
    if not rule:
        return [deepcopy(item) for item in settings if isinstance(item, dict)]

    mode = rule.get("mode")
    if mode == RULE_MODE_EXCLUDE_ALL:
        return None

    if mode != RULE_MODE_FILTER_CATEGORIES:
        return [deepcopy(item) for item in settings if isinstance(item, dict)]

    excluded = {
        str(category).strip()
        for category in (rule.get("excluded_categories") or [])
        if str(category).strip()
    }
    filtered = [
        deepcopy(item)
        for item in settings
        if isinstance(item, dict) and str(item.get("category") or "").strip() not in excluded
    ]
    return filtered or None


def validate_model_rules(
    rules: Any,
    allowed_categories: Iterable[str],
    *,
    max_rules: int = 200,
) -> List[Dict[str, Any]]:
    """校验并规范化需要持久化的模型规则数组。

    返回值可直接整体保存为唯一规则来源。重复模型会直接拒绝，避免 UI 删除/编辑后留下
    含糊的运行时状态；排除的 category 也会按该 canonical model 的现有 safetySettings
    列表校验，避免保存实际不会生效的规则。
    """
    if not isinstance(rules, list):
        raise ValueError("Antigravity safety model rules must be a list")
    if len(rules) > max_rules:
        raise ValueError(f"Antigravity safety model rules cannot exceed {max_rules} entries")

    allowed = {str(category).strip() for category in allowed_categories if str(category).strip()}
    seen_models = set()
    normalized: List[Dict[str, Any]] = []

    for index, raw_rule in enumerate(rules):
        if not isinstance(raw_rule, dict):
            raise ValueError(f"Safety model rule #{index + 1} must be an object")

        model = canonicalize_model_option(raw_rule.get("model"))
        if not model:
            raise ValueError(f"Safety model rule #{index + 1} has an empty model")
        if len(model) > 200:
            raise ValueError(f"Safety model rule #{index + 1} model is too long")
        if model in seen_models:
            raise ValueError(f"Duplicate Antigravity safety model rule: {model}")
        seen_models.add(model)

        mode = str(raw_rule.get("mode") or "").strip()
        if mode not in VALID_RULE_MODES:
            raise ValueError(f"Invalid safety model rule mode for {model}: {mode}")

        excluded_raw = raw_rule.get("excluded_categories") or []
        if not isinstance(excluded_raw, list):
            raise ValueError(f"excluded_categories for {model} must be a list")

        model_allowed = set(get_existing_safety_categories_for_model(model))
        excluded: List[str] = []
        excluded_seen = set()
        for raw_category in excluded_raw:
            category = str(raw_category or "").strip()
            if not category:
                continue
            if category not in allowed:
                raise ValueError(f"Unknown safety category for {model}: {category}")
            if category not in model_allowed:
                raise ValueError(f"Safety category is not used by {model}: {category}")
            if category not in excluded_seen:
                excluded_seen.add(category)
                excluded.append(category)

        if mode == RULE_MODE_EXCLUDE_ALL:
            excluded = []

        normalized.append(
            {
                "model": model,
                "mode": mode,
                "excluded_categories": excluded,
            }
        )

    return normalized


async def apply_antigravity_safety_config(inner: Dict[str, Any], model: str) -> None:
    """将 Antigravity safety 配置应用到最终 inner request。

    必须在模型规范化完成后、最终发送边界（当前为 ``wrap_cli_request``）调用，确保抗截断、
    假流式等本地变体和别名都使用同一个 canonical outbound model 规则。
    """
    from config import (
        get_antigravity_safety_model_rules,
        get_antigravity_safety_model_rules_enabled,
        get_antigravity_safety_settings_enabled,
        get_antigravity_safety_threshold,
    )

    canonical_model = normalize_model_id(model)

    if not await get_antigravity_safety_settings_enabled():
        inner.pop("safetySettings", None)
        log.debug(
            f"[ANTIGRAVITY SAFETY] model={canonical_model or model} action=omit reason=global_disabled"
        )
        return

    current_settings = inner.get("safetySettings")
    if not isinstance(current_settings, list) or not current_settings:
        # 对于现有 normalizer 没有生成 safetySettings 的请求路径（例如图片生成），
        # 不在这里额外构造 category 列表。
        inner.pop("safetySettings", None)
        log.debug(
            f"[ANTIGRAVITY SAFETY] model={canonical_model or model} action=omit reason=no_existing_settings"
        )
        return

    threshold = await get_antigravity_safety_threshold()
    effective_settings = apply_threshold(current_settings, threshold)

    matched_rule: Optional[Dict[str, Any]] = None
    if await get_antigravity_safety_model_rules_enabled():
        rules = await get_antigravity_safety_model_rules()
        matched_rule = find_model_rule(rules, canonical_model)
        effective_settings = apply_model_rule(effective_settings, matched_rule)

    if effective_settings:
        inner["safetySettings"] = effective_settings
        excluded_count = len(current_settings) - len(effective_settings)
        log.debug(
            f"[ANTIGRAVITY SAFETY] model={canonical_model or model} action=send "
            f"threshold={threshold} categories={len(effective_settings)} excluded={excluded_count}"
        )
    else:
        inner.pop("safetySettings", None)
        reason = (
            "model_exclude_all"
            if matched_rule and matched_rule.get("mode") == RULE_MODE_EXCLUDE_ALL
            else "all_categories_filtered"
        )
        log.debug(
            f"[ANTIGRAVITY SAFETY] model={canonical_model or model} action=omit reason={reason}"
        )
