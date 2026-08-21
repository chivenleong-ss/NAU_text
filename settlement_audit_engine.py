"""Deterministic audit calculations and lightweight document normalization.

The demo deliberately keeps numeric conclusions in code. A language model can
later help with semantic extraction and explanations, but it must not decide
quantities, rates, amounts, or the final reduction amount.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS = {
    "quantity_deviation_pct": 0.05,
    "unit_price_deviation_pct": 0.10,
    "market_deviation_pct": 0.10,
    "duplicate_overlap_pct": 0.95,
}


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("，", "").replace("¥", "").replace("元", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else default


def _money(value: float) -> int:
    return int(round(value))


def _pct(value: float, reference: float) -> float | None:
    if abs(reference) < 1e-9:
        return None
    return round((value - reference) / reference, 4)


def _amount(record: dict[str, Any]) -> float:
    if "amount" in record:
        return _number(record["amount"])
    return _number(record.get("quantity")) * _number(record.get("unit_price"))


def _risk(level: str) -> str:
    return {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(level, level)


def compare_three_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare bid, contract, change, actual and settlement facts per item."""
    rows: list[dict[str, Any]] = []
    for item in data.get("line_items", []):
        bid = item.get("bid", {})
        contract = item.get("contract", {})
        change = item.get("change", {})
        actual = item.get("actual", {})
        settlement = item.get("settlement", {})
        contract_qty = _number(contract.get("quantity"))
        actual_qty = _number(actual.get("quantity"))
        settlement_qty = _number(settlement.get("quantity"))
        contract_price = _number(contract.get("unit_price"))
        settlement_price = _number(settlement.get("unit_price"))
        market_price = _number(item.get("market_unit_price"))
        rows.append({
            "item_code": item.get("item_code", ""),
            "name": item.get("name", ""),
            "unit": item.get("unit", ""),
            "location": item.get("location", ""),
            "bid": {"quantity": _number(bid.get("quantity")), "unit_price": _number(bid.get("unit_price")), "amount": _money(_amount(bid))},
            "contract": {"quantity": contract_qty, "unit_price": contract_price, "amount": _money(_amount(contract))},
            "change": {"quantity": _number(change.get("quantity")), "unit_price": _number(change.get("unit_price")), "amount": _money(_amount(change)), "id": change.get("id", ""), "approved": change.get("approved", True)},
            "actual": {"quantity": actual_qty, "unit_price": _number(actual.get("unit_price")), "amount": _money(_amount(actual))},
            "settlement": {"quantity": settlement_qty, "unit_price": settlement_price, "amount": _money(_amount(settlement))},
            "market_unit_price": market_price or None,
            "quantity_deviation_settlement_vs_actual": _pct(settlement_qty, actual_qty),
            "quantity_deviation_settlement_vs_contract": _pct(settlement_qty, contract_qty),
            "unit_price_deviation_settlement_vs_contract": _pct(settlement_price, contract_price),
            "unit_price_deviation_settlement_vs_market": _pct(settlement_price, market_price),
            "evidence": deepcopy(item.get("evidence", [])),
        })
    return rows


def _issue(issue_id: str, phase: str, category: str, title: str, level: str,
           amount: float, rule: str, judgement: str, suggestion: str,
           item: dict[str, Any], status: str = "待主审复核") -> dict[str, Any]:
    return {
        "id": issue_id,
        "phase": phase,
        "category": category,
        "title": title,
        "risk": _risk(level),
        "amount": _money(amount),
        "rule": rule,
        "judgement": judgement,
        "suggestion": suggestion,
        "item_code": item.get("item_code", ""),
        "location": item.get("location", ""),
        "source_objects": [x.get("document", "") for x in item.get("evidence", [])],
        "evidence": deepcopy(item.get("evidence", [])),
        "expert_status": "待分派",
        "chief_status": status,
    }


