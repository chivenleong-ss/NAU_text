#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate an external, inspectable demo package for the settlement audit flow."""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = Path(r"E:\建模需要的表格\_markdown_output")
OUTPUT = ROOT / "_demo_packages" / "中建结算审计演示资料包"


def write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


WIDE_TABLE_FIELDS = [
    ("project_id", "项目编码", "项目主键", "M0/M1/M2/M3", "settlement_demo.json"),
    ("org_code", "组织编码", "组织范围", "M0/M3", "行政架构-四局.md"),
    ("org_name", "组织名称", "组织范围", "M0/M3", "行政架构-四局.md"),
    ("project_name", "项目名称", "项目名称", "M0/M1/M2/M3", "合同、结算资料"),
    ("audit_task_id", "审计任务编码", "审计任务", "M0", "系统生成"),
    ("contract_id", "合同编码", "合同主键", "M1.3/M2.1/M2.2/M2.4/M2.5", "分包合同"),
    ("subcontractor_name", "分包单位", "责任主体", "M2.1/M2.4/M2.5/M3.3", "分包合同"),
    ("item_code", "清单编码", "清单主键", "12个核心模型", "结算资料"),
    ("item_name", "清单名称", "清单名称", "12个核心模型", "结算资料"),
    ("unit", "计量单位", "计量单位", "M1.1/M2.2/M2.3", "结算资料"),
    ("period", "业务期间", "时间维度", "M1.2/M3.1/M3.2", "施工日志、结算资料"),
    ("bid_quantity", "投标数量", "投标基准", "M1.1/M2.2/M2.3", "投标文件"),
    ("bid_unit_price", "投标单价", "投标基准", "M2.1/M2.2/M3.3", "投标文件"),
    ("contract_quantity", "合同数量", "合同基准", "M1.1/M2.2/M2.3", "分包合同"),
    ("contract_unit_price", "合同单价", "合同基准", "M2.1/M2.2/M2.4", "分包合同"),
    ("contract_sign_date", "合同签订日期", "合同时间", "M2.4", "分包合同"),
    ("contract_start_date", "合同开工日期", "合同时间", "M2.4", "分包合同"),
    ("contract_end_date", "合同完工日期", "合同时间", "M1.2/M2.4", "分包合同"),
    ("earliest_work_date", "最早施工日期", "现场时间", "M2.4/M3.3", "施工日志"),
    ("actual_quantity", "实际完成数量", "现场实绩", "M1.1/M2.3/M2.4", "施工日志、验收台账"),
    ("actual_unit_price", "实际执行单价", "现场实绩", "M2.1/M2.2", "结算台账"),
    ("variation_id", "变更签证编号", "变更主键", "M1.3/M2.2", "签证、施工日志"),
    ("variation_amount", "变更金额", "变更金额", "M1.3/M2.2", "签证、结算资料"),
    ("variation_approved", "变更是否审批", "变更状态", "M1.3/M2.1", "审批单、签证"),
    ("settlement_quantity", "申报结算数量", "结算申报", "M1.1/M2.2/M2.3", "结算单"),
    ("settlement_unit_price", "申报结算单价", "结算申报", "M2.1/M2.2", "结算单"),
    ("settlement_amount", "申报结算金额", "结算申报", "M1.1/M2.1/M2.2/M2.5", "结算单"),
    ("approved_quantity", "审定数量", "审定结果", "M1.1/M2.2/M2.3", "审定结论"),
    ("approved_amount", "审定金额", "审定结果", "M1.1/M2.1/M2.2/M2.5", "审定结论"),
    ("material_budget_quantity", "材料预算量", "材料基准", "M2.3", "材料台账、投标文件"),
    ("material_actual_quantity", "材料实际耗用量", "材料实绩", "M2.3", "材料进场验收台账"),
    ("material_loss_rate", "材料损耗率", "材料指标", "M2.3", "材料台账、规则清单"),
    ("material_deduction_amount", "材料超耗应扣金额", "材料计算结果", "M2.3", "模型计算"),
    ("paid_amount", "已支付金额", "资金实绩", "M3.3/M1.2", "财务效益审核数据源"),
    ("unpaid_amount", "未支付金额", "资金实绩", "M1.2/M3.3", "财务效益审核数据源"),
    ("evidence_doc_ids", "证据文档编号", "证据索引", "全部模型", "合同、结算单、日志、台账"),
    ("evidence_locations", "证据定位", "证据穿透", "全部模型", "扫描件页码/表格行号"),
    ("source_file_names", "原始文件名", "来源追溯", "M0/全部模型", "资料包文件"),
    ("data_quality_status", "数据质量状态", "质量控制", "M0", "模型计算"),
    ("settlement_over_contract_rate", "结算超合同比例", "派生指标", "M2.1", "模型计算"),
    ("quantity_deviation_rate", "数量偏差率", "派生指标", "M1.1/M2.2/M2.3", "模型计算"),
    ("risk_index", "风险指数", "派生指标", "M0/M1/M2/M3", "模型计算"),
    # V3.2 D 表的三表比对核心字段
    ("item_seq", "结算子目序号", "清单主键", "M1.1/M2.1/M2.2", "结算单"),
    ("feature_desc", "清单项目特征描述", "清单特征", "M2.2/M2.4", "招标文件、合同清单"),
    ("item_category", "清单费用类别", "清单分类", "M2.1/M2.2/M2.3", "结算单"),
    ("is_contract_item", "是否原合同清单项", "清单归属", "M2.2", "系统比对"),
    ("qty_change_approved", "累计合规变更工程量", "变更量", "M1.3/M2.2", "签证台账"),
    ("qty_audit_approved", "审计最终审定工程量", "审定结果", "M1.1/M2.2/M2.3", "系统计算/主审判定"),
    ("qty_diff_vs_contract", "结算超合同变更量差", "数量差异", "M2.2", "系统计算"),
    ("qty_diff_vs_actual", "结算超实测验收量差", "数量差异", "M1.1/M2.2", "系统计算"),
    ("qty_deducted", "算量核减工程量", "数量审减", "M1.1/M2.2", "系统计算"),
    ("price_tender_control", "招标控制价单价", "价格基准", "M2.1/M3.3", "招标文件"),
    ("price_temp_settlement", "过程临时结算单价", "价格过程值", "M2.1", "过程结算单"),
    ("price_market_info", "市场/定额参考价", "价格基准", "M2.1/M2.2", "信息价/定额库"),
    ("price_audit_approved", "审计最终审定单价", "价格审定", "M2.1/M2.2", "系统计算/主审判定"),
    ("price_deviation_contract", "单价偏离合同比例", "价格差异", "M2.1/M2.2", "系统计算"),
    ("price_deducted", "审减单价差额", "价格审减", "M2.1/M2.2", "系统计算"),
    ("amount_actual_certified", "现场实测完成合价", "金额比对", "M1.1/M2.2", "系统计算"),
    ("amount_audit_approved", "审计最终审定合价", "金额审定", "M1.1/M2.1/M2.2", "系统计算"),
    ("amount_deducted_total", "单项审减总金额", "金额审减", "M1.1/M2.1/M2.2", "系统计算"),
    ("deduct_reason_category", "审减归因分类", "审减归因", "M1/M2/M3", "规则引擎"),
    ("cumulative_paid_amount", "累计已支付工程款", "支付比对", "M1.2/M3.3", "SAP/司库"),
    ("payment_gap", "支付超前风险金额", "支付风险", "M1.2/M3.3", "系统计算"),
    # V3.2 未充分展开、但支撑主审穿透和决策面的补充字段
    ("source_page", "原文页码/行号", "证据定位", "M0/全部模型", "OCR/表格解析"),
    ("evidence_hash", "证据内容哈希", "证据防篡改", "M0/复核", "文件指纹"),
    ("responsible_department", "责任部门", "责任归属", "G1-G9", "组织架构/规则"),
    ("model_hit_codes", "命中模型编码", "模型结果", "M0/M1/M2/M3", "规则引擎"),
    ("review_status", "主审复核状态", "人工裁决", "主审驾驶舱", "主审操作"),
    ("review_reason", "主审复核意见", "人工裁决", "主审驾驶舱", "主审操作"),
    ("management_chain_ids", "管理归因链编码", "点线面归因", "G1-G9", "关联引擎"),
]


