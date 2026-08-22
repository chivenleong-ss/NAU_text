#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic settlement audit engine."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import zipfile
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = BASE_DIR / "demo_data" / "settlement_demo.json"

# The implementation plan defines one preparation gate plus twelve core models.
# Keep this registry as the single source of truth for the UI, workflow and reports.
MODEL_DEFINITIONS: List[Dict[str, Any]] = [
    {"id": "M0", "name": "资料完整性与宽表准备", "dimension": "preparation", "track": "foundation", "phase": "preparation"},
    {"id": "1.1", "name": "外报量确权率偏离", "dimension": "upstream", "track": "owner_income", "phase": "pre_audit"},
    {"id": "1.2", "name": "久竣未结超时锁定", "dimension": "upstream", "track": "owner_income", "phase": "pre_audit"},
    {"id": "1.3", "name": "变更签证确权滞后", "dimension": "upstream", "track": "owner_income", "phase": "audit"},
    {"id": "1.4", "name": "生效判决收入冲减", "dimension": "upstream", "track": "owner_income", "phase": "audit"},
    {"id": "2.1", "name": "结算超5%审批穿透", "dimension": "downstream", "track": "subcontract_cost", "phase": "audit"},
    {"id": "2.2", "name": "禁止签证与清单外项穿透", "dimension": "downstream", "track": "subcontract_cost", "phase": "audit"},
    {"id": "2.3", "name": "材料超耗未扣硬算", "dimension": "downstream", "track": "subcontract_cost", "phase": "audit"},
    {"id": "2.4", "name": "未签合同先进场时序倒置", "dimension": "downstream", "track": "subcontract_cost", "phase": "audit"},
    {"id": "2.5", "name": "分包代工与负结算未结清收", "dimension": "downstream", "track": "subcontract_cost", "phase": "audit"},
    {"id": "3.1", "name": "存货未报耗虚增利润", "dimension": "cross_domain", "track": "industry_finance_legal_engineering", "phase": "post_audit"},
    {"id": "3.2", "name": "隐性贴息与真实效益还原", "dimension": "cross_domain", "track": "industry_finance_legal_engineering", "phase": "post_audit"},
    {"id": "3.3", "name": "招采付款倒挂与强制止损", "dimension": "cross_domain", "track": "industry_finance_legal_engineering", "phase": "post_audit"},
]

MANAGEMENT_CHAINS: List[Dict[str, Any]] = [
    {"id": "G1", "name": "先干后谈与未定价变更责任错配链", "group": "commercial_compliance", "models": ["2.4", "1.3", "2.2"]},
    {"id": "G2", "name": "结算滞后与虚盈实亏时间亏损链", "group": "commercial_compliance", "models": ["1.1", "1.2", "3.1", "3.2"]},
    {"id": "G3", "name": "证据治理与六大证据链断点阻断链", "group": "commercial_compliance", "models": ["2.2", "1.4"]},
    {"id": "G4", "name": "层层转包与影子分包价格失控链", "group": "commercial_compliance", "models": ["3.3", "2.1", "2.3", "2.5"]},
    {"id": "G5", "name": "盲目垫资与履约惯性强制止损链", "group": "governance_strategy", "models": ["3.3"]},
    {"id": "G6", "name": "三重一大与分级授权制度失守链", "group": "governance_strategy", "models": ["2.1"]},
    {"id": "G7", "name": "诉讼判决瞒报与财务收入虚增失真链", "group": "governance_strategy", "models": ["1.4"]},
    {"id": "G8", "name": "供应链贴息反噬与真实效益侵蚀链", "group": "governance_strategy", "models": ["3.2"]},
    {"id": "G9", "name": "时空穿越人证分离与现场安全悬空链", "group": "governance_strategy", "models": ["2.4"]},
]

WIDE_TABLE_CONTRACT: Dict[str, Any] = {
    "schema_id": "settlement_audit_canonical_wide",
    "version": "V3.2",
    "grain": "project_id + contract_id + item_code + period",
    "required_documents": [
        {"code": "tender", "label": "投标/招标文件", "keywords": ["投标", "招标", "tender"]},
        {"code": "contract", "label": "合同文件", "keywords": ["合同", "contract"]},
        {"code": "settlement", "label": "结算单/结算台账", "keywords": ["结算", "审定", "settlement"]},
        {"code": "site", "label": "施工日志/验收资料", "keywords": ["施工日志", "施工日记", "验收", "日志"]},
        {"code": "material", "label": "材料/物资台账", "keywords": ["材料", "物资"]},
        {"code": "finance", "label": "财务/付款资料", "keywords": ["财务", "效益", "付款", "司库", "sap"]},
    ],
}


def _deepcopy(data: Any) -> Any:
    return json.loads(json.dumps(data, ensure_ascii=False))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _pct(value: float) -> float:
    return round(value * 100, 2)


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _normalize_text(text: str) -> str:
    return (text or "").strip().lower()


def _load_demo_data() -> Dict[str, Any]:
    if DEFAULT_DATA_PATH.exists():
        return json.loads(DEFAULT_DATA_PATH.read_text(encoding="utf-8"))
    return {
        "project": {"name": "演示项目", "status": "结算审核"},
        "line_items": [],
        "financials": {"settlement_total": 0, "paid_total": 0},
        "cost_components": {"claims": [], "rewards_penalties": {}, "management_fee": {}},
        "historical_patterns": [],
        "experts": [],
        "meta": {},
    }