def detect_issues(data: dict[str, Any], comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(data.get("meta", {}).get("thresholds", {}))
    items = {x.get("item_code"): x for x in data.get("line_items", [])}
    rows = {x.get("item_code"): x for x in comparisons}
    issues: list[dict[str, Any]] = []

    dirt = items.get("010101001")
    if dirt:
        change = dirt.get("change", {})
        contract_qty = _number(dirt.get("contract", {}).get("quantity"))
        change_qty = _number(change.get("quantity"))
        overlap = min(contract_qty, change_qty) / contract_qty if contract_qty else 0
        if change.get("relation") == "overlap" and overlap >= thresholds["duplicate_overlap_pct"]:
            issues.append(_issue("post-quantity-001", "事后", "工程量", "土方开挖重复计费", "high", change_qty * _number(dirt.get("contract", {}).get("unit_price")),
                f"同一清单编码、部位的合同与变更工程量重合率 {overlap:.0%} >= {thresholds['duplicate_overlap_pct']:.0%}",
                "结算书与 BG-018 均计取地下车库土方开挖，合同清单扣减关系尚未证明，存在重复计价。",
                "补充变更台账、现场收方记录和合同清单扣减记录；在核验前暂按重合工程量控制结算。", dirt))

    change_item = items.get("ZQ-023")
    if change_item and not change_item.get("change", {}).get("approved", True):
        change = change_item.get("change", {})
        issues.append(_issue("post-change-001", "事中", "变更签证", "变更 ZQ-023 审批链不完整", "high", _amount(change),
            "结算引用的变更记录 approval=false，且审批链缺少合同约定岗位",
            "ZQ-023 已进入结算申报，但项目商务经理签认缺失，变更计价效力存在程序性风险。",
            "冻结该项支付，补齐授权审批或转为现场核验事项。", change_item))

    temp = items.get("040801001")
    if temp and _number(temp.get("actual", {}).get("quantity")) == 0 and _number(temp.get("settlement", {}).get("quantity")) > 0:
        issues.append(_issue("post-actual-001", "事后", "实际完成量", "临时设施虚列子目", "high", _amount(temp.get("settlement", {})),
            "竣工实际完成量 = 0，结算工程量 > 0，且未找到移交证据",
            "结算书申报临时设施，但竣工移交资料未见实体移交记录，真实性不足。",
            "补充实体移交、拆除或现场核验记录；证据不足时不予计入结算。", temp))

    concrete = rows.get("010501002")
    concrete_item = items.get("010501002")
    if concrete and concrete_item and abs(concrete.get("unit_price_deviation_settlement_vs_contract") or 0) > thresholds["unit_price_deviation_pct"]:
        delta = (concrete["settlement"]["unit_price"] - concrete["contract"]["unit_price"]) * concrete["settlement"]["quantity"]
        issues.append(_issue("post-price-001", "事后", "单价", "混凝土结算单价偏离合同", "medium", delta,
            f"结算单价较合同单价偏离 {abs(concrete['unit_price_deviation_settlement_vs_contract']):.2%} > {thresholds['unit_price_deviation_pct']:.0%}",
            "结算单价高于合同约定，且未在当前资料包中发现完整调价依据。",
            "核对调价条款、审批单、供应商报价和同期市场参考价后再认定。", concrete_item))

    steel = items.get("010515001")
    leakage = steel.get("leakage", {}) if steel else {}
    if leakage.get("kind") == "material_loss":
        base = _number(leakage.get("base_quantity"))
        recorded = _number(leakage.get("recorded_consumption"))
        target = _number(leakage.get("target_loss_rate"))
        loss_rate = (recorded - base) / base if base else 0
        excess_amount = max(0, recorded - base * (1 + target)) * _number(leakage.get("unit_price"))
        if loss_rate > target:
            issues.append(_issue("post-leakage-001", "事后", "跑冒滴漏", "钢筋材料损耗率异常", "medium", excess_amount,
                f"实际损耗率 {loss_rate:.2%} > 目标损耗率 {target:.2%}",
                "材料台账领用量超过理论消耗与目标损耗允许范围，超额部分缺少专项说明。",
                "勾稽领用、退库、盘点和施工日志，补充损耗审批或核减超额材料成本。", steel))

    machine = items.get("021001004")
    machine_leakage = machine.get("leakage", {}) if machine else {}
    if machine_leakage.get("kind") == "machine_mismatch":
        unmatched = max(0, _number(machine_leakage.get("claimed_hours")) - _number(machine_leakage.get("matched_hours")))
        amount = unmatched / 8 * _number(machine_leakage.get("unit_price"))
        if unmatched:
            issues.append(_issue("post-leakage-002", "事后", "跑冒滴漏", "挖掘机台班与施工日志不匹配", "medium", amount,
                f"申报台班中有 {unmatched:.0f} 小时缺少施工日志匹配记录",
                "机械台班结算记录与施工日志存在连续缺口，无法证明对应设备实际作业。",
                "补充设备进退场、燃油、作业量和施工日志；证据不足部分暂不计量。", machine))

    if not data.get("meta", {}).get("data_source"):
        return issues

    existing_ids = {issue["id"] for issue in issues}
    for row in comparisons:
        item = items.get(row.get("item_code"), {"evidence": row.get("evidence", [])})
        code = row.get("item_code", "")
        title_name = row.get("name", code)
        settlement_amount = row.get("settlement", {}).get("amount", 0)
        actual_amount = row.get("actual", {}).get("amount", 0)
        quantity_deviation = row.get("quantity_deviation_settlement_vs_actual")
        if (
            quantity_deviation is not None
            and abs(quantity_deviation) > thresholds["quantity_deviation_pct"]
            and f"generic-quantity-{code}" not in existing_ids
        ):
            excess = max(0, settlement_amount - actual_amount)
            issues.append(_issue(
                f"generic-quantity-{code}",
                "事后",
                "工程量",
                f"{title_name}结算工程量偏离实际完成量",
                "high" if abs(quantity_deviation) >= 0.2 else "medium",
                excess,
                f"结算工程量较实际完成量偏离 {abs(quantity_deviation):.2%} > {thresholds['quantity_deviation_pct']:.0%}",
                "上传资料中的结算工程量与实际完成工程量不一致，超过阈值。",
                "核对竣工验收、现场收方和结算申报明细，按实际完成量复核。",
                item,
            ))
        if (
            row.get("actual", {}).get("quantity") == 0
            and row.get("settlement", {}).get("quantity", 0) > 0
            and f"generic-empty-actual-{code}" not in existing_ids
        ):
            issues.append(_issue(
                f"generic-empty-actual-{code}",
                "事后",
                "实际完成量",
                f"{title_name}结算有量但实际完成量为零",
                "high",
                settlement_amount,
                "实际完成工程量 = 0，结算工程量 > 0",
                "上传资料显示该子目未形成实际完成量，但已进入结算申报。",
                "补充验收或移交证据；证据不足时不予计入结算。",
                item,
            ))
        price_deviation = row.get("unit_price_deviation_settlement_vs_contract")
        if (
            price_deviation is not None
            and abs(price_deviation) > thresholds["unit_price_deviation_pct"]
            and f"generic-price-{code}" not in existing_ids
        ):
            quantity = row.get("settlement", {}).get("quantity", 0)
            contract_price = row.get("contract", {}).get("unit_price", 0)
            settlement_price = row.get("settlement", {}).get("unit_price", 0)
            issues.append(_issue(
                f"generic-price-{code}",
                "事后",
                "单价",
                f"{title_name}结算单价偏离合同",
                "medium",
                max(0, settlement_price - contract_price) * quantity,
                f"结算单价较合同单价偏离 {abs(price_deviation):.2%} > {thresholds['unit_price_deviation_pct']:.0%}",
                "上传资料中的结算单价偏离合同约定单价，超过规则阈值。",
                "核对调价条款、审批记录和市场参考价后再确认。",
                item,
            ))

    return issues


def _component_item(record: dict[str, Any], code: str, location: str) -> dict[str, Any]:
    return {
        "item_code": code,
        "location": location,
        "evidence": deepcopy(record.get("evidence", [])),
    }


def aggregate_cost_components(data: dict[str, Any]) -> dict[str, Any]:
    """Reconcile contract, changes, claims, rewards, penalties and fees."""
    project = data.get("project", {})
    financials = project.get("financials", {})
    components = project.get("cost_components", {})
    claims = components.get("claims", [])
    rewards_penalties = components.get("rewards_penalties", [])
    fee = components.get("management_fee", {})

    claims_requested = sum(_number(x.get("requested_amount")) for x in claims)
    claims_approved = sum(_number(x.get("approved_amount")) for x in claims)
    claims_settlement = sum(_number(x.get("settlement_amount")) for x in claims)
    reward_amount = sum(_number(x.get("amount")) for x in rewards_penalties if x.get("type") == "奖励")
    penalty_amount = sum(_number(x.get("amount")) for x in rewards_penalties if x.get("type") == "罚款")
    penalty_deducted = sum(_number(x.get("deducted_from_settlement")) for x in rewards_penalties if x.get("type") == "罚款")
    contract_fee = _number(fee.get("contract_base")) * _number(fee.get("contract_rate"))
    settlement_fee = _number(fee.get("settlement_base")) * _number(fee.get("settlement_rate"))
    approved_fee = _number(fee.get("settlement_base")) * _number(fee.get("approved_rate", fee.get("contract_rate")))

    return {
        "contract_total": _money(_number(financials.get("contract_total"))),
        "approved_change_total": _money(_number(financials.get("approved_change_total"))),
        "claims_requested": _money(claims_requested),
        "claims_approved": _money(claims_approved),
        "claims_settlement": _money(claims_settlement),
        "unapproved_claim_amount": _money(max(0, claims_settlement - claims_approved)),
        "reward_amount": _money(reward_amount),
        "penalty_amount": _money(penalty_amount),
        "penalty_deducted": _money(penalty_deducted),
        "penalty_omission": _money(max(0, penalty_amount - penalty_deducted)),
        "management_fee_contract": _money(contract_fee),
        "management_fee_settlement": _money(settlement_fee),
        "management_fee_approved": _money(approved_fee),
        "management_fee_excess": _money(max(0, settlement_fee - approved_fee)),
        "settlement_total": _money(_number(financials.get("settlement_total"))),
    }


def detect_cost_component_issues(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect unsupported claims, reward/penalty omissions and fee drift."""
    project = data.get("project", {})
    components = project.get("cost_components", {})
    issues: list[dict[str, Any]] = []

    for claim in components.get("claims", []):
        settlement_amount = _number(claim.get("settlement_amount"))
        approved_amount = _number(claim.get("approved_amount"))
        unsupported = max(0, settlement_amount - approved_amount)
        if unsupported <= 0:
            continue
        reference = claim.get("reference_change_id")
        relation = f"，且与变更 {reference} 存在费用重叠" if reference else ""
        issues.append(_issue(
            f"post-claim-{claim.get('id', 'unknown')}",
            "事后",
            "索赔",
            claim.get("title", "索赔金额异常"),
            "high" if reference else "medium",
            unsupported,
            f"结算索赔金额 {settlement_amount:,.0f} 元 > 已批准金额 {approved_amount:,.0f} 元{relation}",
            f"索赔已进入结算，但批准金额不足，未批准部分为 {unsupported:,.0f} 元{relation}。",
            "补充责任划分、工期影响、费用测算和正式审批；未批准部分不得直接计入最终结算。",
            _component_item(claim, claim.get("id", "CLAIM"), "索赔资料"),
        ))

    for item in components.get("rewards_penalties", []):
        amount = _number(item.get("amount"))
        if item.get("type") == "奖励" and not item.get("approved", False) and amount > 0:
            issues.append(_issue(
                f"post-reward-{item.get('id', 'unknown')}",
                "事后",
                "奖罚款",
                item.get("title", "奖励款审批缺失"),
                "medium",
                amount,
                "奖励款 approved=false 但已进入结算",
                "结算书计入奖励款，但未见合同约定奖励确认单或审批记录。",
                "补充奖励条件达成证明和审批单；证据不足部分不予计入。",
                _component_item(item, item.get("id", "REWARD"), "奖罚款资料"),
            ))
        if item.get("type") == "罚款":
            deducted = _number(item.get("deducted_from_settlement"))
            omission = max(0, amount - deducted)
            if item.get("approved", False) and omission > 0:
                issues.append(_issue(
                    f"post-penalty-{item.get('id', 'unknown')}",
                    "事后",
                    "奖罚款",
                    item.get("title", "已确认罚款未扣减"),
                    "high",
                    omission,
                    f"已批准罚款 {amount:,.0f} 元，结算仅扣减 {deducted:,.0f} 元",
                    "已生效的质量处罚未在结算中足额扣减，存在应扣未扣风险。",
                    "将处罚单与结算支付、奖罚台账勾稽，补扣未扣金额。",
                    _component_item(item, item.get("id", "PENALTY"), "奖罚款资料"),
                ))

    fee = components.get("management_fee", {})
    settlement_rate = _number(fee.get("settlement_rate"))
    approved_rate = _number(fee.get("approved_rate", fee.get("contract_rate")))
    excess = max(0, _number(fee.get("settlement_base")) * (settlement_rate - approved_rate))
    if excess > 0:
        fee_item = {"evidence": fee.get("evidence", [])}
        issues.append(_issue(
            "post-fee-001",
            "事后",
            "取费",
            "管理费取费费率偏离合同",
            "medium",
            excess,
            f"结算管理费率 {settlement_rate:.2%} > 合同/批准费率 {approved_rate:.2%}",
            "结算按高于合同约定的费率计取管理费，超额部分缺少有效批准依据。",
            "核对合同取费条款、批准费率和计费基数，核减未获批准的超额管理费。",
            _component_item(fee_item, "MANAGEMENT-FEE", "取费资料"),
        ))
    return issues


def score_fraud_risk(data: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Score historical false-settlement patterns with deterministic features."""
    patterns = {x.get("id"): x for x in data.get("historical_patterns", [])}
    hits: list[dict[str, Any]] = []
    titles = " ".join(x.get("title", "") for x in issues)
    categories = {x.get("category") for x in issues}
    checks = [
        ("duplicate_billing", "工程量" in categories and "重复计费" in titles, "发现同编码同部位重复计价疑点"),
        ("unsupported_item", "虚列子目" in titles, "发现结算申报与实际完成量不一致"),
        ("unreasonable_claim", "索赔" in categories, "发现未批准或部分批准索赔进入结算"),
        ("unapproved_change", "变更签证" in categories, "发现审批链未闭合的变更进入结算"),
        ("rate_drift", "单价" in categories or "取费" in categories, "发现单价或取费费率偏离合同"),
    ]
    score = 0
    for pattern_id, matched, evidence in checks:
        if not matched:
            continue
        pattern = patterns.get(pattern_id, {})
        weight = _number(pattern.get("weight"))
        score += int(weight)
        hits.append({"id": pattern_id, "name": pattern.get("name", pattern_id), "weight": int(weight), "evidence": evidence})
    score = min(100, score)
    level = "高风险" if score >= 60 else "中风险" if score >= 30 else "低风险"
    return {
        "score": score,
        "level": level,
        "model": "历史虚假结算特征规则模型",
        "hits": hits,
        "explanation": "；".join(x["evidence"] for x in hits) if hits else "当前资料未命中已配置的历史虚假结算特征。",
    }


def train_false_settlement_model(data: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a lightweight supervised model from historical false-settlement cases."""
    patterns = {item.get("id"): item for item in data.get("historical_patterns", [])}
    cases = data.get("historical_cases", [])
    positive_cases = [case for case in cases if case.get("label") in {"虚假结算", "疑似虚假结算"} or case.get("is_false")]
    negative_cases = [case for case in cases if case not in positive_cases]
    positive_base = max(1, len(positive_cases))
    learned_features = []
    issue_text = " ".join(f"{item.get('category', '')} {item.get('title', '')}" for item in issues)

    for pattern_id, pattern in patterns.items():
        positive_hits = sum(1 for case in positive_cases if pattern_id in set(case.get("features", [])))
        negative_hits = sum(1 for case in negative_cases if pattern_id in set(case.get("features", [])))
        precision = positive_hits / max(1, positive_hits + negative_hits)
        coverage = positive_hits / positive_base
        base_weight = int(_number(pattern.get("weight")))
        learned_weight = max(5, min(40, int(round(base_weight * (0.72 + coverage * 0.52 + precision * 0.18)))))
        learned_features.append({
            "id": pattern_id,
            "name": pattern.get("name", pattern_id),
            "base_weight": base_weight,
            "learned_weight": learned_weight,
            "positive_hits": positive_hits,
            "negative_hits": negative_hits,
            "confidence": round((coverage * 0.65 + precision * 0.35), 3),
            "description": pattern.get("description", ""),
            "current_project_hit": pattern.get("name", "") in issue_text or pattern_id.split("_")[0] in issue_text,
        })

    learned_features.sort(key=lambda item: (item["confidence"], item["learned_weight"]), reverse=True)
    return {
        "model": "历史虚假结算特征训练模型",
        "algorithm": "规则特征 + 历史样本监督打分",
        "sample_count": len(cases),
        "positive_cases": len(positive_cases),
        "negative_cases": len(negative_cases),
        "threshold": 60,
        "features": learned_features,
        "top_features": [item["name"] for item in learned_features[:3]],
        "training_status": "已训练" if cases else "使用内置规则权重",
        "training_note": "样本用于学习特征权重，审计结论仍由规则证据链和主审复核确认。",
    }


def _default_audit_models() -> list[dict[str, Any]]:
    return [
        {
            "id": "M-PRE-001",
            "phase": "事前",
            "model_type": "预警类",
            "business_end": "招采与合同端",
            "name": "招标清单与合同清单差异预警",
            "categories": ["工程量"],
            "input_documents": ["招标文件", "合同"],
            "key_fields": ["项目编码", "清单子目", "招标工程量", "合同工程量", "合同单价"],
            "rule": "合同工程量或单价较招标清单偏离超过阈值时预警",
            "output": "合同签约前清单差异清单",
            "agents": ["资料解析智能体", "结构化建模智能体", "造价专家", "商务专家"],
        },
        {
            "id": "M-PRE-002",
            "phase": "事前",
            "model_type": "预警类",
            "business_end": "合同法务端",
            "name": "合同计价与调价条款风险预警",
            "categories": ["单价", "取费"],
            "input_documents": ["合同", "专用条款", "招标文件"],
            "key_fields": ["计价方式", "调价条件", "取费费率", "审批权限"],
            "rule": "固定价、调价、取费和审批条款缺失或冲突时预警",
            "output": "合同条款风险提示",
            "agents": ["制度知识库机器人", "商务专家", "法务专家"],
        },
        {
            "id": "M-IN-001",
            "phase": "事中",
            "model_type": "预警类",
            "business_end": "变更签证端",
            "name": "变更签证审批链闭合预警",
            "categories": ["变更签证", "索赔"],
            "input_documents": ["变更签证", "索赔资料", "合同"],
            "key_fields": ["变更编号", "审批状态", "审批岗位", "金额", "关联清单"],
            "rule": "未完成授权审批链的变更、索赔进入支付或结算时预警",
            "output": "审批链缺口与冻结支付建议",
            "agents": ["规则比对智能体", "商务专家", "法务专家"],
        },
        {
            "id": "M-IN-002",
            "phase": "事中",
            "model_type": "预警类",
            "business_end": "成本支付端",
            "name": "支付进度与实际完成量偏差预警",
            "categories": ["实际完成量"],
            "input_documents": ["竣工验收", "支付台账", "产值报表"],
            "key_fields": ["已支付金额", "实际认证金额", "完成量", "验收节点"],
            "rule": "已支付金额高于实际完成量对应金额时预警",
            "output": "支付超前风险清单",
            "agents": ["财务专家", "履约专家", "规则比对智能体"],
        },
        {
            "id": "M-IN-003",
            "phase": "事中",
            "model_type": "专项核查类",
            "business_end": "供应链与现场端",
            "name": "材料损耗与机械台班跑冒滴漏核查",
            "categories": ["跑冒滴漏"],
            "input_documents": ["材料台账", "机械台班", "施工日志", "目标成本"],
            "key_fields": ["理论消耗量", "领用量", "退库量", "台班小时", "日志匹配"],
            "rule": "材料损耗率或台班未匹配记录超过专项阈值时标记",
            "output": "材料超耗、台班缺证与责任追踪",
            "agents": ["招采供应链专家", "履约专家", "财务专家"],
        },
        {
            "id": "M-POST-001",
            "phase": "事后",
            "model_type": "问题类",
            "business_end": "结算审核端",
            "name": "工程量三表比对问题模型",
            "categories": ["工程量", "实际完成量"],
            "input_documents": ["合同", "变更签证", "竣工验收", "结算书"],
            "key_fields": ["合同工程量", "实际完成工程量", "结算工程量", "变更工程量"],
            "rule": "结算工程量 vs 合同工程量 vs 实际完成工程量偏差超过阈值",
            "output": "工程量核减疑点",
            "agents": ["造价专家", "履约专家", "主审驾驶舱"],
        },
        {
            "id": "M-POST-002",
            "phase": "事后",
            "model_type": "问题类",
            "business_end": "结算审核端",
            "name": "单价合同与市场参考偏差模型",
            "categories": ["单价", "取费"],
            "input_documents": ["合同", "结算书", "市场价资料", "取费表"],
            "key_fields": ["合同单价", "结算单价", "市场参考价", "取费费率"],
            "rule": "结算单价偏离合同超过 10% 或取费费率高于批准值",
            "output": "单价及取费疑点",
            "agents": ["造价专家", "招采供应链专家", "财务专家"],
        },
        {
            "id": "M-POST-003",
            "phase": "事后",
            "model_type": "问题类",
            "business_end": "商务结算端",
            "name": "合同价、变更、索赔、奖罚金额归集模型",
            "categories": ["索赔", "奖罚款", "变更签证"],
            "input_documents": ["合同", "变更签证", "索赔资料", "奖罚资料", "结算书"],
            "key_fields": ["合同总价", "批准变更", "索赔批准金额", "罚款扣减", "结算金额"],
            "rule": "未批准索赔、奖励或应扣未扣罚款进入结算时标记",
            "output": "金额归集差异与应核减金额",
            "agents": ["商务专家", "财务专家", "法务专家"],
        },
        {
            "id": "M-POST-004",
            "phase": "事后",
            "model_type": "识别类",
            "business_end": "反舞弊与审计端",
            "name": "历史虚假结算特征识别模型",
            "categories": ["工程量", "实际完成量", "索赔", "变更签证", "单价", "取费"],
            "input_documents": ["历史案例库", "结算书", "合同", "变更签证", "竣工验收"],
            "key_fields": ["重复计费", "虚列子目", "不合理索赔", "未完审批", "费率漂移"],
            "rule": "历史虚假结算特征累计评分达到阈值时输出高风险",
            "output": "虚假结算风险评分与命中特征",
            "agents": ["虚假结算识别智能体", "主审驾驶舱", "制度知识库机器人"],
        },
        {
            "id": "M-POST-005",
            "phase": "事后",
            "model_type": "整改类",
            "business_end": "整改闭环端",
            "name": "审计问题整改验证销号模型",
            "categories": ["整改"],
            "input_documents": ["审计报告", "整改说明", "核减确认单", "验证记录"],
            "key_fields": ["问题编号", "责任部门", "整改状态", "核减金额", "验证证据"],
            "rule": "主审确认问题后生成整改任务并跟踪验证状态",
            "output": "整改任务台账与销号记录",
            "agents": ["主审驾驶舱", "制度知识库机器人"],
        },
    ]


def build_audit_model_catalog(
    data: dict[str, Any],
    issues: list[dict[str, Any]],
    remediation_tasks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a lifecycle model catalog spanning warning, issue, detection and close-out models."""
    configured = data.get("audit_models") or _default_audit_models()
    lifecycle = data.get("lifecycle_findings", [])
    remediation_summary = (remediation_tasks or {}).get("summary", {})
    models = []
    for model in configured:
        categories = set(model.get("categories", []))
        matched_issues = [
            issue for issue in issues
            if issue.get("category") in categories or (model.get("id") == "M-POST-004" and issue.get("risk") == "高风险")
        ]
        matched_lifecycle = [
            item for item in lifecycle
            if item.get("phase") == model.get("phase")
            and (item.get("model_type") == model.get("model_type") or item.get("domain") in model.get("business_end", ""))
        ]
        if model.get("id") == "M-POST-005":
            hit_count = remediation_summary.get("total", 0)
            risk_amount = remediation_summary.get("amount", 0)
        else:
            hit_count = len(matched_issues) + len(matched_lifecycle)
            risk_amount = sum(_number(item.get("amount")) for item in matched_issues + matched_lifecycle)
        enriched = deepcopy(model)
        enriched.update({
            "status": "已命中" if hit_count else "待监控",
            "hit_count": hit_count,
            "risk_amount": _money(risk_amount),
            "matched_issue_ids": [item.get("id", "") for item in matched_issues],
            "matched_lifecycle_ids": [item.get("id", "") for item in matched_lifecycle],
        })
        models.append(enriched)

    phase_counts = {
        phase: sum(1 for item in models if item.get("phase") == phase)
        for phase in ["事前", "事中", "事后"]
    }
    type_counts = {
        model_type: sum(1 for item in models if item.get("model_type") == model_type)
        for model_type in sorted({item.get("model_type") for item in models})
    }
    return {
        "models": models,
        "summary": {
            "model_count": len(models),
            "active_model_count": sum(1 for item in models if item.get("hit_count", 0) > 0),
            "phase_counts": phase_counts,
            "type_counts": type_counts,
            "business_end_count": len({item.get("business_end") for item in models}),
            "hit_count": sum(item.get("hit_count", 0) for item in models),
            "risk_amount": sum(item.get("risk_amount", 0) for item in models),
        },
    }


def build_agent_orchestration(data: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Assign audit work to the chief auditor, domain experts and assistant bots."""
    experts = {item.get("name"): item for item in data.get("experts", [])}
    roles = [
        {"id": "chief", "name": "主审驾驶舱", "type": "主审", "responsibility": "统一分派疑点、综合专家意见、完成人工复核并签发报告"},
        {"id": "parser", "name": "资料解析智能体", "type": "处理类", "responsibility": "识别招标、合同、签证、竣工、结算及台账文件并抽取字段"},
        {"id": "modeler", "name": "结构化建模智能体", "type": "处理类", "responsibility": "将多文档映射到统一清单、费用、证据和生命周期模型"},
        {"id": "rule", "name": "规则比对智能体", "type": "问题类", "responsibility": "执行工程量、单价、金额、索赔、奖罚、取费和跑冒滴漏规则"},
        {"id": "fraud", "name": "虚假结算识别智能体", "type": "问题类", "responsibility": "基于历史特征识别重复计费、虚列子目和不合理索赔"},
        {"id": "knowledge", "name": "制度知识库机器人", "type": "问询类", "responsibility": "返回制度依据、适用条件、证据清单和补证建议"},
    ]
    roles.extend(
        {
            "id": f"expert-{expert.get('id', index)}",
            "name": expert.get("name", ""),
            "type": "专家类",
            "responsibility": expert.get("domain", ""),
        }
        for index, expert in enumerate(data.get("experts", []), 1)
    )
    category_map = {
        "工程量": ["造价专家", "商务专家", "履约专家"],
        "单价": ["造价专家", "招采供应链专家", "商务专家"],
        "实际完成量": ["履约专家", "造价专家"],
        "变更签证": ["商务专家", "法务专家", "造价专家"],
        "索赔": ["商务专家", "法务专家", "财务专家"],
        "奖罚款": ["商务专家", "法务专家", "财务专家"],
        "取费": ["造价专家", "财务专家", "商务专家"],
        "跑冒滴漏": ["招采供应链专家", "履约专家", "财务专家"],
    }
    tasks = []
    for issue in issues:
        expert_names = category_map.get(issue.get("category"), ["造价专家", "商务专家"])
        task_agents = [
            {"role": "主审驾驶舱", "action": "复核裁量"},
            {"role": "规则比对智能体", "action": "复算金额和规则命中"},
            {"role": "制度知识库机器人", "action": "检索制度依据和补证清单"},
        ]
        task_agents.extend(
            {"role": name, "action": experts.get(name, {}).get("domain", "专业复核")}
            for name in expert_names
        )
        if issue.get("category") in {"工程量", "实际完成量", "变更签证", "索赔", "奖罚款", "取费"}:
            task_agents.append({"role": "虚假结算识别智能体", "action": "匹配历史虚假结算特征"})
        tasks.append({
            "issue_id": issue.get("id", ""),
            "title": issue.get("title", ""),
            "category": issue.get("category", ""),
            "risk": issue.get("risk", ""),
            "amount": issue.get("amount", 0),
            "agents": task_agents,
            "status": "待主审复核",
        })
    return {
        "workflow": ["资料解析", "结构化建模", "规则比对", "疑点分派", "专家会审", "知识问询", "主审复核", "报告签发", "整改闭环"],
        "roles": roles,
        "tasks": tasks,
        "summary": {
            "role_count": len(roles),
            "task_count": len(tasks),
            "expert_count": len(data.get("experts", [])),
            "knowledge_bot_count": 1,
        },
    }


def summarize(
    data: dict[str, Any],
    issues: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    cost_aggregation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    financials = data.get("project", {}).get("financials", {})
    cost_aggregation = cost_aggregation or aggregate_cost_components(data)
    risk_total = sum(x["amount"] for x in issues)
    quantity_flags = sum(1 for x in comparisons if abs(x.get("quantity_deviation_settlement_vs_actual") or 0) > DEFAULT_THRESHOLDS["quantity_deviation_pct"])
    price_flags = sum(1 for x in comparisons if abs(x.get("unit_price_deviation_settlement_vs_contract") or 0) > DEFAULT_THRESHOLDS["unit_price_deviation_pct"])
    return {
        "issue_count": len(issues),
        "high_risk_count": sum(1 for x in issues if x["risk"] == "高风险"),
        "medium_risk_count": sum(1 for x in issues if x["risk"] == "中风险"),
        "risk_amount": risk_total,
        "contract_total": _money(_number(financials.get("contract_total"))),
        "approved_change_total": _money(_number(financials.get("approved_change_total"))),
        "settlement_total": _money(_number(financials.get("settlement_total"))),
        "actual_certified_total": _money(_number(financials.get("actual_certified_total"))),
        "paid_total": _money(_number(financials.get("paid_total"))),
        "payment_gap": _money(max(0, _number(financials.get("paid_total")) - _number(financials.get("actual_certified_total")))),
        "quantity_flags": quantity_flags,
        "unit_price_flags": price_flags,
        "document_count": len(data.get("project", {}).get("documents", [])),
        "category_counts": {
            category: sum(1 for item in issues if item.get("category") == category)
            for category in sorted({item.get("category") for item in issues})
        },
        "cost_aggregation": cost_aggregation,
    }


def build_report(
    data: dict[str, Any],
    issues: list[dict[str, Any]],
    summary: dict[str, Any],
    decisions: dict[str, Any] | None = None,
    fraud_assessment: dict[str, Any] | None = None,
    remediation_tasks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decisions = decisions or {}
    fraud_assessment = fraud_assessment or {}
    remediation_tasks = remediation_tasks or {"summary": {}}
    confirmed = [x for x in issues if decisions.get(x["id"], {}).get("decision") == "confirm"]
    cost = summary.get("cost_aggregation", {})
    remediation_summary = remediation_tasks.get("summary", {})
    return {
        "status": "正式问题待主审签发" if confirmed else "主审复核草稿",
        "title": f"{data.get('project', {}).get('name', '')}工程结算审计报告（草稿）",
        "confirmed_count": len(confirmed),
        "issue_count": len(issues),
        "risk_amount": summary["risk_amount"],
        "confirmed_amount": sum(x["amount"] for x in confirmed),
        "sections": [
            "一、审计范围与资料接入",
            "二、合同价、变更价、实际完成量与结算金额比对",
            "三、异常规则与跑冒滴漏核查",
            "四、审计问题、依据及责任建议",
            "五、整改验证与销号安排",
        ],
        "management_notes": [
            f"识别 {summary['issue_count']} 项疑点，规则测算影响金额 {_money(summary['risk_amount']):,} 元。",
            f"已支付金额较实际完成量对应金额高 {_money(summary['payment_gap']):,} 元，需联动支付审核。",
            f"索赔申报 {cost.get('claims_settlement', 0):,} 元，已批准 {cost.get('claims_approved', 0):,} 元，未批准进入结算 {cost.get('unapproved_claim_amount', 0):,} 元。",
            f"虚假结算特征模型评分 {fraud_assessment.get('score', 0)} 分，等级：{fraud_assessment.get('level', '低风险')}。",
            f"已生成整改任务 {remediation_summary.get('total', 0)} 项，待整改金额 {remediation_summary.get('open_amount', 0):,} 元。",
            "正式审计问题须在主审完成人工裁量后签发。",
        ],
    }


def _remediation_owner(category: str) -> str:
    owner_map = {
        "工程量": "项目商务部 / 造价负责人",
        "单价": "项目商务部 / 招采供应链",
        "实际完成量": "项目履约部 / 工程管理部",
        "变更签证": "项目商务部 / 合同法务",
        "索赔": "项目商务部 / 合同法务",
        "奖罚款": "项目商务部 / 财务共享",
        "取费": "项目商务部 / 财务共享",
        "跑冒滴漏": "项目成本部 / 供应链管理",
    }
    return owner_map.get(category, "项目商务部")


def _remediation_actions(category: str) -> list[str]:
    action_map = {
        "工程量": ["复核竣工图、现场收方和结算清单", "按实际完成量办理核减或补证", "形成清单扣减记录"],
        "单价": ["核对合同单价和调价条款", "补充审批单、报价或市场依据", "确认未获批准价差核减金额"],
        "实际完成量": ["组织现场核验或资料复验", "补齐移交、验收、拆除等证据", "证据不足部分不予计入结算"],
        "变更签证": ["补齐授权审批链", "复核变更与原合同清单扣减关系", "未闭合前冻结对应结算支付"],
        "索赔": ["复核责任划分和工期影响", "补充费用测算与正式批复", "剔除未批准或重复索赔金额"],
        "奖罚款": ["勾稽奖罚台账、处罚单和结算扣减", "补扣已生效罚款", "未获批奖励不得计入结算"],
        "取费": ["复核合同费率、批准费率和计费基数", "核减超合同费率取费", "保留取费复算底稿"],
        "跑冒滴漏": ["勾稽材料、台班、日志和盘点记录", "追踪超耗超领责任", "核减无有效证据的成本"],
    }
    return action_map.get(category, ["补充审计证据", "明确责任部门和整改金额", "提交主审复核"])


def build_remediation_tasks(
    issues: list[dict[str, Any]],
    decisions: dict[str, Any] | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create close-out tasks for issues confirmed by the chief auditor."""
    decisions = decisions or {}
    updates = updates or {}
    tasks: list[dict[str, Any]] = []
    for issue in issues:
        decision = decisions.get(issue.get("id", ""), {})
        if decision.get("decision") != "confirm":
            continue
        update = updates.get(issue.get("id", ""), {})
        status = update.get("status") or "待整改"
        actions = _remediation_actions(issue.get("category", ""))
        task = {
            "task_id": f"rem-{issue.get('id', '')}",
            "issue_id": issue.get("id", ""),
            "title": issue.get("title", ""),
            "category": issue.get("category", ""),
            "risk": issue.get("risk", ""),
            "amount": issue.get("amount", 0),
            "owner": update.get("owner") or _remediation_owner(issue.get("category", "")),
            "status": status,
            "decision_time": decision.get("time", ""),
            "chief_note": decision.get("note", ""),
            "due_rule": "主审确认后 10 个工作日内完成整改反馈，20 个工作日内完成验证销号",
            "actions": actions,
            "required_evidence": [
                "整改说明或核减确认单",
                "责任部门复核底稿",
                "支撑资料页码、影像或系统截图",
                "主审验证记录",
            ],
            "latest_update": {
                "status": status,
                "note": update.get("note", ""),
                "time": update.get("time", ""),
                "operator": update.get("operator", ""),
            },
        }
        tasks.append(task)
    closed_statuses = {"已销号"}
    verified_statuses = {"整改验证", "已销号"}
    return {
        "workflow": ["主审确认", "下发整改", "责任部门反馈", "审计验证", "销号归档"],
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "waiting": sum(1 for x in tasks if x["status"] == "待整改"),
            "in_progress": sum(1 for x in tasks if x["status"] == "整改中"),
            "verifying": sum(1 for x in tasks if x["status"] == "整改验证"),
            "closed": sum(1 for x in tasks if x["status"] in closed_statuses),
            "verified": sum(1 for x in tasks if x["status"] in verified_statuses),
            "amount": sum(x["amount"] for x in tasks),
            "open_amount": sum(x["amount"] for x in tasks if x["status"] not in closed_statuses),
        },
    }


def build_structured_model(
    data: dict[str, Any],
    comparisons: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    cost_aggregation: dict[str, Any],
    fraud_assessment: dict[str, Any],
    agent_orchestration: dict[str, Any],
    audit_model_catalog: dict[str, Any],
    false_settlement_training: dict[str, Any],
) -> dict[str, Any]:
    project = data.get("project", {})
    documents = [
        {
            "id": doc.get("id", ""),
            "type": doc.get("type", ""),
            "name": doc.get("name", ""),
            "version": doc.get("version", ""),
            "location": doc.get("location", ""),
            "status": doc.get("status", ""),
            "fields": doc.get("fields", 0),
        }
        for doc in project.get("documents", [])
    ]
    items = []
    for row in comparisons:
        evidence = row.get("evidence", [])
        items.append(
            {
                "item_code": row.get("item_code", ""),
                "name": row.get("name", ""),
                "unit": row.get("unit", ""),
                "location": row.get("location", ""),
                "bid": row.get("bid", {}),
                "contract": row.get("contract", {}),
                "actual": row.get("actual", {}),
                "settlement": row.get("settlement", {}),
                "change": row.get("change", {}),
                "market_unit_price": row.get("market_unit_price"),
                "quantity_deviation_settlement_vs_actual": row.get("quantity_deviation_settlement_vs_actual"),
                "unit_price_deviation_settlement_vs_contract": row.get("unit_price_deviation_settlement_vs_contract"),
                "evidence_count": len(evidence),
                "evidence": evidence,
            }
        )
    lifecycle = deepcopy(data.get("lifecycle_findings", []))
    phase_models: list[dict[str, Any]] = []
    phase_index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in lifecycle:
        key = (item.get("phase", ""), item.get("model_type", ""))
        slot = phase_index.get(key)
        if not slot:
            slot = {
                "phase": item.get("phase", ""),
                "model_type": item.get("model_type", ""),
                "domain": item.get("domain", ""),
                "count": 0,
                "amount": 0,
                "titles": [],
            }
            phase_index[key] = slot
            phase_models.append(slot)
        slot["count"] += 1
        slot["amount"] += _money(_number(item.get("amount")))
        slot["titles"].append(item.get("title", ""))
    return {
        "project": {
            "id": project.get("id", ""),
            "name": project.get("name", ""),
            "phase": project.get("phase", ""),
            "owner": project.get("owner", ""),
            "contractor": project.get("contractor", ""),
            "status": project.get("status", ""),
        },
        "documents": documents,
        "items": items,
        "cost_components": {
            "claims": deepcopy(project.get("cost_components", {}).get("claims", [])),
            "rewards_penalties": deepcopy(project.get("cost_components", {}).get("rewards_penalties", [])),
            "management_fee": deepcopy(project.get("cost_components", {}).get("management_fee", {})),
            "aggregation": deepcopy(cost_aggregation),
        },
        "lifecycle": lifecycle,
        "lifecycle_models": phase_models,
        "experts": deepcopy(data.get("experts", [])),
        "agents": deepcopy(agent_orchestration),
        "audit_model_catalog": deepcopy(audit_model_catalog),
        "false_settlement_training": deepcopy(false_settlement_training),
        "knowledge": deepcopy(data.get("knowledge", [])),
        "thresholds": deepcopy(data.get("meta", {}).get("thresholds", DEFAULT_THRESHOLDS)),
        "fraud_assessment": deepcopy(fraud_assessment),
        "summary": {
            "document_count": len(documents),
            "item_count": len(items),
            "issue_count": len(issues),
            "historical_pattern_count": len(data.get("historical_patterns", [])),
            "historical_case_count": len(data.get("historical_cases", [])),
            "audit_model_count": audit_model_catalog.get("summary", {}).get("model_count", 0),
            "lifecycle_count": len(data.get("lifecycle_findings", [])),
        },
    }


def analyze(
    data: dict[str, Any],
    decisions: dict[str, Any] | None = None,
    remediation_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    comparisons = compare_three_tables(data)
    issues = detect_issues(data, comparisons)
    issues.extend(detect_cost_component_issues(data))
    cost_aggregation = aggregate_cost_components(data)
    fraud_assessment = score_fraud_risk(data, issues)
    agent_orchestration = build_agent_orchestration(data, issues)
    summary = summarize(data, issues, comparisons, cost_aggregation)
    remediation_tasks = build_remediation_tasks(issues, decisions, remediation_updates)
    false_settlement_training = train_false_settlement_model(data, issues)
    audit_model_catalog = build_audit_model_catalog(data, issues, remediation_tasks)
    structured_model = build_structured_model(
        data,
        comparisons,
        issues,
        cost_aggregation,
        fraud_assessment,
        agent_orchestration,
        audit_model_catalog,
        false_settlement_training,
    )
    return {
        "meta": deepcopy(data.get("meta", {})),
        "organizations": deepcopy(data.get("organizations", [])),
        "project": deepcopy(data.get("project", {})),
        "comparisons": comparisons,
        "issues": issues,
        "summary": summary,
        "cost_aggregation": cost_aggregation,
        "fraud_assessment": fraud_assessment,
        "agent_orchestration": agent_orchestration,
        "audit_model_catalog": audit_model_catalog,
        "false_settlement_training": false_settlement_training,
        "structured_model": structured_model,
        "experts": deepcopy(data.get("experts", [])),
        "expert_opinions": deepcopy(data.get("expert_opinions", {})),
        "lifecycle_findings": deepcopy(data.get("lifecycle_findings", [])),
        "knowledge": deepcopy(data.get("knowledge", [])),
        "closure": deepcopy(data.get("closure", [])),
        "remediation_tasks": remediation_tasks,
        "report": build_report(data, issues, summary, decisions, fraud_assessment, remediation_tasks),
    }


def answer_question(question: str, knowledge: list[dict[str, Any]], issue: dict[str, Any] | None = None) -> dict[str, Any]:
    query = (question or "").strip()
    if not query:
        return {"answer": "请输入制度、签证、结算、工程量或材料损耗问题。", "matches": []}
    ranked = []
    for entry in knowledge:
        score = sum(1 for word in entry.get("keywords", []) if word in query)
        if score:
            ranked.append((score, entry))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    matches = [entry for _, entry in ranked[:3]]
    if matches:
        primary = matches[0]
        answer = f"依据《{primary['title']}》{primary['basis']}：{primary['answer']}"
    else:
        answer = "当前知识库未命中明确条款。建议先核对合同专用条款、授权清单、变更审批链和实际完成量证据，并由主审人工确认。"
    if issue:
        answer += f" 当前关联疑点为“{issue.get('title', '')}”，涉及金额 {_money(_number(issue.get('amount'))):,} 元。"
    return {"answer": answer, "matches": matches}


def _tabular_rows_from_bytes(filename: str, payload: bytes) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        value = json.loads(payload.decode("utf-8-sig"))
        if isinstance(value, dict):
            return value.get("rows") or value.get("line_items") or [value]
        return value if isinstance(value, list) else []
    if suffix == ".csv":
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    if suffix in {".xlsx", ".xls"}:
        try:
            import openpyxl
        except ImportError:
            return []
        workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        values = list(sheet.values)
        if not values:
            return []
        headers = [str(x or "").strip() for x in values[0]]
        return [dict(zip(headers, row)) for row in values[1:] if any(x is not None for x in row)]
    return []


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def structured_json_from_bytes(filename: str, payload: bytes) -> dict[str, Any] | None:
    if Path(filename).suffix.lower() != ".json":
        return None
    value = json.loads(payload.decode("utf-8-sig"))
    if isinstance(value, dict) and isinstance(value.get("line_items"), list):
        return value
    return None


def _preview_zip_member(name: str, payload: bytes) -> dict[str, Any]:
    preview = {
        "name": Path(name).name,
        "type": Path(name).suffix.lstrip("."),
        "document_type": classify_document_name(name),
        "fields": {},
        "rows": [],
        "warnings": [],
    }
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        structured = structured_json_from_bytes(name, payload)
        rows = _tabular_rows_from_bytes(name, payload)
        for row in rows:
            if isinstance(row, dict):
                row.setdefault("__document_name", Path(name).name)
                row.setdefault("__document_type", preview["document_type"])
        preview["rows"] = rows[:100]
        preview["fields"] = {"row_count": len(rows), "columns": list(rows[0].keys()) if rows else []}
        if structured:
            preview["structured_data"] = structured
            preview["fields"]["schema"] = "settlement_demo.line_items"
        return preview
    if suffix in {".csv", ".xlsx", ".xls"}:
        rows = _tabular_rows_from_bytes(name, payload)
        for row in rows:
            if isinstance(row, dict):
                row.setdefault("__document_name", Path(name).name)
                row.setdefault("__document_type", preview["document_type"])
        preview["rows"] = rows[:100]
        preview["fields"] = {"row_count": len(rows), "columns": list(rows[0].keys()) if rows else []}
        return preview
    if suffix in {".pdf", ".docx", ".txt"}:
        text, warnings, docx_rows = _extract_text_payload(name, payload)
        rows = docx_rows or _extract_text_rows(text, preview["document_type"], Path(name).name)
        for row in rows:
            row.setdefault("__document_name", Path(name).name)
            row.setdefault("__document_type", preview["document_type"])
        preview["rows"] = rows[:100]
        preview["fields"] = _extract_text_fields(text)
        preview["fields"].update({
            "text_length": len(text),
            "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
        })
        preview["warnings"].extend(warnings)
        if not rows and suffix in {".pdf", ".docx"}:
            preview["warnings"].append("已抽取文本但未识别到标准清单行，可补充 OCR 或表头映射。")
        return preview
    preview["warnings"].append("Zip 内子文件类型暂未展开。")
    return preview


def classify_document_name(filename: str) -> str:
    name = Path(filename).stem
    categories = [
        ("合同", ("合同", "协议", "专用条款")),
        ("结算书", ("结算", "取费表")),
        ("变更签证", ("变更", "签证", "洽商")),
        ("竣工验收", ("竣工", "验收", "移交")),
        ("索赔资料", ("索赔", "工期索赔")),
        ("奖罚资料", ("奖罚", "奖励", "罚款", "处罚")),
        ("材料台账", ("材料", "收发存", "领用")),
        ("机械台班", ("机械", "台班", "施工日志")),
        ("招标文件", ("招标", "清单", "投标")),
    ]
    for label, words in categories:
        if any(word in name for word in words):
            return label
    return "未分类资料"


def _first(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias in row:
            return row.get(alias)
        value = lowered.get(alias.lower())
        if value not in (None, ""):
            return value
    return None


def _infer_section(document_type: str, row: dict[str, Any]) -> str:
    explicit = str(_first(row, ("section", "source", "来源", "资料类型", "表类型", "阶段")) or "")
    text = f"{document_type} {explicit} {' '.join(str(key) for key in row.keys())}"
    if any(word in text for word in ("结算", "申报")):
        return "settlement"
    if any(word in text for word in ("合同", "协议")):
        return "contract"
    if any(word in text for word in ("实际", "完成", "竣工", "验收", "收方")):
        return "actual"
    if any(word in text for word in ("变更", "签证", "洽商")):
        return "change"
    if any(word in text for word in ("招标", "投标")):
        return "bid"
    return "settlement"


def normalize_uploaded_rows(preview: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(preview.get("rows", []), 1):
        if not isinstance(row, dict):
            continue
        document_type = str(row.get("__document_type") or preview.get("document_type", ""))
        document_name = str(row.get("__document_name") or preview.get("name", ""))
        item_code = str(_first(row, ("item_code", "项目编码", "清单编码", "项目编号", "编码")) or "").strip()
        name = str(_first(row, ("name", "项目名称", "清单子目", "子目名称", "工作内容")) or "").strip()
        if not item_code and not name:
            continue
        section = _infer_section(document_type, row)
        quantity = _number(_first(row, (
            "quantity",
            "工程量",
            "数量",
            "招标工程量",
            "合同工程量",
            "实际完成工程量",
            "结算工程量",
            "申报量",
        )))
        unit_price = _number(_first(row, (
            "unit_price",
            "单价",
            "综合单价",
            "招标单价",
            "合同单价",
            "结算单价",
            "市场参考价",
        )))
        amount = _number(_first(row, ("amount", "合价", "金额", "结算金额", "合同金额", "申报金额")))
        normalized.append({
            "item_code": item_code or f"UPLOAD-{index:03d}",
            "name": name or item_code,
            "unit": str(_first(row, ("unit", "单位", "计量单位")) or "").strip(),
            "location": str(_first(row, ("location", "部位", "工程部位", "施工部位")) or "").strip(),
            "section": section,
            "quantity": quantity,
            "unit_price": unit_price,
            "amount": amount or quantity * unit_price,
            "market_unit_price": _number(_first(row, ("market_unit_price", "市场参考价", "市场价"))),
            "document": document_name,
            "document_type": document_type,
            "source_row": index,
        })
    return normalized


def build_data_from_upload_previews(base_data: dict[str, Any], previews: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """Build an active audit dataset from uploaded structured files or tables."""
    for preview in reversed(previews):
        structured = preview.get("structured_data")
        if isinstance(structured, dict) and isinstance(structured.get("line_items"), list):
            data = deepcopy(structured)
            data.setdefault("meta", {}).update({"data_source": "上传结构化资料包", "last_analysis": datetime_label()})
            return data, True

    records: list[dict[str, Any]] = []
    for preview in previews:
        records.extend(normalize_uploaded_rows(preview))
    if not records:
        return deepcopy(base_data), False

    data = deepcopy(base_data)
    data.setdefault("meta", {}).update({"data_source": "上传表格资料包", "last_analysis": datetime_label()})
    documents = []
    for index, preview in enumerate(previews, 1):
        documents.append({
            "id": f"upload-doc-{index:03d}",
            "type": preview.get("document_type", preview.get("type", "")),
            "name": preview.get("name", ""),
            "version": "上传版",
            "location": "上传资料包",
            "status": "已识别",
            "fields": preview.get("fields", {}).get("row_count", len(preview.get("rows", []))),
        })

    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        item = grouped.setdefault(record["item_code"], {
            "item_code": record["item_code"],
            "name": record["name"],
            "unit": record["unit"],
            "location": record["location"],
            "bid": {},
            "contract": {},
            "change": {"quantity": 0, "unit_price": 0, "amount": 0, "approved": True},
            "actual": {},
            "settlement": {},
            "evidence": [],
        })
        if record["name"]:
            item["name"] = record["name"]
        if record["unit"]:
            item["unit"] = record["unit"]
        if record["location"]:
            item["location"] = record["location"]
        section = record["section"]
        item[section] = {
            "quantity": record["quantity"],
            "unit_price": record["unit_price"],
            "amount": record["amount"],
        }
        if section == "change":
            item[section]["approved"] = True
        if record.get("market_unit_price"):
            item["market_unit_price"] = record["market_unit_price"]
        item["evidence"].append({
            "document": record["document"],
            "location": f"{record['document_type']} / 第 {record['source_row']} 行",
            "fact": f"{record['name']} {record['quantity']} {record['unit']}，单价 {record['unit_price']}，金额 {record['amount']}",
        })

    line_items = list(grouped.values())
    data["project"]["documents"] = documents
    data["line_items"] = line_items
    data["project"]["cost_components"] = {"claims": [], "rewards_penalties": [], "management_fee": {}}
    financials = data["project"].setdefault("financials", {})
    financials["contract_total"] = _money(sum(_amount(item.get("contract", {})) for item in line_items))
    financials["approved_change_total"] = _money(sum(_amount(item.get("change", {})) for item in line_items))
    financials["settlement_total"] = _money(sum(_amount(item.get("settlement", {})) for item in line_items))
    financials["actual_certified_total"] = _money(sum(_amount(item.get("actual", {})) for item in line_items))
    financials["paid_total"] = financials.get("paid_total", financials["actual_certified_total"])
    return data, True


def datetime_label() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _extract_text_fields(text: str) -> dict[str, Any]:
    patterns = {
        "project_code": r"(?:合同编号|项目编号|项目编码)\s*[:：]?\s*([A-Za-z0-9-]+)",
        "quantity": r"(?:工程量|数量|申报量)\s*[:：]?\s*([\d,.]+)",
        "unit_price": r"(?:单价|含税单价|结算单价)\s*[:：]?\s*([\d,.]+)",
        "amount": r"(?:合价|金额|结算金额)\s*[:：]?\s*([\d,.]+)",
    }
    fields: dict[str, Any] = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text or "")
        if match:
            fields[name] = _number(match.group(1)) if name != "project_code" else match.group(1)
    return fields


def _extract_text_rows(text: str, document_type: str, document_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    line_pattern = re.compile(
        r"(?P<code>[A-Za-z0-9]{1,8}[-]?\d{2,}|[A-Za-z]{1,4}-\d{2,})"
        r"[\s,，|；;]+(?P<name>[\u4e00-\u9fa5A-Za-z0-9（）()·\- ]{2,40}?)"
        r"[\s,，|；;]+(?P<unit>m3|m³|㎡|m2|t|台班|项|米|吨|立方米|平方米)"
        r"[\s,，|；;]+(?P<quantity>-?\d+(?:,\d{3})*(?:\.\d+)?)"
        r"[\s,，|；;]+(?P<unit_price>-?\d+(?:,\d{3})*(?:\.\d+)?)"
        r"(?:[\s,，|；;]+(?P<amount>-?\d+(?:,\d{3})*(?:\.\d+)?))?"
    )
    labeled_pattern = re.compile(
        r"(?:项目编码|清单编码|编码)[:：]?\s*(?P<code>[A-Za-z0-9-]+).*?"
        r"(?:项目名称|清单子目|名称)[:：]?\s*(?P<name>[\u4e00-\u9fa5A-Za-z0-9（）()·\- ]+?)\s+"
        r"(?:单位|计量单位)[:：]?\s*(?P<unit>m3|m³|㎡|m2|t|台班|项|米|吨|立方米|平方米).*?"
        r"(?:工程量|数量|申报量)[:：]?\s*(?P<quantity>-?\d+(?:,\d{3})*(?:\.\d+)?).*?"
        r"(?:单价|综合单价|含税单价)[:：]?\s*(?P<unit_price>-?\d+(?:,\d{3})*(?:\.\d+)?)"
        r"(?:.*?(?:合价|金额|结算金额)[:：]?\s*(?P<amount>-?\d+(?:,\d{3})*(?:\.\d+)?))?"
    )
    for index, raw_line in enumerate((text or "").splitlines(), 1):
        line = re.sub(r"\s+", " ", raw_line.strip())
        if not line:
            continue
        match = labeled_pattern.search(line) or line_pattern.search(line)
        if not match:
            continue
        values = match.groupdict()
        quantity = _number(values.get("quantity"))
        unit_price = _number(values.get("unit_price"))
        amount = _number(values.get("amount"))
        rows.append({
            "项目编码": str(values.get("code") or "").strip(),
            "项目名称": str(values.get("name") or "").strip(" ：:，,;；"),
            "单位": str(values.get("unit") or "").strip(),
            "工程量": quantity,
            "单价": unit_price,
            "合价": amount or quantity * unit_price,
            "资料类型": document_type,
            "来源位置": f"{document_name} / 文本第 {index} 行",
            "__document_name": document_name,
            "__document_type": document_type,
        })
    return rows


def _extract_rows_from_docx_tables(document: Any, document_type: str, document_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_index, table in enumerate(document.tables, 1):
        table_rows = table.rows
        if not table_rows:
            continue
        headers = [cell.text.strip() for cell in table_rows[0].cells]
        if not any(header for header in headers):
            continue
        for row_index, row in enumerate(table_rows[1:], 1):
            values = [cell.text.strip() for cell in row.cells]
            record = dict(zip(headers, values))
            if not (_first(record, ("项目编码", "清单编码", "编码", "item_code")) or _first(record, ("项目名称", "清单子目", "名称", "name"))):
                continue
            record.setdefault("资料类型", document_type)
            record.setdefault("__document_name", document_name)
            record.setdefault("__document_type", document_type)
            record.setdefault("来源位置", f"{document_name} / 表 {table_index} 第 {row_index} 行")
            rows.append(record)
    return rows


def _extract_text_payload(filename: str, payload: bytes) -> tuple[str, list[str], list[dict[str, Any]]]:
    suffix = Path(filename).suffix.lower()
    warnings: list[str] = []
    table_rows: list[dict[str, Any]] = []
    if suffix == ".txt":
        return _decode_text(payload), warnings, table_rows
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(payload)).pages)
            return text, warnings, table_rows
        except ImportError:
            warnings.append("当前运行环境未安装 PDF 文本提取依赖，已接收文件但未抽取字段。")
        except Exception as exc:
            warnings.append(f"PDF 解析失败：{exc}")
        return "", warnings, table_rows
    if suffix == ".docx":
        try:
            from docx import Document
            document = Document(io.BytesIO(payload))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            table_rows = _extract_rows_from_docx_tables(document, classify_document_name(filename), Path(filename).name)
            return text, warnings, table_rows
        except ImportError:
            warnings.append("当前运行环境未安装 DOCX 文本提取依赖，已接收文件但未抽取字段。")
        except Exception as exc:
            warnings.append(f"DOCX 解析失败：{exc}")
    return "", warnings, table_rows


def parse_uploaded_file(path: str | Path) -> dict[str, Any]:
    """Extract a small, auditable preview from common settlement file types."""
    file_path = Path(path)
    payload = file_path.read_bytes()
    suffix = file_path.suffix.lower()
    result = {
        "name": file_path.name,
        "type": suffix.lstrip("."),
        "document_type": classify_document_name(file_path.name),
        "fields": {},
        "rows": [],
        "warnings": [],
    }
    if suffix in {".json", ".csv", ".xlsx", ".xls"}:
        structured = structured_json_from_bytes(file_path.name, payload) if suffix == ".json" else None
        rows = _tabular_rows_from_bytes(file_path.name, payload)
        result["rows"] = rows[:100]
        result["fields"] = {"row_count": len(rows), "columns": list(rows[0].keys()) if rows else []}
        if structured:
            result["structured_data"] = structured
            result["fields"]["schema"] = "settlement_demo.line_items"
        if not rows:
            result["warnings"].append("未识别到可结构化行，请检查表头或文件格式。")
        return result
    text = ""
    if suffix in {".pdf", ".docx", ".txt"}:
        text, warnings, table_rows = _extract_text_payload(file_path.name, payload)
        rows = table_rows or _extract_text_rows(text, result["document_type"], file_path.name)
        result["rows"] = rows[:100]
        result["fields"] = _extract_text_fields(text)
        result["fields"].update({
            "text_length": len(text),
            "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
        })
        result["warnings"].extend(warnings)
        if not text:
            result["warnings"].append("未提取到文本字段，扫描件需要 OCR 适配后再入库。")
        elif not rows:
            result["warnings"].append("已抽取文本但未识别到标准清单行，可补充 OCR 或表头映射。")
        return result
    if suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            children = [name for name in archive.namelist() if not name.endswith("/")]
            child_previews = []
            merged_rows: list[dict[str, Any]] = []
            structured_data = None
            for child_name in children[:30]:
                try:
                    child_bytes = archive.read(child_name)
                except Exception:
                    continue
                child_preview = _preview_zip_member(child_name, child_bytes)
                child_previews.append(child_preview)
                merged_rows.extend(child_preview.get("rows", []))
                if structured_data is None and child_preview.get("structured_data"):
                    structured_data = child_preview["structured_data"]
        result["fields"] = {"file_count": len(children), "children": children[:30], "row_count": len(merged_rows)}
        result["rows"] = merged_rows[:100]
        result["child_previews"] = child_previews
        if structured_data:
            result["structured_data"] = structured_data
            result["fields"]["schema"] = "settlement_demo.line_items"
        result["warnings"].append("资料包已接收，子文件已按类型展开预览。")
        return result
    else:
        result["warnings"].append("文件已接收，当前 Demo 仅对 PDF、DOCX、Excel、CSV、JSON 和 ZIP 做字段预览。")
    return result