def build_wide_table(data: dict) -> tuple[list[str], list[list[object]]]:
    project = data["project"]
    source_files = ";".join(doc["name"] for doc in project.get("documents", []))
    rows = []
    for index, item in enumerate(data["line_items"], start=1):
        contract = item.get("contract", {})
        actual = item.get("actual", {})
        settlement = item.get("settlement", {})
        change = item.get("change", {})
        evidence = item.get("evidence", [])
        contract_amount = float(contract.get("quantity", 0)) * float(contract.get("unit_price", 0))
        settlement_amount = float(settlement.get("quantity", 0)) * float(settlement.get("unit_price", 0))
        actual_qty = float(actual.get("quantity", 0))
        contract_qty = float(contract.get("quantity", 0))
        material_budget = contract_qty if item["item_code"] == "DEMO-002" else 0
        material_actual = actual_qty if item["item_code"] == "DEMO-002" else 0
        material_deduction = max(material_actual - material_budget, 0) * float(contract.get("unit_price", 0))
        values = {
            "project_id": project["id"], "org_code": "04", "org_name": "中建四局",
            "project_name": project["name"], "audit_task_id": "AUDIT-DEMO-2026-001",
            "contract_id": "DEMO-CON-001" if index < 4 else "DEMO-CON-002",
            "subcontractor_name": "示例分包单位A" if index < 4 else "示例分包单位B",
            "item_code": item["item_code"], "item_name": item["name"], "unit": item["unit"],
            "period": "2025Q4", "bid_quantity": contract_qty, "bid_unit_price": contract.get("unit_price", 0),
            "contract_quantity": contract_qty, "contract_unit_price": contract.get("unit_price", 0),
            "contract_sign_date": project["sign_date"], "contract_start_date": "2025-04-01",
            "contract_end_date": project["actual_finish_date"], "earliest_work_date": "2025-03-20",
            "actual_quantity": actual_qty, "actual_unit_price": actual.get("unit_price", 0),
            "variation_id": "VAR-DEMO-003" if item["item_code"] == "DEMO-003" else "",
            "variation_amount": settlement_amount - contract_amount if item["item_code"] == "DEMO-003" else 0,
            "variation_approved": "否" if not change.get("approved", True) else "是",
            "settlement_quantity": settlement.get("quantity", 0), "settlement_unit_price": settlement.get("unit_price", 0),
            "settlement_amount": settlement_amount, "approved_quantity": contract_qty,
            "approved_amount": contract_amount, "material_budget_quantity": material_budget,
            "material_actual_quantity": material_actual, "material_loss_rate": 0.06 if material_budget else 0,
            "material_deduction_amount": material_deduction, "paid_amount": 0, "unpaid_amount": settlement_amount,
            "evidence_doc_ids": ";".join(f"DOC-{n+1:03d}" for n, _ in enumerate(evidence)),
            "evidence_locations": ";".join(str(e.get("location", "")) for e in evidence),
            "source_file_names": source_files, "data_quality_status": "待模型校验",
            "settlement_over_contract_rate": round(settlement_amount / contract_amount - 1, 4) if contract_amount else 0,
            "quantity_deviation_rate": round(actual_qty / contract_qty - 1, 4) if contract_qty else 0,
            "risk_index": 0,
            "item_seq": index, "feature_desc": f"{item['name']}施工及结算特征（演示）",
            "item_category": "变更签证" if item["item_code"] == "DEMO-003" else "合同清单",
            "is_contract_item": item["item_code"] != "DEMO-003",
            "qty_change_approved": 0,
            "qty_audit_approved": contract_qty,
            "qty_diff_vs_contract": float(settlement.get("quantity", 0)) - contract_qty,
            "qty_diff_vs_actual": float(settlement.get("quantity", 0)) - actual_qty,
            "qty_deducted": max(float(settlement.get("quantity", 0)) - contract_qty, 0),
            "price_tender_control": contract.get("unit_price", 0),
            "price_temp_settlement": contract.get("unit_price", 0),
            "price_market_info": contract.get("unit_price", 0),
            "price_audit_approved": contract.get("unit_price", 0),
            "price_deviation_contract": round(float(settlement.get("unit_price", 0)) / float(contract.get("unit_price", 1)) - 1, 4) if contract.get("unit_price") else 0,
            "price_deducted": max(float(settlement.get("unit_price", 0)) - float(contract.get("unit_price", 0)), 0),
            "amount_actual_certified": actual_qty * float(contract.get("unit_price", 0)),
            "amount_audit_approved": contract_amount,
            "amount_deducted_total": max(settlement_amount - contract_amount, 0),
            "deduct_reason_category": "未审批变更" if item["item_code"] == "DEMO-003" else "数量/单价偏差",
            "cumulative_paid_amount": project.get("financials", {}).get("paid_total", 0),
            "payment_gap": max(project.get("financials", {}).get("paid_total", 0) - contract_amount, 0),
            "source_page": ";".join(str(e.get("location", "")) for e in evidence),
            "evidence_hash": f"demo-{item['item_code'].lower()}",
            "responsible_department": "项目商务部" if item["item_code"] != "DEMO-002" else "项目工程部",
            "model_hit_codes": "M2.1;M2.2" if item["item_code"] in {"DEMO-001", "DEMO-003"} else "M2.3",
            "review_status": "待主审复核", "review_reason": "",
            "management_chain_ids": "G1;G4" if item["item_code"] == "DEMO-003" else "G2",
        }
        rows.append([values[key] for key, *_ in WIDE_TABLE_FIELDS])
    return [field[0] for field in WIDE_TABLE_FIELDS], rows


