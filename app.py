#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Settlement audit web service."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS

from settlement_audit_engine import SettlementAuditEngine, analyze, answer_question, parse_uploaded_file
from generate_demo_package import generate_package
from dify_adapter import DifyAdapter


BASE_DIR = Path(__file__).resolve().parent
ORG_SOURCE_PATH = BASE_DIR / "行政架构-四局.md"
SOURCE_SEARCH_ROOTS = [
    Path(r"E:\建模需要的表格"),
    Path(r"C:\Users\sasa\Desktop\模型建设\260602"),
    BASE_DIR / "模型建设",
    BASE_DIR / "_demo_packages",
]
SOURCE_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".md", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

app = Flask(__name__, static_folder=".")
CORS(app)

engine = SettlementAuditEngine()
dify = DifyAdapter()


def _load_organization_nodes() -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    if not ORG_SOURCE_PATH.exists():
        return {"root": None, "nodes": [], "count": 0, "source": str(ORG_SOURCE_PATH)}
    pattern = re.compile(r"^(?P<indent>\s*)-\s+\*\*(?P<name>.+?)\*\*（编码：(?P<code>\d+)）")
    stack: List[Dict[str, Any]] = []
    for raw_line in ORG_SOURCE_PATH.read_text(encoding="utf-8").splitlines():
        match = pattern.match(raw_line)
        if not match:
            continue
        level = len(match.group("indent")) // 4
        node = {
            "code": match.group("code"),
            "name": match.group("name"),
            "level": level,
            "parent_code": None,
            "children": [],
        }
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        if stack:
            node["parent_code"] = stack[-1]["code"]
            stack[-1]["children"].append(node["code"])
        nodes.append(node)
        stack.append(node)
    root = nodes[0]["code"] if nodes else None
    return {"root": root, "nodes": nodes, "count": len(nodes), "source": str(ORG_SOURCE_PATH)}


ORGANIZATION_NODES = _load_organization_nodes()


def _category_from_request(default: str = "all") -> str:
    category = request.args.get("category", default)
    return category if category in {"all", "high_risk", "over_qty", "over_price"} else default


def _json_body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _upload_kind(filename: str) -> str:
    lower = filename.lower()
    if "合同" in filename or "contract" in lower:
        return "contract"
    if "竣工" in filename or "实际" in filename or "actual" in lower or "accept" in lower:
        return "actual"
    if "结算" in filename or "宽表" in filename or "settlement" in lower or "wide" in lower or "审定" in filename:
        return "settlement"
    return "other"