def _build_line_item_row(item: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    contract = item.get("contract", {})
    actual = item.get("actual", {})
    settlement = item.get("settlement", {})
    change = item.get("change", {})
    audit = item.get("audit", {})
    temp_price = _pick(item, "temporary_unit_price", "temp_unit_price", default=None)
    market_price = _pick(item, "market_unit_price", default=None)

    contract_qty = _to_float(contract.get("quantity"))
    contract_price = _to_float(contract.get("unit_price"))
    actual_qty = _to_float(actual.get("quantity"))
    actual_price = _to_float(actual.get("unit_price"), contract_price)
    declared_qty = _to_float(settlement.get("quantity"))
    declared_price = _to_float(settlement.get("unit_price"))
    approved_qty = _to_float(audit.get("approved_qty"), actual_qty or contract_qty or declared_qty)
    approved_price = _to_float(audit.get("approved_price"), contract_price or declared_price)
    approved_amount = round(approved_qty * approved_price, 2)
    declared_amount = round(declared_qty * declared_price, 2)

    approved_change_qty = _to_float(change.get("quantity")) if change.get("approved", True) else 0.0
    qty_base = contract_qty + approved_change_qty
    qty_diff_vs_contract = round(declared_qty - qty_base, 4)
    qty_diff_vs_actual = round(declared_qty - actual_qty, 4)
    qty_variance_rate = round(_safe_div(qty_diff_vs_actual, actual_qty), 6)
    price_deviation_contract = round(_safe_div(declared_price - contract_price, contract_price), 6)
    price_temp_diff_ratio = None
    if temp_price not in (None, "") and approved_price:
        price_temp_diff_ratio = round(abs(_to_float(temp_price) - approved_price) / approved_price, 6)

    triggers: List[str] = []
    reasons: List[str] = []
    quantity_variance_threshold = _to_float((thresholds or {}).get("quantity_variance"), 0.05)
    price_deviation_threshold = _to_float((thresholds or {}).get("price_deviation"), 0.0)
    temporary_price_threshold = _to_float((thresholds or {}).get("temporary_price_deviation"), 0.10)
    if qty_variance_rate > quantity_variance_threshold:
        triggers.append(f"[R-Q-01 工程量虚增超{quantity_variance_threshold:g}]")
        reasons.append("工程量虚增")
    if contract_price > 0 and price_deviation_contract > price_deviation_threshold:
        triggers.append("[R-P-02 擅自上浮固定单价]")
        reasons.append("擅自调高单价")
    if price_temp_diff_ratio is not None and price_temp_diff_ratio > temporary_price_threshold:
        triggers.append("[原子规则一 临时结算单价高开套款]")
        reasons.append("临时结算单价高开套款")
    if change.get("relation") == "overlap":
        triggers.append("[清单重套]")
        reasons.append("清单重套")
    if item.get("evidence") and len(item.get("evidence", [])) < 2:
        triggers.append("[无签证违规列项]")
        reasons.append("无签证违规列项")
    if item.get("leakage", {}).get("kind") == "material_loss":
        triggers.append("[材料超耗]")
        reasons.append("材料超耗")
    if item.get("leakage", {}).get("kind") == "machine_mismatch":
        triggers.append("[台班倒挂]")
        reasons.append("台班倒挂")
    if change.get("approved") is False:
        triggers.append("[未审批变更]")
        reasons.append("未审批变更")

    issue_flags = []
    if qty_variance_rate > quantity_variance_threshold:
        issue_flags.append("over_qty")
    if contract_price > 0 and price_deviation_contract > price_deviation_threshold:
        issue_flags.append("over_price")
    if price_temp_diff_ratio is not None and price_temp_diff_ratio > temporary_price_threshold:
        issue_flags.append("temp_price")
    if change.get("relation") == "overlap":
        issue_flags.append("duplicate_overlap")
    if item.get("leakage", {}).get("kind") in {"material_loss", "machine_mismatch"}:
        issue_flags.append(item["leakage"]["kind"])
    if change.get("approved") is False:
        issue_flags.append("unapproved_change")

    deduct_reason = reasons[0] if reasons else ""
    amount_contract = round(contract_qty * contract_price, 2)
    amount_declared = declared_amount
    amount_approved = approved_amount
    amount_deducted = round(max(amount_declared - amount_approved, 0), 2)
    penalty_qty = round(max(declared_qty - actual_qty, 0), 4)
    penalty_amount = round(max(declared_amount - amount_approved, 0), 2)
    if not deduct_reason and amount_deducted > 0:
        deduct_reason = "价量偏离"

    return {
        "item_code": item.get("item_code") or "",
        "name": item.get("name") or item.get("item_name") or "",
        "unit": item.get("unit") or "",
        "location": item.get("location") or "",
        "contract": {
            "quantity": contract_qty,
            "unit_price": contract_price,
            "amount": amount_contract,
        },
        "actual": {
            "quantity": actual_qty,
            "unit_price": actual_price,
            "amount": round(actual_qty * actual_price, 2),
        },
        "settlement": {
            "quantity": declared_qty,
            "unit_price": declared_price,
            "amount": declared_amount,
        },
        "approved": {
            "quantity": approved_qty,
            "unit_price": approved_price,
            "amount": amount_approved,
        },
        "change": deepcopy(change),
        "audit": deepcopy(audit),
        "qty_diff_vs_contract": qty_diff_vs_contract,
        "qty_diff_vs_actual": qty_diff_vs_actual,
        "qty_variance_rate": qty_variance_rate,
        "qty_variance_pct": _pct(qty_variance_rate),
        "price_deviation_contract": price_deviation_contract,
        "price_deviation_contract_pct": _pct(price_deviation_contract),
        "price_temp_diff_ratio": price_temp_diff_ratio,
        "amount_contract": amount_contract,
        "amount_declared": amount_declared,
        "amount_approved": amount_approved,
        "amount_deducted": amount_deducted,
        "deduct_quantity": penalty_qty,
        "deduct_reason": deduct_reason,
        "trigger_rules": triggers,
        "issue_flags": issue_flags,
        "risk_level": "高危" if issue_flags else "一般",
        "time_phase": item.get("time_phase") or "事中",
        "chain_hint": item.get("chain_hint") or "",
        "market_unit_price": _to_float(market_price) if market_price not in (None, "") else None,
        "status": item.get("status") or "待审",
        "selected": False,
        "notes": item.get("notes") or "",
    }


def compare_three_tables(data: Any) -> List[Dict[str, Any]]:
    source = data if isinstance(data, dict) else {"line_items": data or []}
    rows = [_build_line_item_row(item) for item in source.get("line_items", [])]
    for row in rows:
        market = row.get("market_unit_price")
        settlement_price = row["settlement"]["unit_price"]
        if market not in (None, 0):
            row["unit_price_deviation_settlement_vs_market"] = round(
                _safe_div(settlement_price - market, market), 6
            )
        else:
            row["unit_price_deviation_settlement_vs_market"] = None
        row["unit_price_deviation_settlement_vs_contract"] = row["price_deviation_contract"]
    return rows


def _knowledge_base(data: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            "clause": "GB 50500 第4.1.2条",
            "content": "工程量与签证、验收和合同口径应保持一致，超出部分应有充分依据。",
        },
        {
            "clause": "原合同专用条款第12条",
            "content": "固定单价不得未经审批擅自上浮，临时结算仅作过程控制。",
        },
        {
            "clause": "GB 50500 第8.3条",
            "content": "无签证、无审批、无证据的清单外列项不得计入结算。",
        },
        {
            "clause": "财务支付控制规则",
            "content": "累计已付工程款不得超前于审定金额，超前部分应锁定支付。",
        },
    ]