def build_dataset() -> dict:
    return {
        "project": {
            "id": "DEMO-NF-026",
            "name": "南方产业园一期施工总承包项目",
            "status": "资料已导入，待审计分析",
            "phase": "竣工结算",
            "owner": "南方产业园建设有限公司",
            "contractor": "中建某局建设有限公司",
            "sign_date": "2025-03-15",
            "actual_finish_date": "2026-01-15",
            "acceptance_date": "2026-03-20",
            "settlement_date": None,
            "completed_output": 41200000,
            "confirmed_output": 38600000,
            "cumulative_collection": 42000000,
            "sap_cumulative_revenue": 51086000,
            "sap_inventory_balance": 8687800,
            "book_profit": 12000000,
            "supply_chain_discount_fee": 37130000,
            "steel_overdue_interest": 47100000,
            "internal_loan_interest": 0,
            "owner_default_ratio": 0.35,
            "owner_default_months": 14,
            "owner_progress_pay_ratio": 0.70,
            "financials": {
                "settlement_total": 51086000,
                "actual_certified_total": 50200000,
                "paid_total": 50620000,
            },
            "documents": [
                {"id": "doc-contract", "type": "分包合同", "name": "A-SHJT-ZT-2022-02.md", "status": "已识别"},
                {"id": "doc-settlement", "type": "分包结算", "name": "主体工程劳务分包结算管理附表.md", "status": "已识别"},
                {"id": "doc-ledger", "type": "结算台账", "name": "结算台账.md", "status": "已识别"},
                {"id": "doc-material", "type": "物资台账", "name": "物资进场验收台账.md", "status": "已识别"},
                {"id": "doc-diary", "type": "施工日志", "name": "A项目施工日记（2024年01月20日）.md", "status": "已识别"},
                {"id": "doc-finance", "type": "财务资料", "name": "财务效益审核数据源.csv", "status": "已识别"},
            ],
        },
        "line_items": [
            {
                "item_code": "DEMO-001", "name": "主体结构混凝土", "unit": "m³", "location": "主体结构",
                "contract": {"quantity": 1000, "unit_price": 420}, "actual": {"quantity": 1060, "unit_price": 420},
                "settlement": {"quantity": 1060, "unit_price": 460},
                "change": {"quantity": 0, "approved": True}, "evidence": [{"document": "物资进场验收台账.md", "location": "第12行"}, {"document": "主体工程劳务分包结算管理附表.md", "location": "第18行"}],
            },
            {
                "item_code": "DEMO-002", "name": "钢筋材料损耗", "unit": "t", "location": "地下室",
                "contract": {"quantity": 100, "unit_price": 3100}, "actual": {"quantity": 106, "unit_price": 3100},
                "settlement": {"quantity": 106, "unit_price": 3500},
                "change": {"quantity": 0, "approved": True}, "leakage": {"kind": "material_loss"}, "evidence": [{"document": "物资进场验收台账.md", "location": "第32行"}, {"document": "结算台账.md", "location": "第9行"}],
            },
            {
                "item_code": "DEMO-003", "name": "临时变更签证", "unit": "项", "location": "园区道路",
                "contract": {"quantity": 1, "unit_price": 180000}, "actual": {"quantity": 1, "unit_price": 180000},
                "settlement": {"quantity": 1, "unit_price": 280000},
                "change": {"quantity": 1, "approved": False, "relation": "overlap"}, "evidence": [{"document": "施工日记.md", "location": "2025-11-04"}],
            },
            {
                "item_code": "DEMO-004", "name": "第三方代工维修", "unit": "项", "location": "精装区域",
                "contract": {"quantity": 1, "unit_price": 900000}, "actual": {"quantity": 1, "unit_price": 900000},
                "settlement": {"quantity": 1, "unit_price": 900000},
                "change": {"quantity": 0, "approved": True}, "leakage": {"kind": "machine_mismatch"}, "evidence": [{"document": "施工日记.md", "location": "退场记录"}],
            },
        ],
        "cost_components": {"claims": [], "rewards_penalties": [], "management_fee": {}},
        "experts": [{"name": "合同造价专家"}, {"name": "现场工程专家"}, {"name": "财务资金专家"}, {"name": "法务风控专家"}],
        "meta": {"package_name": "中建结算审计演示资料包", "generated_at": date.today().isoformat()},
    }