def _rows_from_csv_bytes(raw: bytes) -> List[Dict[str, Any]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _build_uploaded_dataset(files) -> Dict[str, Any]:
    payloads: Dict[str, List[Dict[str, Any]]] = {"contract": [], "actual": [], "settlement": [], "other": []}
    filenames: List[str] = []
    structured_payload: Dict[str, Any] = {}
    package_files: List[str] = []
    package_rows: List[Dict[str, Any]] = []
    for file_storage in files:
        filename = file_storage.filename or "upload"
        filenames.append(filename)
        raw = file_storage.read()
        if filename.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    package_files.append(name)
                    data = archive.read(name)
                    if name.lower().endswith(".csv"):
                        rows = _rows_from_csv_bytes(data)
                        if rows:
                            payloads[_upload_kind(name)].extend(rows)
                            if "审计统一宽表" in name or "canonical" in name.lower():
                                package_rows.extend(rows)
                    elif name.lower().endswith(".json"):
                        try:
                            parsed = json.loads(data.decode("utf-8-sig"))
                            if isinstance(parsed, dict) and parsed.get("line_items"):
                                structured_payload = parsed
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
        elif filename.lower().endswith(".csv"):
            rows = _rows_from_csv_bytes(raw)
            if rows:
                payloads[_upload_kind(filename)].extend(rows)
        elif filename.lower().endswith(".json"):
            try:
                parsed = json.loads(raw.decode("utf-8-sig"))
                if isinstance(parsed, dict) and parsed.get("line_items"):
                    structured_payload = parsed
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

    if structured_payload:
        structured_payload.setdefault("meta", {})
        documents = list(structured_payload.get("project", {}).get("documents", []) or [])
        known_names = {str(doc.get("name", "")) for doc in documents}
        for index, name in enumerate(package_files, start=1):
            base = Path(name).name
            if base in known_names or base in {"资料包清单.json", "settlement_demo.json"}:
                continue
            documents.append({"id": f"upload-doc-{index:03d}", "type": _source_category(Path(base)), "name": base, "status": "待校验"})
        structured_payload.setdefault("project", {})["documents"] = documents
        structured_payload["meta"].update({
            "uploaded_files": filenames,
            "package_files": package_files,
            "data_source": "uploaded_package",
            "data_layers": {
                "system_capture": {"label": "系统抓数", "status": "待接入", "records": 0, "rows": []},
                "document_ocr": {"label": "扫描件/OCR识别", "status": "已登记待核验", "records": len(documents), "files": package_files},
                "canonical_wide_table": {"label": "统一审计宽表", "status": "已生成待校验", "records": len(package_rows) or len(structured_payload.get("line_items", [])), "rows": package_rows},
            },
        })
        return structured_payload

    merged: Dict[str, Dict[str, Any]] = {}
    for kind, rows in payloads.items():
        for row in rows:
            code = row.get("项目编码") or row.get("项目编号") or row.get("item_code") or row.get("item") or row.get("编码")
            if not code:
                continue
            item = merged.setdefault(
                code,
                {
                    "item_code": code,
                    "name": row.get("项目名称") or row.get("name") or code,
                    "unit": row.get("单位") or row.get("unit") or "",
                    "contract": {"quantity": 0, "unit_price": 0},
                    "actual": {"quantity": 0, "unit_price": 0},
                    "settlement": {"quantity": 0, "unit_price": 0},
                    "change": {"quantity": 0, "unit_price": 0, "approved": True},
                    "evidence": [],
                },
            )
            qty = row.get("工程量") or row.get("quantity") or row.get("数量")
            price = row.get("单价") or row.get("unit_price") or row.get("综合单价")
            if kind == "contract":
                item["contract"] = {"quantity": qty or 0, "unit_price": price or 0}
            elif kind == "actual":
                item["actual"] = {"quantity": qty or 0, "unit_price": price or 0}
            elif kind == "settlement":
                item["settlement"] = {"quantity": qty or 0, "unit_price": price or 0}

    line_items = list(merged.values())
    financials = {
        "contract_total": sum(float(x.get("contract", {}).get("quantity", 0)) * float(x.get("contract", {}).get("unit_price", 0)) for x in line_items),
        "settlement_total": sum(float(x.get("settlement", {}).get("quantity", 0)) * float(x.get("settlement", {}).get("unit_price", 0)) for x in line_items),
        "paid_total": sum(float(x.get("settlement", {}).get("quantity", 0)) * float(x.get("settlement", {}).get("unit_price", 0)) for x in line_items),
    }
    return {
        "project": {"id": "UPLOAD", "name": "上传资料包", "status": "结算审核"},
        "line_items": line_items,
        "financials": financials,
        "cost_components": {"claims": [], "rewards_penalties": [], "management_fee": {}},
        "historical_patterns": [],
        "experts": engine.raw_data.get("experts", []),
        "meta": {"uploaded_files": filenames},
    }


def _source_category(path: Path) -> str:
    name = path.name.lower()
    if "合同" in path.name or "contract" in name:
        return "合同"
    if "结算" in path.name or "审定" in path.name or "settlement" in name:
        return "结算"
    if "日志" in path.name or "日记" in path.name or "diary" in name:
        return "施工日志"
    if "投标" in path.name or "招标" in path.name or "tender" in name:
        return "投标/招标"
    if "材料" in path.name or "物资" in path.name:
        return "材料台账"
    if "效益" in path.name or "财务" in path.name:
        return "效益/财务"
    return "其他资料"


def _find_source_file(name: str) -> Path | None:
    wanted = Path(str(name or "").replace("\\", "/")).name
    if not wanted or wanted in {".", ".."}:
        return None
    candidates: List[Path] = []
    for root in SOURCE_SEARCH_ROOTS:
        if not root.exists():
            continue
        candidates.extend(path for path in root.rglob(wanted) if path.is_file())
    package_root = BASE_DIR / "_demo_packages" / "中建结算审计演示资料包"
    if package_root.exists():
        candidates.extend(path for path in package_root.rglob(wanted) if path.is_file())
    return next((path for path in candidates if path.suffix.lower() in SOURCE_EXTENSIONS), None)


def _read_source_file(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".csv", ".json"}:
        content = path.read_text(encoding="utf-8-sig", errors="replace")
        return {"content": content[:20000], "truncated": len(content) > 20000, "format": suffix[1:] or "text"}
    return {"content": "", "truncated": False, "format": suffix[1:] or "binary"}


@app.route("/api/settlement-demo/source-files", methods=["GET"])
def source_files():
    query = (request.args.get("q") or "").strip().lower()
    category = (request.args.get("category") or "").strip()
    limit = min(max(int(request.args.get("limit", 200)), 1), 1000)
    results: List[Dict[str, Any]] = []
    seen = set()
    for root in SOURCE_SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            item_category = _source_category(path)
            if query and query not in path.name.lower() and query not in str(path.parent).lower():
                continue
            if category and item_category != category:
                continue
            stat = path.stat()
            results.append({
                "name": path.name,
                "path": str(path),
                "category": item_category,
                "extension": path.suffix.lower(),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "readable": True,
            })
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    results.sort(key=lambda item: (item["category"], item["name"]))
    return jsonify({"status": "success", "count": len(results), "roots": [str(root) for root in SOURCE_SEARCH_ROOTS], "files": results})


@app.route("/api/settlement-demo/demo-package/generate", methods=["POST"])
def generate_demo_package_endpoint():
    """Create an inspectable demo package; importing it remains a separate user action."""
    result = generate_package()
    return jsonify({"status": "success", "message": "演示资料包已生成，请在审计准备阶段导入", "package": result})


@app.route("/api/settlement-demo/demo-package/download", methods=["GET"])
def download_demo_package():
    package_path = BASE_DIR / "_demo_packages" / "中建结算审计演示资料包.zip"
    if not package_path.exists():
        return jsonify({"status": "error", "message": "请先生成演示资料包"}), 404
    return send_file(package_path, as_attachment=True, download_name=package_path.name)


@app.route("/")
def index():
    return send_from_directory(".", "settlement_platform.html")


@app.route("/<path:path>")
def static_files(path: str):
    return send_from_directory(".", path)


@app.route("/api/init", methods=["POST"])
def init_data():
    return jsonify(engine.import_sample_data())


@app.route("/api/settlement-demo/state", methods=["GET"])
def settlement_state():
    category = _category_from_request()
    return jsonify(engine.export_state(category))


@app.route("/api/settlement-demo/organizations", methods=["GET"])
def organizations():
    return jsonify({"status": "success", "organization_tree": ORGANIZATION_NODES})


@app.route("/api/settlement-demo/compare", methods=["GET"])
def settlement_compare():
    category = _category_from_request()
    return jsonify(engine.compare(category))


@app.route("/api/settlement-demo/apply-deduction", methods=["POST"])
def apply_deduction():
    data = _json_body()
    result = engine.apply_deduction(
        data.get("item_code", ""),
        data.get("approved_qty"),
        data.get("approved_price"),
        data.get("reason", ""),
    )
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/decision", methods=["POST"])
def decision():
    data = _json_body()
    result = engine.review_issue(
        data.get("issue_id", ""),
        data.get("decision", ""),
        data.get("note") or data.get("reasoning", ""),
        data.get("deduction_amount", 0),
    )
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/remediation", methods=["POST"])
def remediation():
    data = _json_body()
    issue_id = data.get("issue_id", "")
    status = data.get("status", "")
    note = data.get("note", "")
    proof_number = data.get("proof_number", "")
    proof_type = data.get("proof_type", "")

    if proof_number and status:
        result = engine.close_issue(issue_id, proof_number, proof_type)
        return jsonify(result), (200 if result.get("status") == "success" else 400)

    if status == "closed":
        if not proof_number:
            return jsonify({"status": "error", "message": "proof_number is required"}), 400
        result = engine.close_issue(issue_id, proof_number, proof_type)
        return jsonify(result), (200 if result.get("status") == "success" else 400)

    if status in {"已销号", "closed", "close"}:
        if not proof_number:
            return jsonify({"status": "error", "message": "销号必须提供凭证编号"}), 400
        result = engine.close_issue(issue_id, proof_number, proof_type)
        return jsonify(result), (200 if result.get("status") == "success" else 400)

    if status == "已销号":
        if not proof_number:
            return jsonify({"status": "error", "message": "销号必须提供凭证号"}), 400
        result = engine.close_issue(issue_id, proof_number, proof_type)
    else:
        result = engine.review_issue(issue_id, status or "整改中", note, data.get("deduction_amount", 0))
        if result.get("status") == "success":
            issue = result["issue"]
            issue["status"] = status or issue["status"]
            result["remediation_tasks"] = engine._sync_remediation_tasks()
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/knowledge", methods=["POST"])
def knowledge():
    data = _json_body()
    question = data.get("question", "")
    dify_result = dify.retrieve(question, int(data.get("top_k", 5) or 5))
    if dify_result.get("status") == "success":
        payload = dify_result.get("data") or {}
        records = payload.get("records") or payload.get("data") or []
        return jsonify({
            "status": "success",
            "provider": "dify",
            "answer": "已从知识库召回相关制度依据，请结合当前疑点进行主审判断。",
            "source": "Dify 知识库",
            "records": records,
            "retrieval": payload,
        })
    result = engine.query_knowledge(question, data.get("context_issue_id", ""))
    return jsonify({"status": "success", "provider": "local", "dify": dify_result, **result})


@app.route("/api/settlement-demo/dify/status", methods=["GET"])
def dify_status():
    return jsonify({"status": "success", **dify.status()})


@app.route("/api/settlement-demo/source-preview", methods=["GET"])
def source_preview():
    name = request.args.get("name", "")
    path = _find_source_file(name)
    if not path:
        return jsonify({"status": "error", "message": "未找到原始资料"}), 404
    payload = _read_source_file(path)
    return jsonify({
        "status": "success",
        "name": path.name,
        "category": _source_category(path),
        "path": str(path),
        **payload,
    })


@app.route("/api/settlement-demo/history-reports", methods=["GET"])
def history_reports():
    keyword = request.args.get("keyword", "").strip().lower()
    matches: List[Dict[str, Any]] = []
    for root in SOURCE_SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            label = path.name.lower()
            if not any(token in label for token in ("历史", "报告", "审计")):
                continue
            if keyword and keyword not in label:
                continue
            matches.append({"name": path.name, "path": str(path), "category": _source_category(path)})
    unique = {item["path"]: item for item in matches}
    return jsonify({"status": "success", "reports": list(unique.values())[:100]})


@app.route("/api/settlement-demo/expert-opinion", methods=["POST"])
def expert_opinion():
    data = _json_body()
    result = engine.save_expert_opinion(
        data.get("issue_id", ""),
        data.get("expert_role", "审计专家"),
        data.get("opinion", ""),
    )
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/issue-analysis/<issue_id>", methods=["GET"])
def issue_analysis(issue_id: str):
    issue = engine.issue_index.get(issue_id)
    if not issue:
        return jsonify({"status": "error", "message": "未找到疑点"}), 404
    return jsonify(
        {
            "status": "success",
            "analysis": issue,
            "screening": {"keep_for_chief_review": issue["risk_level"] == "高危"},
            "conference": {"mode": "专家圆桌" if issue["risk_level"] == "高危" else "统一互动讨论"},
            "knowledge_prompt": f"{issue['title']} 的制度依据是什么？",
            "question_slots": ["依据", "证据", "口径", "建议"],
        }
    )


@app.route("/api/settlement-demo/model", methods=["GET"])
def model():
    return jsonify({"status": "success", **engine.structured_model})


@app.route("/api/settlement-demo/model-chain", methods=["GET"])
def model_chain():
    return jsonify({"status": "success", **engine.model_chain})


@app.route("/api/settlement-demo/nine-grid", methods=["GET"])
def nine_grid():
    return jsonify({"status": "success", **engine.point_line_surface})


@app.route("/api/settlement-demo/cross-model-hints", methods=["GET"])
def cross_model_hints():
    return jsonify({"status": "success", **engine.cross_model_hints})


@app.route("/api/settlement-demo/model-catalog", methods=["GET"])
def model_catalog():
    return jsonify(
        {
            "status": "success",
            "audit_model_catalog": engine.audit_model_catalog,
            "false_settlement_training": engine.false_settlement_training,
            "model_chain": engine.model_chain,
            "nine_grid": engine.point_line_surface,
            "cross_model_hints": engine.cross_model_hints,
        }
    )


@app.route("/api/settlement-demo/model/<model_id>/run", methods=["POST"])
def run_model(model_id: str):
    result = engine.run_model(model_id)
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/robot/configure", methods=["POST"])
def configure_robot():
    data = _json_body()
    result = engine.configure_robot(data.get("task_name", ""), data.get("schedule", ""), data.get("source", ""))
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/robot/run", methods=["POST"])
def run_robot():
    data = _json_body()
    result = engine.run_robot(data.get("task_name", ""), data.get("rows") or [], data.get("source", "业务系统"))
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/workflow", methods=["GET"])
def workflow():
    state = engine.export_state(_category_from_request())
    return jsonify({"status": "success", "workflow": state["workflow"], "preparation": state["preparation"], "model_runs": state["model_runs"]})


@app.route("/api/settlement-demo/relations", methods=["GET"])
def relations():
    state = engine.export_state(_category_from_request())
    return jsonify({"status": "success", "relation_chains": state["relation_chains"]})


@app.route("/api/settlement-demo/decision-analysis", methods=["GET"])
def decision_analysis():
    state = engine.export_state(_category_from_request())
    return jsonify({"status": "success", "decision_analysis": state["decision_analysis"]})


@app.route("/api/settlement-demo/analysis-snapshot", methods=["GET"])
def analysis_snapshot():
    return jsonify(engine.analysis_snapshot())


@app.route("/api/settlement-demo/scope", methods=["POST"])
def scope():
    data = _json_body()
    return jsonify(engine.set_scope(data.get("selected_org_codes"), data.get("selected_project_ids")))


@app.route("/api/settlement-demo/scope/options", methods=["GET"])
def scope_options():
    return jsonify({"status": "success", **engine.scope_options()})


@app.route("/api/settlement-demo/audit-task", methods=["POST"])
def create_audit_task():
    data = _json_body()
    result = engine.create_audit_task(
        data.get("org_code", ""),
        data.get("project_id", ""),
        data.get("contract_ids") or [],
        data.get("owner", "主审"),
    )
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/models/run-all", methods=["POST"])
def run_all_models():
    return jsonify(engine.run_all_models())


@app.route("/api/settlement-demo/model-rules", methods=["GET"])
def model_rules():
    return jsonify(engine.model_rules(request.args.get("model_id", "")))


@app.route("/api/settlement-demo/model-rules", methods=["POST"])
def save_model_rules():
    data = _json_body()
    result = engine.save_model_rules(
        data.get("model_id", ""),
        data.get("thresholds") or {},
        data.get("operator", "主审"),
    )
    return jsonify(result), (200 if result.get("status") == "success" else 400)


@app.route("/api/settlement-demo/data-quality/confirm", methods=["POST"])
def confirm_data_quality():
    data = _json_body()
    return jsonify(engine.confirm_data_quality(data.get("operator", "主审")))


@app.route("/api/settlement-demo/system-capture", methods=["POST"])
def system_capture():
    data = _json_body()
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        return jsonify({"status": "error", "message": "系统抓数必须是数组"}), 400
    meta = engine.raw_data.setdefault("meta", {})
    layers = meta.setdefault("data_layers", {})
    layers["system_capture"] = {
        "label": "系统抓数",
        "status": "已接入待校验",
        "records": len(rows),
        "source": data.get("source", "RPA/业务系统"),
        "rows": rows,
    }
    engine._rebuild()
    return jsonify({"status": "success", "message": "系统抓数已进入来源层，等待与文档和宽表交叉验证", "state": engine.export_state()})


@app.route("/api/settlement-demo/report", methods=["GET", "POST"])
def report():
    data = _json_body() if request.method == "POST" else {}
    if request.method == "POST" and "draft" in data:
        result = engine.save_report_draft(data.get("draft", ""), data.get("author", "主审"))
        return jsonify(result), (200 if result.get("status") == "success" else 400)
    return jsonify({"status": "success", "report": engine.generate_report()})


@app.route("/api/settlement-demo/export", methods=["GET"])
def export():
    fmt = request.args.get("format", "json").lower()
    state = engine.export_state(_category_from_request())
    if fmt == "csv":
        rows = ["编号,名称,单位,数量差,价差,审减金额"]
        for item in state["items"]:
            rows.append(
                ",".join(
                    [
                        item["item_code"],
                        item["name"],
                        item["unit"],
                        str(item["qty_diff_vs_actual"]),
                        str(item["price_deviation_contract"]),
                        str(item["amount_deducted"]),
                    ]
                )
            )
        return app.response_class("\n".join(rows), mimetype="text/csv")
    if fmt == "md":
        text = [
            "# 工程结算审计报告初稿",
            "",
            f"- 审减金额: {state['summary']['sum_deducted']}",
            f"- 支付超前敞口: {state['summary']['payment_gap']}",
            f"- 疑点数量: {state['summary']['issue_count']}",
        ]
        return app.response_class("\n".join(text), mimetype="text/markdown")
    return jsonify(state)


@app.route("/api/settlement-demo/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"status": "error", "message": "未收到文件"}), 400
    dataset = _build_uploaded_dataset(files)
    engine.load_dataset(dataset)
    payload = engine.export_state()
    payload.update(
        {
            "upload_activated": True,
            "active_data_source": "上传表格资料包",
            "uploaded_now": [{"fields": {"file_count": len(files)}}],
        }
    )
    return jsonify(payload)


@app.route("/api/settlement-demo/reset", methods=["POST"])
def reset():
    return jsonify(engine.import_sample_data())


@app.route("/api/comparison/<contract_id>", methods=["GET"])
def legacy_comparison(contract_id: str):
    return jsonify(engine.three_table_comparison(contract_id))


@app.route("/api/issues/generate/<contract_id>", methods=["POST"])
def legacy_issue_generate(contract_id: str):
    return jsonify(engine.generate_true_issue_package(contract_id))


@app.route("/api/issues/list", methods=["GET"])
def legacy_issue_list():
    return jsonify({"status": "success", "issues": engine.issues})


@app.route("/api/contracts/list", methods=["GET"])
def contracts_list():
    project = engine.raw_data.get("project", {})
    return jsonify(
        {
            "status": "success",
            "contracts": [
                {
                    "contract_id": project.get("id", "NF-026"),
                    "org_level": "项目",
                    "org_name": project.get("owner", ""),
                    "project_name": project.get("name", ""),
                    "owner_name": project.get("contractor", ""),
                    "contract_amount": engine.summary.get("sum_declared", 0),
                    "contract_date": project.get("sign_date", ""),
                    "completion_date": project.get("acceptance_date", ""),
                    "contract_type": "总包",
                }
            ],
        }
    )


@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    return jsonify(
        {
            "status": "success",
            "total_issues": engine.summary.get("issue_count", 0),
            "high_risk_count": engine.summary.get("high_risk_count", 0),
            "medium_risk_count": engine.summary.get("medium_risk_count", 0),
            "pending_review": sum(1 for issue in engine.issues if issue["status"] == "待主审复核"),
            "in_remediation": sum(1 for issue in engine.issues if issue["status"] == "整改中"),
            "closed": sum(1 for issue in engine.issues if issue["status"] == "已销号"),
            "total_amount_impact": engine.summary.get("risk_amount", 0),
            "chain_distribution": {chain["chain_type"]: chain["count"] for chain in []},
        }
    )


@app.route("/api/report/generate", methods=["POST"])
def generate_report():
    data = _json_body()
    return jsonify({"status": "success", "report": engine.generate_report(data.get("contract_id"), data.get("report_type", "full"))})


@app.route("/api/models/1.1/<contract_id>", methods=["GET"])
def model_1_1(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "1.1"})


@app.route("/api/models/1.2/<contract_id>", methods=["GET"])
def model_1_2(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "1.2"})