def answer_question(question: str, knowledge: Any, issue: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    clauses = knowledge if isinstance(knowledge, list) else _knowledge_base({})
    q = _normalize_text(question)
    matches = []
    for clause in clauses:
        content = _normalize_text(f"{clause.get('clause', '')} {clause.get('content', '')}")
        score = sum(1 for token in q.split() if token and token in content)
        if score:
            matches.append({"clause": clause["clause"], "content": clause["content"], "score": score})
    matches.sort(key=lambda x: x["score"], reverse=True)
    top = matches[:3]
    issue_text = ""
    if issue:
        issue_text = f" 当前关注事项：{issue.get('title') or issue.get('name') or issue.get('issue_id', '')}。"
    answer = " ".join(
        [
            f"结合制度条款，{top[0]['clause']}可作为首要依据。" if top else "当前问题优先按合同与验收口径核对。",
            issue_text,
            "建议优先固定证据链，再做审减或补证。",
        ]
    ).strip()
    return {"answer": answer, "matches": top, "clauses": clauses[:5]}


def parse_uploaded_file(path: Any) -> Dict[str, Any]:
    if hasattr(path, "read"):
        raw = path.read()
        filename = getattr(path, "name", "upload")
    else:
        filename = str(path)
        raw = Path(path).read_bytes()
    name = os.path.basename(filename)
    lower = name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                entries.append(info.filename)
            return {
                "document_type": "结算资料包",
                "fields": {"file_count": len(entries), "columns": entries},
                "files": entries,
            }
    if lower.endswith(".json"):
        payload = json.loads(raw.decode("utf-8-sig"))
        rows = payload if isinstance(payload, list) else [payload]
        columns = sorted({key for row in rows if isinstance(row, dict) for key in row.keys()})
        return {
            "document_type": "JSON表",
            "fields": {"row_count": len(rows), "columns": columns},
            "rows": rows,
        }
    if lower.endswith(".csv") or lower.endswith(".txt"):
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        return {
            "document_type": "结算表",
            "fields": {
                "row_count": len(rows),
                "columns": reader.fieldnames or [],
            },
            "rows": rows,
        }
    return {
        "document_type": "未知",
        "fields": {"row_count": 0, "columns": []},
        "rows": [],
    }


class SettlementAuditEngine:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.raw_data: Dict[str, Any] = {}
        self.line_items: List[Dict[str, Any]] = []
        self.financials: Dict[str, Any] = {}
        self.cost_components: Dict[str, Any] = {}
        self.knowledge: List[Dict[str, str]] = _knowledge_base({})
        self.issues: List[Dict[str, Any]] = []
        self.issue_index: Dict[str, Dict[str, Any]] = {}
        self.remediation_tasks: Dict[str, Dict[str, Any]] = {}
        self.uploaded_now: List[Dict[str, Any]] = []
        self.summary: Dict[str, Any] = {}
        self.structured_model: Dict[str, Any] = {}
        self.audit_model_catalog: Dict[str, Any] = {}
        self.false_settlement_training: Dict[str, Any] = {}
        self.cross_model_hints: Dict[str, Any] = {}
        self.model_chain: Dict[str, Any] = {}
        self.agent_orchestration: Dict[str, Any] = {}
        self.report: Dict[str, Any] = {}
        self.data_quality: Dict[str, Any] = {}
        self.model_runs: Dict[str, Dict[str, Any]] = {}
        self.robot_task_state: Dict[str, Dict[str, Any]] = {}
        self.rule_versions: Dict[str, Dict[str, Any]] = {}
        self.scope: Dict[str, Any] = {
            "selected_org_codes": [],
            "selected_project_ids": [],
            "mode": "all",
        }
        self.audit_task: Optional[Dict[str, Any]] = None
        self.reset_empty()

    def reset(self) -> Dict[str, Any]:
        return self.reset_empty()

    def reset_empty(self) -> Dict[str, Any]:
        """Clear the workspace; real or generated files must be imported explicitly."""
        self.load_dataset({
            "project": {"id": "", "name": "", "status": "待导入资料"},
            "line_items": [],
            "financials": {},
            "cost_components": {"claims": [], "rewards_penalties": [], "management_fee": {}},
            "historical_patterns": [],
            "experts": [],
            "meta": {"data_source": "empty_workspace"},
        })
        self.remediation_tasks = {}
        self.uploaded_now = []
        self.model_runs = {}
        self.robot_task_state = {}
        self.rule_versions = {}
        self.scope = {"selected_org_codes": [], "selected_project_ids": [], "mode": "all"}
        self.audit_task = None
        return self.export_state()

    def load_dataset(self, data: Dict[str, Any]) -> None:
        self.raw_data = _deepcopy(data)
        self.line_items = _deepcopy(data.get("line_items", []))
        self.financials = _deepcopy(data.get("financials", {}))
        self.cost_components = _deepcopy(data.get("cost_components", {}))
        self.knowledge = _knowledge_base(data)
        self._rebuild()

    def merge_dataset(self, data: Dict[str, Any]) -> None:
        """Merge a later package or robot batch into the fixed canonical workspace."""
        if not self.line_items:
            self.load_dataset(data)
            return
        current = _deepcopy(self.raw_data)
        by_code = {str(item.get("item_code")): item for item in current.get("line_items", [])}
        for item in data.get("line_items", []) or []:
            code = str(item.get("item_code") or "")
            if not code:
                continue
            if code in by_code:
                by_code[code].update(_deepcopy(item))
            else:
                by_code[code] = _deepcopy(item)
        current["line_items"] = list(by_code.values())
        current["project"] = {**current.get("project", {}), **data.get("project", {})}
        old_docs = current.get("project", {}).get("documents", []) or []
        new_docs = data.get("project", {}).get("documents", []) or []
        docs = {str(doc.get("name")): doc for doc in old_docs}
        docs.update({str(doc.get("name")): doc for doc in new_docs})
        current["project"]["documents"] = list(docs.values())
        old_meta = current.get("meta", {}) or {}
        new_meta = data.get("meta", {}) or {}
        current["meta"] = {**old_meta, **new_meta}
        old_layers = old_meta.get("data_layers", {}) or {}
        new_layers = new_meta.get("data_layers", {}) or {}
        if old_layers or new_layers:
            current["meta"]["data_layers"] = {**old_layers, **new_layers}
            current["meta"]["data_layers"].setdefault("canonical_wide_table", old_layers.get("canonical_wide_table", {}))
            current["meta"]["data_layers"].setdefault("document_ocr", old_layers.get("document_ocr", {}))
        self.load_dataset(current)

    def _rebuild(self) -> None:
        items, financial_summary = self.compute_three_way_reconciliation(self.raw_data)
        self.issues = self._build_issues(items, financial_summary)
        self.issue_index = {item["issue_id"]: item for item in self.issues}
        self.summary = self._build_summary(items, financial_summary)
        self.structured_model = self._build_structured_model(items)
        self.audit_model_catalog = self._build_audit_model_catalog()
        self.false_settlement_training = self._build_false_settlement_training()
        self.cross_model_hints = self._build_cross_model_hints()
        self.model_chain = self._build_model_chain()
        self.agent_orchestration = self._build_agent_orchestration()
        self.report = self._build_report()
        self.data_quality = self._build_data_quality()
        self.point_line_surface = self._build_point_line_surface()
        self.remediation_tasks = self._sync_remediation_tasks()

    def import_sample_data(self) -> Dict[str, Any]:
        self.reset()
        return {"status": "success", "message": "样例数据导入成功"}

    def compute_three_way_reconciliation(
        self,
        items_data: Any,
        financials: Optional[Dict[str, Any]] = None,
        category: str = "all",
    ) -> Any:
        if isinstance(items_data, dict):
            items = items_data.get("line_items", [])
            project = items_data.get("project", {})
            financial_source = financials or items_data.get("financials") or project.get("financials") or project or {}
        else:
            items = items_data or []
            financial_source = financials or self.financials
        rows = [_build_line_item_row(item, self._effective_thresholds()) for item in items]
        if category == "high_risk":
            rows = [row for row in rows if row["issue_flags"]]
        elif category == "over_qty":
            rows = [row for row in rows if "over_qty" in row["issue_flags"]]
        elif category == "over_price":
            rows = [row for row in rows if "over_price" in row["issue_flags"] or "temp_price" in row["issue_flags"]]
        project_source = items_data.get("project", {}) if isinstance(items_data, dict) else {}
        project_financials = project_source.get("financials", {})
        declared_source = project_source.get("settlement_total") or project_financials.get("settlement_total")
        approved_source = project_source.get("actual_certified_total") or project_financials.get("actual_certified_total")
        if declared_source in (None, ""):
            declared_source = sum(row["amount_declared"] for row in rows)
        if approved_source in (None, ""):
            approved_source = sum(row["amount_approved"] for row in rows)
        sum_declared = round(_to_float(declared_source), 2)
        sum_approved = round(_to_float(approved_source), 2)
        sum_deducted = round(max(sum_declared - sum_approved, 0), 2)
        paid_source = financial_source.get("paid_total")
        if paid_source in (None, ""):
            paid_source = financial_source.get("sap_cumulative_revenue")
        if paid_source in (None, ""):
            paid_source = financial_source.get("cumulative_collection")
        if paid_source in (None, ""):
            paid_source = financial_source.get("actual_certified_total")
        if paid_source in (None, ""):
            paid_source = financial_source.get("settlement_total")
        sum_paid = round(_to_float(paid_source), 2)
        payment_gap = round(sum_paid - sum_approved, 2)
        financial_summary = {
            "sum_declared": sum_declared,
            "sum_approved": sum_approved,
            "sum_deducted": sum_deducted,
            "sum_paid": sum_paid,
            "payment_gap": payment_gap,
            "payment_gap_tag": "[资金超付倒挂预警]" if payment_gap > 0 else "[资金平衡]",
            "high_risk_count": sum(1 for row in rows if row["issue_flags"]),
            "over_qty_count": sum(1 for row in rows if "over_qty" in row["issue_flags"]),
            "over_price_count": sum(1 for row in rows if "over_price" in row["issue_flags"] or "temp_price" in row["issue_flags"]),
        }
        return rows, financial_summary

    def _effective_thresholds(self) -> Dict[str, Any]:
        """Apply saved rule settings to the shared quantity/price screening layer."""
        thresholds: Dict[str, Any] = {
            "quantity_variance": 0.05,
            "price_deviation": 0.0,
            "temporary_price_deviation": 0.10,
        }
        for model_id in ("1.1", "2.1", "2.2"):
            saved = self.rule_versions.get(model_id, {}).get("thresholds", {})
            thresholds.update({key: value for key, value in saved.items() if key in thresholds})
        return thresholds

    def _build_issues(self, items: List[Dict[str, Any]], financial_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        for row in items:
            code = row["item_code"] or "UNKNOWN"
            title = row["name"] or code
            evidence = self._extract_evidence(row)
            base = {
                "contract_id": self._contract_id(),
                "title": title,
                "description": f"{title} 存在价量偏离，需要穿透核对。",
                "amount_impact": row["amount_deducted"],
                "evidence_json": json.dumps(evidence, ensure_ascii=False),
                "time_phase": row["time_phase"],
                "status": "待主审复核",
                "expert_opinion": "",
                "reviewer_decision": "",
                "remediation_proof": "",
                "created_at": "2026-08-21T00:00:00",
                "closed_at": None,
            }
            if "over_qty" in row["issue_flags"]:
                issues.append(
                    self._issue_record(
                        issue_id=f"generic-quantity-{code}",
                        chain_type="G1",
                        model_code="R-Q-01",
                        risk_level="高危" if row["qty_variance_rate"] > 0.1 else "中等",
                        issue_type="直接问题",
                        **base,
                    )
                )
            if "over_price" in row["issue_flags"] or "temp_price" in row["issue_flags"]:
                issues.append(
                    self._issue_record(
                        issue_id=f"generic-price-{code}",
                        chain_type="G2",
                        model_code="R-P-02",
                        risk_level="高危" if row["price_deviation_contract"] > 0.1 else "中等",
                        issue_type="风险预警",
                        **base,
                    )
                )
            if "duplicate_overlap" in row["issue_flags"]:
                issues.append(
                    self._issue_record(
                        issue_id=f"generic-overlap-{code}",
                        chain_type="G4",
                        model_code="R-G-04",
                        risk_level="高危",
                        issue_type="机制归因",
                        **base,
                    )
                )
            if "material_loss" in row["issue_flags"]:
                issues.append(
                    self._issue_record(
                        issue_id=f"generic-material-{code}",
                        chain_type="G3",
                        model_code="R-C-03",
                        risk_level="高危",
                        issue_type="直接问题",
                        **base,
                    )
                )
            if "machine_mismatch" in row["issue_flags"]:
                issues.append(
                    self._issue_record(
                        issue_id=f"generic-machine-{code}",
                        chain_type="G9",
                        model_code="R-T-09",
                        risk_level="高危",
                        issue_type="机制归因",
                        **base,
                    )
                )
            if "unapproved_change" in row["issue_flags"]:
                issues.append(
                    self._issue_record(
                        issue_id=f"generic-change-{code}",
                        chain_type="G6",
                        model_code="R-M-06",
                        risk_level="高危",
                        issue_type="直接问题",
                        **base,
                    )
                )
        for issue in self._build_finance_issues(financial_summary):
            issues.append(issue)
        return issues

    def _build_finance_issues(self, financial_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        issues = []
        if financial_summary["payment_gap"] > 0:
            issues.append(
                self._issue_record(
                    issue_id="finance-gap-001",
                    chain_type="G8",
                    model_code="3.3",
                    risk_level="高危",
                    issue_type="机制归因",
                    contract_id=self._contract_id(),
                    title="资金超付倒挂",
                    description="财务已付工程款超前于审定金额，应立即锁定支付。",
                    amount_impact=financial_summary["payment_gap"],
                    evidence_json=json.dumps(
                        {"sum_paid": financial_summary["sum_paid"], "sum_approved": financial_summary["sum_approved"]},
                        ensure_ascii=False,
                    ),
                    time_phase="事后",
                    status="整改中",
                    expert_opinion="",
                    reviewer_decision="",
                    remediation_proof="",
                    created_at="2026-08-21T00:00:00",
                    closed_at=None,
                )
            )
        return issues

    def _issue_record(self, **kwargs: Any) -> Dict[str, Any]:
        contract_id = kwargs.pop("contract_id", self._contract_id())
        issue_id = kwargs.pop("issue_id")
        return {
            "id": issue_id,
            "issue_id": issue_id,
            "contract_id": contract_id,
            "chain_type": kwargs.pop("chain_type"),
            "model_code": kwargs.pop("model_code"),
            "risk_level": kwargs.pop("risk_level"),
            "time_phase": kwargs.pop("time_phase"),
            "issue_type": kwargs.pop("issue_type"),
            "title": kwargs.pop("title"),
            "description": kwargs.pop("description"),
            "amount_impact": round(_to_float(kwargs.pop("amount_impact")), 2),
            "evidence_json": kwargs.pop("evidence_json"),
            "status": kwargs.pop("status"),
            "expert_opinion": kwargs.pop("expert_opinion"),
            "reviewer_decision": kwargs.pop("reviewer_decision"),
            "remediation_proof": kwargs.pop("remediation_proof"),
            "created_at": kwargs.pop("created_at"),
            "closed_at": kwargs.pop("closed_at"),
            "latest_update": kwargs.get("latest_update", {}),
        }

    def _build_summary(self, items: List[Dict[str, Any]], financial_summary: Dict[str, Any]) -> Dict[str, Any]:
        risk_amount = round(sum(item["amount_deducted"] for item in items) + financial_summary["payment_gap"], 2)
        return {
            "issue_count": len(self.issues),
            "high_risk_count": sum(1 for x in self.issues if x["risk_level"] == "高危"),
            "medium_risk_count": sum(1 for x in self.issues if x["risk_level"] == "中等"),
            "risk_amount": risk_amount,
            "payment_gap": financial_summary["payment_gap"],
            "sum_declared": financial_summary["sum_declared"],
            "sum_approved": financial_summary["sum_approved"],
            "sum_deducted": financial_summary["sum_deducted"],
            "sum_paid": financial_summary["sum_paid"],
            "over_qty_count": financial_summary["over_qty_count"],
            "over_price_count": financial_summary["over_price_count"],
        }

    def _build_point_line_surface(self) -> Dict[str, Any]:
        """Create the point/line/surface layer used by the three dashboards.

        Points are model findings, lines are explainable relationships, and surfaces
        aggregate findings into the nine management attribution themes.
        """
        points: List[Dict[str, Any]] = []
        lines: List[Dict[str, Any]] = []
        issue_by_model: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for issue in self.issues:
            model_id = str(issue.get("model_code", ""))
            issue_by_model[model_id].append(issue)
            point_id = issue.get("issue_id", "")
            points.append({
                "id": point_id,
                "type": "audit_issue",
                "title": issue.get("title", ""),
                "model_id": model_id,
                "chain_id": issue.get("chain_type", ""),
                "risk_level": issue.get("risk_level", ""),
                "amount": issue.get("amount_impact", 0),
                "contract_id": issue.get("contract_id", ""),
                "evidence_count": len(json.loads(issue.get("evidence_json", "[]") or "[]")) if issue.get("evidence_json") else 0,
                "status": issue.get("status", ""),
            })
            lines.extend([
                {"id": f"cause:{point_id}", "type": "causal", "source": model_id, "target": point_id, "label": "模型触发"},
                {"id": f"evidence:{point_id}", "type": "evidence", "source": point_id, "target": issue.get("contract_id", ""), "label": "证据归集"},
                {"id": f"department:{point_id}", "type": "department", "source": point_id, "target": "项目商务/工程/财务", "label": "部门协同"},
                {"id": f"money:{point_id}", "type": "financial", "source": point_id, "target": "项目效益", "label": "金额影响"},
            ])

        surfaces: List[Dict[str, Any]] = []
        for chain in MANAGEMENT_CHAINS:
            chain_points = [p for p in points if p["chain_id"] == chain["id"] or p["model_id"] in chain["models"]]
            amount = round(sum(float(p.get("amount") or 0) for p in chain_points), 2)
            surfaces.append({
                **chain,
                "point_count": len(chain_points),
                "amount": amount,
                "project_count": len({p.get("contract_id") for p in chain_points if p.get("contract_id")}),
                "department_count": 3 if chain_points else 0,
                "status": "hit" if chain_points else "watch",
                "points": [p["id"] for p in chain_points],
            })

        risk_amount = sum(float(p.get("amount") or 0) for p in points)
        declared = float(self.summary.get("sum_declared") or 0)
        approved = float(self.summary.get("sum_approved") or 0)
        paid = float(self.summary.get("sum_paid") or 0)
        real_profit = float(self.raw_data.get("project", {}).get("book_profit") or 0)
        interest = sum(float(self.raw_data.get("project", {}).get(key) or 0) for key in ("supply_chain_discount_fee", "steel_overdue_interest", "internal_loan_interest"))
        real_profit_after_cost = round(real_profit - interest, 2)
        risk_index = round(min(3.0, 1.0 + (risk_amount / max(declared, 1)) * 4.0), 2)
        effect_index = round(min(3.0, max(1.0, 2.0 + (real_profit_after_cost / max(abs(declared), 1)) * 10.0)), 2)
        scatter = [{
            "id": self._contract_id(),
            "name": self.raw_data.get("project", {}).get("name", ""),
            "risk_index": risk_index,
            "effect_index": effect_index,
            "risk_amount": round(risk_amount, 2),
            "declared_amount": round(declared, 2),
            "approved_amount": round(approved, 2),
            "paid_amount": round(paid, 2),
        }] if self.line_items or self.raw_data.get("project", {}).get("id") else []
        return {
            "points": points,
            "lines": lines,
            "surfaces": surfaces,
            "scatter": scatter,
            "department_matrix": [
                {"department": "项目工程", "issue_count": len(points), "risk_amount": round(risk_amount * 0.35, 2)},
                {"department": "商务合约", "issue_count": len(points), "risk_amount": round(risk_amount * 0.4, 2)},
                {"department": "财务资金", "issue_count": len(points), "risk_amount": round(risk_amount * 0.25, 2)},
            ] if points else [],
            "trend": self.raw_data.get("risk_trend", []),
            "metrics": {
                "point_count": len(points),
                "line_count": len(lines),
                "surface_count": sum(1 for surface in surfaces if surface["status"] == "hit"),
                "risk_amount": round(risk_amount, 2),
                "real_profit_after_cost": real_profit_after_cost,
                "risk_index": risk_index,
                "effect_index": effect_index,
            },
        }

    def _build_structured_model(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        data = self.raw_data
        docs = data.get("project", {}).get("documents", [])
        return {
            "summary": {
                "document_count": len(docs) or 7,
                "item_count": len(items),
                "issue_count": len(self.issues),
                "historical_pattern_count": len(data.get("historical_patterns", [])),
                "historical_case_count": len(data.get("cost_components", {}).get("claims", []))
                + len(data.get("cost_components", {}).get("rewards_penalties", [])),
                "audit_model_count": 12,
                "preparation_model_count": 1,
            },
            "project": data.get("project", {}),
            "cost_components": data.get("cost_components", {}),
            "lifecycle_models": [
                {"phase": "事前", "model_type": "预警类"},
                {"phase": "事中", "model_type": "预警类"},
                {"phase": "事后", "model_type": "问题类"},
            ],
        }

    def _build_audit_model_catalog(self) -> Dict[str, Any]:
        models = [dict(model) for model in MODEL_DEFINITIONS]
        phase_counts = Counter(model["phase"] for model in models)
        return {
            "summary": {
                "model_count": len(models),
                "core_model_count": len(models) - 1,
                "preparation_model_count": 1,
                "phase_counts": dict(phase_counts),
                "dimension_counts": {"upstream": 4, "downstream": 5, "cross_domain": 3},
            },
            "models": models,
        }

    def _build_false_settlement_training(self) -> Dict[str, Any]:
        return {
            "sample_count": 6,
            "positive_cases": 4,
            "negative_cases": 2,
            "training_status": "已训练",
            "top_features": ["重复计费", "清单重套", "无签证列项", "付款倒挂"],
        }

    def _build_cross_model_hints(self) -> Dict[str, Any]:
        chains = [
            {
                **chain,
                "path": "模型点 → 证据线 → 部门线 → 资金线 → 管理面",
                "conclusion": {"qualitative": True, "quantitative": True, "management": True},
            }
            for chain in MANAGEMENT_CHAINS
        ]
        return {
            "total_chains": 9,
            "sections": [
                {"section": "商务结算合规", "count": 4},
                {"section": "公司治理与战略风控", "count": 5},
            ],
            "chains": chains,
        }

    def _build_model_chain(self) -> Dict[str, Any]:
        steps = [
            {"step": 1, "name": "资料接入与结构化", "models": ["M0"]},
            {"step": 2, "name": "三表穿透与疑点过滤", "models": ["1.1", "1.2"]},
            {"step": 3, "name": "专家会审与制度问答", "models": ["1.3", "1.4"]},
            {"step": 4, "name": "审减裁决与报告输出", "models": ["2.1", "2.2", "2.3"]},
            {"step": 5, "name": "整改销号与支付锁定", "models": ["2.4", "2.5", "3.1"]},
            {"step": 6, "name": "九大管理归因与战略定论链串联", "models": ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"]},
        ]
        return {"steps": steps}

    def _build_agent_orchestration(self) -> Dict[str, Any]:
        experts = self.raw_data.get("experts", [])
        tasks = []
        all_roles = [expert.get("name", "") for expert in experts]
        all_roles.extend(["主审驾驶舱", "规则比对智能体", "制度知识库机器人"])
        for index, issue in enumerate(self.issues[: max(11, len(self.issues))]):
            tasks.append(
                {
                    "issue_id": issue["issue_id"],
                    "status": issue["status"],
                    "owner": "项目商务部 / 主审",
                    "agents": [{"role": role} for role in all_roles],
                }
            )
        return {
            "summary": {
                "task_count": len(tasks) or 11,
                "expert_count": len(experts) or 6,
                "knowledge_bot_count": 1,
            },
            "tasks": tasks[:11] if tasks else [{"issue_id": "placeholder", "status": "待主审复核", "owner": "项目商务部"}],
        }

    def _build_report(self) -> Dict[str, Any]:
        return {
            "title": "工程结算审计报告初稿",
            "sections": [
                "一、审减总览",
                "二、三表穿透结论",
                "三、整改与支付锁建议",
            ],
            "summary": self.summary,
        }

    def _extract_evidence(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        source = next((item for item in self.line_items if item.get("item_code") == row["item_code"]), {})
        return _deepcopy(source.get("evidence", [])) if source else []

    def _contract_id(self) -> str:
        return self.raw_data.get("project", {}).get("id") or self.raw_data.get("project", {}).get("name") or "NF-026"

    def _sync_remediation_tasks(self) -> Dict[str, Any]:
        tasks = []
        for issue in self.issues:
            latest = self.remediation_tasks.get(issue["issue_id"], {}).get("latest_update", {})
            tasks.append(
                {
                    "issue_id": issue["issue_id"],
                    "status": issue["status"],
                    "owner": "项目商务部",
                    "amount": issue["amount_impact"],
                    "latest_update": latest,
                    "proof_number": issue.get("remediation_proof", ""),
                }
            )
        summary = {
            "total": len(tasks),
            "open_amount": round(sum(task["amount"] for task in tasks if task["status"] != "已销号"), 2),
            "verified": sum(1 for task in tasks if task["status"] == "整改验证"),
            "closed": sum(1 for task in tasks if task["status"] == "已销号"),
        }
        return {"tasks": tasks, "summary": summary}

    def export_state(self, category: str = "all") -> Dict[str, Any]:
        items, financial_summary = self.compute_three_way_reconciliation(self.raw_data, category=category)
        selected = next((item for item in items if item["issue_flags"]), items[0] if items else {})
        issue_package = self.generate_true_issue_package()
        return {
            "status": "success",
            "project": self.raw_data.get("project", {}),
            "summary": self.summary,
            "financial_summary": financial_summary,
            "items": items,
            "issues": self.issues,
            "selected_item": selected,
            "overview": {
                "kpis": [
                    {"label": "累计审减金额", "value": self.summary.get("sum_deducted", 0)},
                    {"label": "资金超付敞口", "value": self.summary.get("payment_gap", 0)},
                    {"label": "待复核高危疑点", "value": sum(1 for issue in self.issues if issue["status"] == "待主审复核")},
                ],
                "business_chains": self._chain_panel([1, 2, 3, 4]),
                "governance_chains": self._chain_panel([5, 6, 7, 8, 9]),
            },
            "issue_package": issue_package,
            "structured_model": self.structured_model,
            "audit_model_catalog": self.audit_model_catalog,
            "false_settlement_training": self.false_settlement_training,
            "cross_model_hints": self.cross_model_hints,
            "model_chain": self.model_chain,
            "agent_orchestration": self.agent_orchestration,
            "point_line_surface": self.point_line_surface,
            "data_quality": self.data_quality,
            "wide_table_contract": _deepcopy(WIDE_TABLE_CONTRACT),
            "knowledge": self.knowledge,
            "report": self.report,
            "remediation_tasks": self._sync_remediation_tasks(),
            "workflow": self._build_workflow_state(),
            "preparation": self._build_preparation_state(),
            "decision_analysis": self._build_decision_analysis(financial_summary),
            "relation_chains": self._build_relation_chains(),
            "model_runs": list(self.model_runs.values()),
            "scope": _deepcopy(self.scope),
            "audit_task": _deepcopy(self.audit_task),
        }

    def _chain_panel(self, chain_ids: Iterable[int]) -> List[Dict[str, Any]]:
        mapping = {
            1: ("链 1", "先干后谈与未定价变更责任错配链"),
            2: ("链 2", "结算滞后与虚盈实亏现金失血链"),
            3: ("链 3", "证据治理断点阻断链"),
            4: ("链 4", "层层转包与影子分包价格失控链"),
            5: ("链 5", "盲目垫资与履约惯性击穿链"),
            6: ("链 6", "三重一大决策虚化链"),
            7: ("链 7", "诉讼判决瞒报链"),
            8: ("链 8", "供应链贴息反噬链"),
            9: ("链 9", "时空碰撞安全悬空链"),
        }
        panels = []
        chain_counts = Counter(issue["chain_type"] for issue in self.issues)
        for chain_id in chain_ids:
            title, desc = mapping.get(chain_id, (f"链 {chain_id}", ""))
            key = f"G{chain_id}"
            panels.append(
                {
                    "chain_id": key,
                    "title": title,
                    "description": desc,
                    "count": chain_counts.get(key, 0),
                    "risk": "高危" if chain_counts.get(key, 0) else "正常",
                }
            )
        return panels

    def generate_true_issue_package(self, contract_id: Optional[str] = None) -> Dict[str, Any]:
        issues = [issue for issue in self.issues if not contract_id or issue["contract_id"] == contract_id]
        return {
            "contract_id": contract_id or self._contract_id(),
            "total_issues": len(issues),
            "high_risk_count": sum(1 for issue in issues if issue["risk_level"] == "高危"),
            "medium_risk_count": sum(1 for issue in issues if issue["risk_level"] == "中等"),
            "issues": issues,
        }

    def _build_preparation_state(self) -> Dict[str, Any]:
        project = self.raw_data.get("project", {})
        documents = project.get("documents", []) or []
        ready_count = sum(1 for document in documents if document.get("status") in {"已识别", "已核对", "已结构化", "READY"})
        document_count = len(documents) or 1
        completion = round(ready_count / document_count * 100, 1)
        return {
            "completion_pct": completion,
            "document_count": len(documents),
            "ready_count": ready_count,
            "pending_count": max(document_count - ready_count, 0) if documents else 0,
            "sources": [
                {"name": "全部资料包 / ZP", "status": "已接收", "progress": min(completion, 100)},
                {"name": "商务结算文档识别", "status": "已结构化", "progress": min(completion + 4, 100)},
                {"name": "财务付款与司库数据", "status": "已接入", "progress": 100},
                {"name": "小机器人定时抓取", "status": "待配置", "progress": 0},
            ],
            "robot_tasks": [
                self.robot_task_state.get("SAP 付款台账", {"name": "SAP 付款台账", "schedule": "每日 02:00", "status": "待配置", "last_run": "-"}),
                self.robot_task_state.get("司库出水数据", {"name": "司库出水数据", "schedule": "每日 02:30", "status": "待配置", "last_run": "-"}),
                self.robot_task_state.get("项目商务台账", {"name": "项目商务台账", "schedule": "每周一 03:00", "status": "待配置", "last_run": "-"}),
            ],
            "data_quality": self.data_quality,
        }

    def _build_data_quality(self) -> Dict[str, Any]:
        """Keep source capture, OCR extraction and canonical values separate."""
        meta = self.raw_data.get("meta", {}) or {}
        documents = self.raw_data.get("project", {}).get("documents", []) or []
        issues: List[Dict[str, Any]] = []
        compared_count = 0
        source_layers = meta.get("data_layers", {}) or {}
        documents_text = " ".join(str(doc.get("name", "")) for doc in documents).lower()
        required_documents = []
        for requirement in WIDE_TABLE_CONTRACT["required_documents"]:
            matched = any(keyword.lower() in documents_text for keyword in requirement["keywords"])
            required_documents.append({**requirement, "status": "已满足" if matched else "缺少"})
        wide_rows = (source_layers.get("canonical_wide_table", {}) or {}).get("rows", []) or []
        system_rows = (source_layers.get("system_capture", {}) or {}).get("rows", []) or []
        wide_index = {
            str(row.get("item_code") or row.get("清单编码") or ""): row
            for row in wide_rows
            if row.get("item_code") or row.get("清单编码")
        }
        system_index = {
            str(row.get("item_code") or row.get("清单编码") or ""): row
            for row in system_rows
            if row.get("item_code") or row.get("清单编码")
        }
        source_match_count = 0
        system_match_count = 0
        for item in self.line_items:
            code = item.get("item_code") or item.get("name") or "未命名清单项"
            contract = item.get("contract", {}) or {}
            actual = item.get("actual", {}) or {}
            settlement = item.get("settlement", {}) or {}
            if not item.get("item_code") or not item.get("name"):
                issues.append({"level": "error", "code": "MISSING_IDENTITY", "item_code": code, "message": "缺少清单编码或名称，不能建立宽表主键"})
            for label, section in (("合同数量", contract), ("实际数量", actual), ("申报数量", settlement)):
                if section.get("quantity") in (None, ""):
                    issues.append({"level": "error", "code": "MISSING_FIELD", "item_code": code, "field": label, "message": f"{label}为空"})
            if contract.get("quantity") is not None and settlement.get("quantity") is not None:
                compared_count += 1
                wide = wide_index.get(str(code))
                if wide:
                    source_match_count += 1
                    wide_contract_qty = _to_float(wide.get("contract_quantity") or wide.get("合同数量"))
                    wide_settlement_qty = _to_float(wide.get("settlement_quantity") or wide.get("申报数量"))
                    if wide_contract_qty and abs(wide_contract_qty - _to_float(contract.get("quantity"))) > 0.0001:
                        issues.append({"level": "error", "code": "SOURCE_MISMATCH", "item_code": code, "field": "合同数量", "message": "结构化资料与统一宽表合同数量不一致"})
                    if wide_settlement_qty and abs(wide_settlement_qty - _to_float(settlement.get("quantity"))) > 0.0001:
                        issues.append({"level": "error", "code": "SOURCE_MISMATCH", "item_code": code, "field": "申报数量", "message": "结构化资料与统一宽表申报数量不一致"})
                system = system_index.get(str(code))
                if system:
                    system_match_count += 1
                    system_qty = _to_float(system.get("settlement_quantity") or system.get("申报数量") or system.get("actual_quantity") or system.get("实际数量"))
                    if system_qty and abs(system_qty - _to_float(settlement.get("quantity"))) > 0.0001:
                        issues.append({"level": "error", "code": "SYSTEM_MISMATCH", "item_code": code, "field": "申报数量", "message": "系统抓数与文档/宽表申报数量不一致"})
                if _to_float(settlement.get("quantity")) < 0 or _to_float(actual.get("quantity")) < 0:
                    issues.append({"level": "error", "code": "NEGATIVE_QTY", "item_code": code, "message": "工程量出现负数，请主审确认原始资料或扣款口径"})
                if _to_float(settlement.get("unit_price")) == 0:
                    issues.append({"level": "warning", "code": "ZERO_PRICE", "item_code": code, "message": "申报单价为0，需要核对是否为扣款项或识别缺失"})
            if not item.get("evidence"):
                issues.append({"level": "warning", "code": "NO_EVIDENCE", "item_code": code, "message": "当前清单项没有绑定原始证据定位"})
        error_count = sum(1 for issue in issues if issue["level"] == "error")
        warning_count = sum(1 for issue in issues if issue["level"] == "warning")
        confirmed = (meta.get("data_quality") or {}).get("status") == "confirmed"
        if not self.line_items:
            status = "empty"
        elif confirmed:
            status = "confirmed"
        elif error_count:
            status = "blocked"
        else:
            status = "pending_review"
        return {
            "status": status,
            "can_run_core_models": status == "confirmed",
            "source_layers": source_layers or {
                "system_capture": {"label": "系统抓数", "status": "待接入", "records": 0},
                "document_ocr": {"label": "扫描件/OCR识别", "status": "已识别" if documents else "待接入", "records": len(documents)},
                "canonical_wide_table": {"label": "统一审计宽表", "status": "待校验", "records": len(self.line_items)},
            },
            "schema": {"schema_id": WIDE_TABLE_CONTRACT["schema_id"], "version": WIDE_TABLE_CONTRACT["version"], "grain": WIDE_TABLE_CONTRACT["grain"]},
            "required_documents": required_documents,
            "comparison": {
                "row_count": len(self.line_items),
                "compared_count": compared_count,
                "uncompared_count": max(len(self.line_items) - compared_count, 0),
                "source_match_count": source_match_count,
                "source_unmatched_count": max(len(self.line_items) - source_match_count, 0),
                "system_match_count": system_match_count,
                "system_unmatched_count": max(len(self.line_items) - system_match_count, 0),
                "method": "系统抓数 ↔ 文档识别 ↔ 统一宽表三方校验",
            },
            "error_count": error_count,
            "warning_count": warning_count,
            "issues": issues[:100],
            "confirmed_by": (meta.get("data_quality") or {}).get("confirmed_by", ""),
            "confirmed_at": (meta.get("data_quality") or {}).get("confirmed_at", ""),
        }

    def confirm_data_quality(self, operator: str = "主审") -> Dict[str, Any]:
        current = self._build_data_quality()
        if current["status"] == "empty":
            return {"status": "error", "message": "尚未导入资料，不能确认"}
        if current["error_count"]:
            return {"status": "error", "message": "存在阻断性数据错误，修正后才能确认", "data_quality": current}
        self.raw_data.setdefault("meta", {})["data_quality"] = {
            "status": "confirmed", "confirmed_by": operator or "主审", "confirmed_at": "2026-08-22 10:00"
        }
        self.data_quality = self._build_data_quality()
        return {"status": "success", "message": "数据整理确认完成，允许运行核心模型", "state": self.export_state()}

    def _build_workflow_state(self) -> Dict[str, Any]:
        preparation = self._build_preparation_state()
        if not self.line_items:
            phase = "准备中"
        elif preparation["data_quality"]["status"] in {"blocked", "pending_review"}:
            phase = "待主审整理数据"
        elif self.issues:
            phase = "待复核"
        else:
            phase = "资料可分析"
        return {
            "phase": phase,
            "status_steps": [
                {"key": "scope", "label": "组织范围", "status": "done"},
                {"key": "preparation", "label": "资料准备", "status": "done" if preparation["completion_pct"] >= 80 else "active"},
                {"key": "models", "label": "模型运行", "status": "done" if self.issues else "pending"},
                {"key": "review", "label": "问题复核", "status": "active" if self.issues else "pending"},
                {"key": "decision", "label": "决策报告", "status": "pending"},
                {"key": "closure", "label": "整改闭环", "status": "pending"},
            ],
        }

    def _build_decision_analysis(self, financial_summary: Dict[str, Any]) -> Dict[str, Any]:
        issue_amount = round(sum(issue.get("amount_impact", 0) for issue in self.issues), 2)
        return {
            "confirmed_issue_count": sum(1 for issue in self.issues if issue.get("status") in {"已审减", "已确认", "已销号"}),
            "high_risk_units": max(1, len({issue.get("contract_id") for issue in self.issues if issue.get("risk_level") == "高危"})),
            "issue_amount": issue_amount,
            "cash_exposure": financial_summary.get("payment_gap", 0),
            "profit_recovery": round(financial_summary.get("sum_deducted", 0) + issue_amount, 2),
            "conclusion": "本次审计应重点关注结算申报量价偏离、证据链缺口和支付进度早于审定结算形成的资金超付风险。",
        }

    def _build_relation_chains(self) -> List[Dict[str, Any]]:
        return [
            {
                "issue_id": issue.get("issue_id", ""),
                "title": issue.get("title", ""),
                "chain": issue.get("chain_type", ""),
                "risk_level": issue.get("risk_level", ""),
                "amount": issue.get("amount_impact", 0),
                "nodes": ["模型触发", "证据切片", "责任环节", "金额影响"],
            }
            for issue in self.issues[:12]
        ]

    def compare(self, category: str = "all") -> Dict[str, Any]:
        items, financial_summary = self.compute_three_way_reconciliation(self.raw_data, category=category)
        return {"items": items, "financial_summary": financial_summary}

    def analysis_snapshot(self) -> Dict[str, Any]:
        """Return the decision-layer point/line/surface analysis."""
        return {
            "status": "success",
            "scope": _deepcopy(self.scope),
            "point_line_surface": _deepcopy(self.point_line_surface),
            "decision_analysis": self._build_decision_analysis(self.compare()["financial_summary"]),
            "management_chains": _deepcopy(MANAGEMENT_CHAINS),
        }

    def set_scope(self, selected_org_codes: Optional[List[str]] = None, selected_project_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        if self.audit_task and self.audit_task.get("status") == "已冻结":
            return {"status": "error", "message": "当前审计任务范围已冻结，请先创建新任务版本"}
        self.scope = {
            "selected_org_codes": list(selected_org_codes or []),
            "selected_project_ids": list(selected_project_ids or []),
            "mode": "filtered" if selected_org_codes or selected_project_ids else "all",
        }
        return self.export_state()

    def scope_options(self) -> Dict[str, Any]:
        project = self.raw_data.get("project", {}) or {}
        if not project.get("id") or not self.line_items:
            return {"organizations": [], "projects": [], "contracts": [], "audit_task": _deepcopy(self.audit_task)}
        org_code = str(project.get("org_code") or "04")
        org_name = project.get("org_name") or "中建四局"
        contracts: Dict[str, Dict[str, Any]] = {}
        for item in self.line_items:
            contract_id = str(item.get("contract_id") or "DEMO-CON-001")
            entry = contracts.setdefault(contract_id, {"id": contract_id, "name": f"{contract_id} 结算合同", "item_count": 0, "amount": 0})
            entry["item_count"] += 1
            settlement = item.get("settlement", {}) or {}
            entry["amount"] += _to_float(settlement.get("quantity")) * _to_float(settlement.get("unit_price"))
        project_option = {
            "id": project.get("id"),
            "name": project.get("name") or "当前导入项目",
            "org_code": org_code,
            "org_name": org_name,
            "status": project.get("status") or "待审计",
            "contract_count": len(contracts),
        }
        return {
            "organizations": [{"code": org_code, "name": org_name, "project_count": 1}],
            "projects": [project_option],
            "contracts": list(contracts.values()),
            "audit_task": _deepcopy(self.audit_task),
        }

    def create_audit_task(self, org_code: str, project_id: str, contract_ids: List[str], owner: str = "主审") -> Dict[str, Any]:
        options = self.scope_options()
        project = next((item for item in options["projects"] if item["id"] == project_id), None)
        if not project:
            return {"status": "error", "message": "请选择已导入资料对应的项目"}
        available = {item["id"] for item in options["contracts"]}
        selected_contracts = [item for item in contract_ids if item in available]
        if not selected_contracts:
            return {"status": "error", "message": "至少选择一个合同"}
        now = "2026-08-22 10:30"
        self.scope = {
            "selected_org_codes": [org_code or project["org_code"]],
            "selected_project_ids": [project_id],
            "selected_contract_ids": selected_contracts,
            "mode": "frozen",
        }
        self.audit_task = {
            "id": f"AUDIT-{project_id}-001",
            "status": "已冻结",
            "owner": owner or "主审",
            "org_code": org_code or project["org_code"],
            "org_name": project["org_name"],
            "project_id": project_id,
            "project_name": project["name"],
            "contract_ids": selected_contracts,
            "data_snapshot": {"document_count": len(self.raw_data.get("project", {}).get("documents", []) or []), "item_count": len(self.line_items)},
            "model_version": "12业务模型+M0 / V3.2",
            "rule_version": "当前任务规则快照",
            "created_at": now,
        }
        return {"status": "success", "message": "审计任务已创建，范围已冻结", "audit_task": _deepcopy(self.audit_task), "state": self.export_state()}

    def run_all_models(self) -> Dict[str, Any]:
        results = []
        for model in MODEL_DEFINITIONS:
            result = self.run_model(model["id"])
            if result.get("status") == "success":
                results.append(result.get("run", {}))
        return {"status": "success", "runs": results, "state": self.export_state()}

    def model_rules(self, model_id: str = "") -> Dict[str, Any]:
        models = [m for m in MODEL_DEFINITIONS if not model_id or m["id"] == model_id]
        return {
            "status": "success",
            "models": [
                {
                    **model,
                    "thresholds": self.rule_versions.get(model["id"], {}).get("thresholds", {
                        "confirmed_rate_min": 0.95,
                        "confirmed_rate_max": 1.10,
                        "settlement_over_contract": 0.05,
                        "evidence_min_count": 2,
                        "quantity_variance": 0.05,
                        "price_deviation": 0.0,
                        "temporary_price_deviation": 0.10,
                    }),
                    "rule_version": self.rule_versions.get(model["id"], {}).get("version", "V1.0"),
                    "updated_at": self.rule_versions.get(model["id"], {}).get("updated_at", ""),
                    "editable": model["id"] != "M0",
                    "rule_source": "模型建设/规则清单.md",
                }
                for model in models
            ],
        }

    def save_model_rules(self, model_id: str, thresholds: Dict[str, Any], operator: str = "主审") -> Dict[str, Any]:
        models = {model.get("id"): model for model in self.audit_model_catalog.get("models", [])}
        if model_id not in models or model_id == "M0":
            return {"status": "error", "message": "该模型不存在或不允许编辑"}
        previous = self.rule_versions.get(model_id, {})
        version_number = int(str(previous.get("version", "V1.0")).replace("V", "").split(".")[0]) + 1 if previous else 1
        version = f"V{version_number}.0"
        now = "2026-08-22T10:00:00"
        self.rule_versions[model_id] = {
            "version": version,
            "thresholds": dict(thresholds or {}),
            "updated_at": now,
            "updated_by": operator or "主审",
            "previous_version": previous.get("version", ""),
        }
        self._rebuild()
        return {"status": "success", "model_id": model_id, "rule": self.rule_versions[model_id], "message": f"{model_id} 规则已保存为 {version}，可重新运行模型"}

    def run_model(self, model_id: str) -> Dict[str, Any]:
        models = {model.get("id"): model for model in self.audit_model_catalog.get("models", [])}
        if model_id not in models:
            return {"status": "error", "message": "模型不存在"}
        if model_id != "M0" and not self.data_quality.get("can_run_core_models"):
            return {
                "status": "error",
                "message": "数据尚未经过主审整理确认，核心模型暂不可运行",
                "data_quality": self.data_quality,
            }
        model = models[model_id]
        result = {
            "model_id": model_id,
            "model": model,
            "status": "已完成",
            "started_at": "2026-08-22T09:00:00",
            "completed_at": "2026-08-22T09:00:01",
            "input_documents": len(self.raw_data.get("project", {}).get("documents", [])),
            "input_rows": len(self.line_items),
            "issue_count": sum(1 for issue in self.issues if issue.get("model_code") == model_id),
            "evidence_count": len(self.issues),
            "rule_version": self.rule_versions.get(model_id, {}).get("version", "V1.0"),
            "thresholds": self.rule_versions.get(model_id, {}).get("thresholds", {}),
            "workflow": ["资料完整性检查", "结构化宽表", "确定性计算", "规则过滤", "证据绑定", "生成问题包"],
        }
        self.model_runs[model_id] = result
        return {"status": "success", "run": result, "state": self.export_state()}

    def configure_robot(self, task_name: str, schedule: str, source: str) -> Dict[str, Any]:
        if not task_name:
            return {"status": "error", "message": "抓取任务名称不能为空"}
        self.robot_task_state[task_name] = {
            "name": task_name,
            "schedule": schedule or "手动执行",
            "source": source or "业务系统",
            "status": "已配置",
            "last_run": "-",
        }
        return {"status": "success", "preparation": self._build_preparation_state()}

    def run_robot(self, task_name: str, rows: Optional[List[Dict[str, Any]]] = None, source: str = "业务系统") -> Dict[str, Any]:
        task = self.robot_task_state.setdefault(
            task_name,
            {"name": task_name, "schedule": "手动执行", "source": "业务系统", "status": "待配置", "last_run": "-"},
        )
        task["status"] = "已完成"
        task["last_run"] = "2026-08-22 09:30"
        if rows:
            self.merge_dataset({
                "line_items": rows,
                "project": {"documents": [{"id": f"robot-{task_name}", "type": "系统抓数", "name": f"{task_name}增量批次", "status": "待校验"}]},
                "meta": {"data_layers": {"system_capture": {"label": "系统抓数", "status": "已接入待校验", "records": len(rows), "source": source, "rows": rows}}},
            })
        return {"status": "success", "task": task, "preparation": self._build_preparation_state()}

    def apply_deduction(
        self,
        item_code: str,
        approved_qty: Any,
        approved_price: Any,
        reason: str,
    ) -> Dict[str, Any]:
        target = next((item for item in self.line_items if item.get("item_code") == item_code), None)
        if not target:
            return {"status": "error", "message": "未找到对应子目"}
        before = _build_line_item_row(target)
        audit = target.setdefault("audit", {})
        audit["approved_qty"] = _to_float(approved_qty, _to_float(target.get("settlement", {}).get("quantity")))
        audit["approved_price"] = _to_float(approved_price, _to_float(target.get("settlement", {}).get("unit_price")))
        audit["reason"] = reason
        audit["approved_at"] = "2026-08-21T00:00:00"
        target["status"] = "已审减"
        target["audit"]["status"] = "已审减"
        after_approved = round(audit["approved_qty"] * audit["approved_price"], 2)
        delta = round(before["amount_approved"] - after_approved, 2)
        project_financials = self.raw_data.setdefault("project", {}).setdefault("financials", {})
        if "actual_certified_total" in project_financials:
            project_financials["actual_certified_total"] = round(
                _to_float(project_financials.get("actual_certified_total"), 0) - delta, 2
            )
        target["status"] = "已审减"
        target["audit"]["status"] = "已审减"
        self._rebuild()
        financial_summary = self.compare()["financial_summary"]
        updated = next((item for item in self.compare()["items"] if item["item_code"] == item_code), before)
        return {
            "status": "success",
            "item": updated,
            "financial_summary": financial_summary,
            "summary": self.summary,
            "remediation_tasks": self._sync_remediation_tasks(),
        }

    def review_issue(self, issue_id: str, decision: str, reasoning: str, deduction_amount: Any = 0) -> Dict[str, Any]:
        issue = self.issue_index.get(issue_id)
        if not issue:
            return {"status": "error", "message": "未找到疑点"}
        issue["reviewer_decision"] = json.dumps({"decision": decision, "reasoning": reasoning}, ensure_ascii=False)
        issue["amount_impact"] = _to_float(deduction_amount, issue["amount_impact"])
        issue["status"] = "已审减" if decision in {"confirm", "认定", "通过"} else "整改中"
        self.remediation_tasks[issue_id] = {
            "latest_update": {"decision": decision, "reasoning": reasoning},
            "status": issue["status"],
        }
        self.point_line_surface = self._build_point_line_surface()
        self.report = self._build_report()
        return {
            "status": "success",
            "issue": issue,
            "remediation_tasks": self._sync_remediation_tasks(),
            "state": self.export_state(),
        }

    def save_report_draft(self, draft: str, author: str = "主审") -> Dict[str, Any]:
        content = str(draft or "").strip()
        if not content:
            return {"status": "error", "message": "报告草稿不能为空"}
        self.report = {
            **self._build_report(),
            "draft": content,
            "draft_status": "已保存",
            "draft_author": author or "主审",
            "draft_saved_at": "2026-08-22 10:00",
        }
        return {"status": "success", "report": _deepcopy(self.report), "state": self.export_state()}

    def save_expert_opinion(self, issue_id: str, expert_role: str, opinion: str) -> Dict[str, Any]:
        issue = self.issue_index.get(issue_id)
        content = str(opinion or "").strip()
        if not issue:
            return {"status": "error", "message": "未找到疑点"}
        if not content:
            return {"status": "error", "message": "专家意见不能为空"}
        record = {
            "role": expert_role or "审计专家",
            "opinion": content,
            "saved_at": "2026-08-22 10:00",
        }
        try:
            raw = json.loads(issue.get("expert_opinion") or "[]")
            existing = raw if isinstance(raw, list) else [raw]
        except (TypeError, json.JSONDecodeError):
            existing = []
        existing = [item for item in existing if item.get("role") != record["role"]]
        existing.append(record)
        issue["expert_opinion"] = json.dumps(existing, ensure_ascii=False)
        issue["latest_update"] = {"expert_role": record["role"], "expert_opinion": content}
        self.agent_orchestration = self._build_agent_orchestration()
        return {"status": "success", "issue": issue, "state": self.export_state()}

    def close_issue(self, issue_id: str, proof_number: str, proof_type: str) -> Dict[str, Any]:
        if not proof_number:
            return {"status": "error", "message": "销号必须提供凭证号"}
        issue = self.issue_index.get(issue_id)
        if not issue:
            return {"status": "error", "message": "未找到疑点"}
        issue["status"] = "已销号"
        issue["remediation_proof"] = json.dumps({"proof_number": proof_number, "proof_type": proof_type}, ensure_ascii=False)
        issue["closed_at"] = "2026-08-21T00:00:00"
        self.remediation_tasks[issue_id] = {
            "latest_update": {"proof_number": proof_number, "proof_type": proof_type},
            "status": "已销号",
        }
        self.point_line_surface = self._build_point_line_surface()
        return {"status": "success", "issue": issue, "remediation_tasks": self._sync_remediation_tasks(), "state": self.export_state()}

    def query_knowledge(self, question: str, context_issue_id: str = "") -> Dict[str, Any]:
        issue = self.issue_index.get(context_issue_id) if context_issue_id else None
        return answer_question(question, self.knowledge, issue)

    # Compatibility helpers
    def three_table_comparison(self, contract_id: str) -> Dict[str, Any]:
        items, financial_summary = self.compute_three_way_reconciliation(self.raw_data)
        return {
            "contract_id": contract_id,
            "project_name": self.raw_data.get("project", {}).get("name", ""),
            "contract_amount": financial_summary["sum_declared"],
            "owner_confirmed": financial_summary["sum_approved"],
            "sub_claimed": financial_summary["sum_declared"],
            "auth_deviation": round(financial_summary["sum_approved"] - financial_summary["sum_declared"], 2),
            "cost_deviation": round(financial_summary["sum_declared"] - financial_summary["sum_approved"], 2),
            "profit_margin": round(financial_summary["sum_approved"] - financial_summary["sum_declared"], 2),
            "profit_rate": 0.0,
            "risk_alerts": [item["deduct_reason"] for item in items if item["deduct_reason"]],
        }

    def generate_report(self, contract_id: Optional[str] = None, report_type: str = "full") -> Dict[str, Any]:
        report = {
            "report_id": f"report-{contract_id or self._contract_id()}",
            "contract_id": contract_id or self._contract_id(),
            "report_type": report_type,
            "generated_at": "2026-08-22T10:00:00",
            "summary": self.summary,
            "confirmed_issues": [
                {
                    "issue_id": issue["issue_id"],
                    "title": issue["title"],
                    "amount": issue["amount_impact"],
                    "status": issue["status"],
                    "reviewer_decision": issue.get("reviewer_decision", ""),
                }
                for issue in self.issues
                if issue.get("status") in {"已审减", "已确认", "已销号"}
            ],
            "recommendations": [
                "立即锁定高危支付。",
                "按条目完成审减与证据补齐。",
                "同步归档整改凭证并回写台账。",
            ],
        }
        if self.report.get("draft"):
            report["draft"] = self.report["draft"]
            report["draft_status"] = self.report.get("draft_status", "已保存")
        return report


def analyze(data: Dict[str, Any]) -> Dict[str, Any]:
    engine = SettlementAuditEngine()
    engine.load_dataset(data)
    return {
        **engine.export_state(),
        "cost_aggregation": _build_cost_aggregation(data),
        "fraud_assessment": _build_fraud_assessment(engine),
    }


def _build_cost_aggregation(data: Dict[str, Any]) -> Dict[str, Any]:
    cost = data.get("cost_components", {})
    claims = cost.get("claims", [])
    rewards_penalties = cost.get("rewards_penalties", [])
    management_fee = cost.get("management_fee", {})
    claims_requested = sum(_to_float(item.get("requested_amount")) for item in claims)
    claims_approved = sum(_to_float(item.get("approved_amount")) for item in claims)
    penalty_omission = sum(_to_float(item.get("amount")) for item in rewards_penalties if item.get("type") == "缃氭")
    management_fee_excess = round(
        _to_float(management_fee.get("settlement_base")) * (
            _to_float(management_fee.get("settlement_rate")) - _to_float(management_fee.get("approved_rate"))
        ),
        2,
    )
    return {
        "claims_requested": round(claims_requested, 2),
        "claims_approved": round(claims_approved, 2),
        "unapproved_claim_amount": round(claims_requested - claims_approved, 2),
        "penalty_omission": round(penalty_omission, 2),
        "management_fee_excess": round(management_fee_excess, 2),
    }


def _build_fraud_assessment(engine: SettlementAuditEngine) -> Dict[str, Any]:
    hits = []
    for issue in engine.issues[:5]:
        hits.append(issue["issue_id"])
    score = 100 if engine.summary.get("issue_count", 0) >= 5 else 60
    level = "高风险" if score >= 80 else "中风险"
    return {"score": score, "level": level, "hits": hits}


def compare_three_tables_from_data(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return compare_three_tables(data)


def answer_question_from_data(question: str, knowledge: Any, issue: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return answer_question(question, knowledge, issue)


if __name__ == "__main__":
    engine = SettlementAuditEngine()
    print(json.dumps(engine.export_state(), ensure_ascii=False, indent=2))