def generate_package() -> dict[str, str | int]:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "01_结构化数据").mkdir(parents=True)
    (OUTPUT / "02_商务资料").mkdir(parents=True)
    (OUTPUT / "03_财务资料").mkdir(parents=True)
    data = build_dataset()
    (OUTPUT / "01_结构化数据" / "settlement_demo.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    wide_headers, wide_rows = build_wide_table(data)
    write_csv(OUTPUT / "01_结构化数据" / "审计统一宽表.csv", wide_headers, wide_rows)
    write_csv(OUTPUT / "01_结构化数据" / "审计统一宽表_固定模板.csv", wide_headers, [])
    schema = {
        "name": "审计统一宽表",
        "version": "V3.2-adapted",
        "grain": "project_id + contract_id + item_code + period",
        "purpose": "承接合同、投标、结算、现场、材料、付款、证据和模型结果；效益审核表单独维护",
        "source_spec": "模型建设/工程全生命周期结算审计多智能体系统终极实施方案与全量数据宽表 (V3.2 终极完整版).md",
        "model_count": {"preparation": 1, "core": 12, "total": 13},
        "tables": [
            {"code": "A", "name": "项目与合同全周期宽表", "status": "建议正式建表"},
            {"code": "B", "name": "工程量清单与三表穿透原子宽表", "status": "建议正式建表"},
            {"code": "C", "name": "现场时空工单与人机轨迹宽表", "status": "建议正式建表"},
            {"code": "D", "name": "工程结算三表穿透比对宽表", "status": "当前演示合并实现"},
            {"code": "E", "name": "项目效益审核表", "status": "独立维护，不并入审计统一宽表"},
        ],
        "fields": [
            {"name": key, "label": label, "domain": domain, "models": models, "source": source}
            for key, label, domain, models, source in WIDE_TABLE_FIELDS
        ],
    }
    (OUTPUT / "01_结构化数据" / "审计统一宽表字段说明.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUTPUT / "03_财务资料" / "财务效益审核数据源.csv", ["项目编码", "成本分类", "客商名称", "合同编码", "财务已入账成本", "已支付金额"], [
        ["DEMO-NF-026", "材料费", "示例供应商A", "DEMO-CON-001", 16800000, 16200000],
        ["DEMO-NF-026", "分包工程", "示例分包商B", "DEMO-CON-002", 15180000, 15800000],
        ["DEMO-NF-026", "财务费用", "供应链金融", "", 84240000, 0],
    ])
    write_csv(OUTPUT / "02_商务资料" / "商务结算宽表.csv", ["项目编码", "合同编码", "清单编码", "清单名称", "合同数量", "实际数量", "申报数量", "合同单价", "申报单价", "责任单位"], [
        ["DEMO-NF-026", "DEMO-CON-001", "DEMO-001", "主体结构混凝土", 1000, 1060, 1060, 420, 460, "项目商务部"],
        ["DEMO-NF-026", "DEMO-CON-001", "DEMO-002", "钢筋材料损耗", 100, 106, 106, 3100, 3500, "项目工程部"],
        ["DEMO-NF-026", "DEMO-CON-002", "DEMO-003", "临时变更签证", 1, 1, 1, 180000, 280000, "项目经理"],
    ])
    for source_dir, target_dir in [(SOURCE / "2.分包合同", OUTPUT / "02_商务资料"), (SOURCE / "3.分包结算资料", OUTPUT / "02_商务资料"), (SOURCE / "4.结算台账", OUTPUT / "02_商务资料"), (SOURCE / "5.物资进场验收台账", OUTPUT / "02_商务资料"), (SOURCE / "6.施工日记", OUTPUT / "02_商务资料")]:
        if source_dir.exists():
            for path in source_dir.glob("*.md"):
                shutil.copy2(path, target_dir / path.name)
    # Keep every converted source document in the external package for traceability.
    source_archive = OUTPUT / "02_商务资料" / "原始资料识别"
    source_archive.mkdir(parents=True, exist_ok=True)
    if SOURCE.exists():
        for path in SOURCE.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}:
                target = source_archive / path.name
                if not target.exists():
                    shutil.copy2(path, target)
    manifest = {
        "name": "中建结算审计演示资料包",
        "entry": "01_结构化数据/settlement_demo.json",
        "wide_table": "01_结构化数据/审计统一宽表.csv",
        "benefit_table": "03_财务资料/财务效益审核数据源.csv",
        "documents": [p.relative_to(OUTPUT).as_posix() for p in OUTPUT.rglob("*") if p.is_file()],
    }
    (OUTPUT / "资料包清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = OUTPUT.parent / "中建结算审计演示资料包.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in OUTPUT.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(OUTPUT).as_posix())
    return {
        "directory": str(OUTPUT),
        "zip_path": str(zip_path),
        "file_count": len(manifest["documents"]),
        "entry": str(OUTPUT / "01_结构化数据" / "settlement_demo.json"),
    }


def main() -> None:
    result = generate_package()
    print(result["directory"])
    print(result["zip_path"])


if __name__ == "__main__":
    main()