@app.route("/api/models/1.3/<contract_id>", methods=["GET"])
def model_1_3(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "1.3"})


@app.route("/api/models/1.4/<contract_id>", methods=["GET"])
def model_1_4(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "1.4"})


@app.route("/api/models/2.1/<contract_id>", methods=["GET"])
def model_2_1(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "2.1"})


@app.route("/api/models/2.2/<contract_id>", methods=["GET"])
def model_2_2(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "2.2"})


@app.route("/api/models/2.3/<contract_id>", methods=["GET"])
def model_2_3(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "2.3"})


@app.route("/api/models/2.4/<contract_id>", methods=["GET"])
def model_2_4(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "2.4"})


@app.route("/api/models/2.5/<contract_id>", methods=["GET"])
def model_2_5(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "2.5"})


@app.route("/api/models/3.1/<contract_id>", methods=["GET"])
def model_3_1(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "3.1"})


@app.route("/api/models/3.2/<contract_id>", methods=["GET"])
def model_3_2(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "3.2"})


@app.route("/api/models/3.3/<contract_id>", methods=["GET"])
def model_3_3(contract_id: str):
    return jsonify({"status": "success", "contract_id": contract_id, "model_code": "3.3"})


@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "message": "API endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"status": "error", "message": "Internal server error"}), 500


if __name__ == "__main__":
    print("=" * 80)
    print("Settlement audit web service starting on http://127.0.0.1:5100")
    print("=" * 80)
    app.run(host="0.0.0.0", port=5100, debug=True)
