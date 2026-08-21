# -*- coding: utf-8 -*-
"""Standalone engineering settlement audit agent demo.

This application is intentionally independent from the legacy benefit-audit
application. It owns its data, audit engine, template, upload workspace and
port configuration.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import csv
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_file

from settlement_audit_engine import analyze, answer_question, build_data_from_upload_previews, parse_uploaded_file


APP_DIR = Path(__file__).resolve().parent
DATA_PATH = APP_DIR / "demo_data" / "settlement_demo.json"
TEMPLATE_PATH = APP_DIR / "settlement_platform.html"
WORK_DIR = APP_DIR / "_jobs"
UPLOAD_DIR = WORK_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024


@app.after_request
def _disable_cache(response):
    # 本地演示常改前端，禁用浏览器缓存，避免刷新后仍看到旧页面
    response.headers["Cache-Control"] = "no-store"
    return response

_LOCK = threading.Lock()
_DECISIONS: dict[str, dict] = {}
_REMEDIATION_UPDATES: dict[str, dict] = {}
_EXPERT_OPINION_CACHE: dict[str, dict] = {}
_UPLOADS: list[dict] = []
_ACTIVE_DATA: dict | None = None

# 原件与结构化双栏穿透核对窗 —— 演示数据（左原件 / 右结构化字段 / 置信度 / 签章状态）
# bbox 为相对“原件纸面”的百分比坐标（x,y,w,h ∈ 0~100），前端据此定位并高亮框选。
_VIEWER_DOCS: dict[str, dict] = {
    "BG-018": {
        "title": "现场签证单 BG-018",
        "issue_id": "post-quantity-001",
        "page_hint": "第 2 页 / 地下车库土方开挖",
        "fields": [
            {"key": "签证编号", "value": "BG-018", "confidence": 0.99, "bbox": {"x": 7, "y": 9, "w": 24, "h": 5}, "status": "ok", "note": ""},
            {"key": "项目名称", "value": "南方产业园二期项目", "confidence": 0.97, "bbox": {"x": 7, "y": 17, "w": 34, "h": 5}, "status": "ok", "note": ""},
            {"key": "签证事项", "value": "土方开挖 10,000 m³", "confidence": 0.92, "bbox": {"x": 7, "y": 27, "w": 40, "h": 7}, "status": "ok", "note": ""},
            {"key": "签证金额", "value": "300,000 元", "confidence": 0.95, "bbox": {"x": 7, "y": 39, "w": 26, "h": 6}, "status": "ok", "note": ""},
            {"key": "审批链", "value": "建设、监理已签认 / 商务经理未签认", "confidence": 0.90, "bbox": {"x": 52, "y": 39, "w": 41, "h": 7}, "status": "warn", "note": "合同专用条款第92条：变更须商务审批链闭合后计价"},
        ],
        "stamps": [
            {"name": "建设单位", "state": "已盖"},
            {"name": "监理单位", "state": "已盖"},
            {"name": "施工单位·商务经理", "state": "缺失"},
        ],
        "issue_note": "命中疑点：土方开挖与合同清单同部位重复计费",
    },
    "ZQ-023": {
        "title": "变更签证单 ZQ-023",
        "issue_id": "post-change-001",
        "page_hint": "第 1-3 页 / 地下室抗浮及排水变更",
        "fields": [
            {"key": "变更编号", "value": "ZQ-023", "confidence": 0.99, "bbox": {"x": 7, "y": 9, "w": 22, "h": 5}, "status": "ok", "note": ""},
            {"key": "变更事项", "value": "地下室抗浮及排水", "confidence": 0.94, "bbox": {"x": 7, "y": 17, "w": 36, "h": 6}, "status": "ok", "note": ""},
            {"key": "变更金额", "value": "800,000 元", "confidence": 0.96, "bbox": {"x": 7, "y": 28, "w": 24, "h": 6}, "status": "ok", "note": ""},
            {"key": "审批链", "value": "缺项目商务经理签认", "confidence": 0.91, "bbox": {"x": 50, "y": 28, "w": 43, "h": 7}, "status": "warn", "note": "审批链不完整即进入结算，存在超前计价风险"},
            {"key": "计价状态", "value": "已进入结算申报", "confidence": 0.89, "bbox": {"x": 7, "y": 40, "w": 30, "h": 6}, "status": "warn", "note": ""},
        ],
        "stamps": [
            {"name": "建设单位", "state": "已盖"},
            {"name": "监理单位", "state": "已盖"},
            {"name": "施工单位·商务经理", "state": "缺失"},
        ],
        "issue_note": "命中疑点：变更审批链未闭合即进入结算",
    },
    "NF-026": {
        "title": "工程结算书 NF-026（分部分项）",
        "issue_id": "post-price-001",
        "page_hint": "Sheet 分部分项 / 第 33 行 / 清单 010501002",
        "fields": [
            {"key": "清单子目", "value": "010501002 C30 混凝土", "confidence": 0.98, "bbox": {"x": 7, "y": 10, "w": 42, "h": 6}, "status": "ok", "note": ""},
            {"key": "合同单价", "value": "400 元/m³", "confidence": 0.97, "bbox": {"x": 7, "y": 20, "w": 24, "h": 5}, "status": "ok", "note": ""},
            {"key": "结算单价", "value": "447.5 元/m³", "confidence": 0.93, "bbox": {"x": 7, "y": 29, "w": 26, "h": 6}, "status": "warn", "note": "偏离合同 11.88%，超过单价阈值"},
            {"key": "同期市场价", "value": "420 元/m³", "confidence": 0.90, "bbox": {"x": 7, "y": 38, "w": 24, "h": 5}, "status": "ok", "note": ""},
            {"key": "调价依据", "value": "未见审批单", "confidence": 0.88, "bbox": {"x": 48, "y": 38, "w": 42, "h": 6}, "status": "warn", "note": ""},
        ],
        "stamps": [
            {"name": "编制单位", "state": "已盖"},
            {"name": "复核单位", "state": "缺失"},
        ],
        "issue_note": "命中疑点：结算单价高于合同价与市场参考价",
    },
}


def _load_data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _state() -> dict:
    base_data = _load_data()
    with _LOCK:
        decisions = dict(_DECISIONS)
        remediation_updates = dict(_REMEDIATION_UPDATES)
        uploads = list(_UPLOADS)
        active_data = _ACTIVE_DATA
    data = active_data or base_data
    result = analyze(data, decisions=decisions, remediation_updates=remediation_updates)
    issue_packages = {}
    screened_issue_ids: list[str] = []
    for issue in result.get("issues", []):
        package = _issue_analysis_package(result, issue)
        issue_packages[str(issue.get("id", ""))] = package
        if package.get("screening", {}).get("keep_for_chief_review"):
            screened_issue_ids.append(str(issue.get("id", "")))
    result["decisions"] = decisions
    result["remediation_updates"] = remediation_updates
    result["uploads"] = uploads
    result["active_data_source"] = data.get("meta", {}).get("data_source", "内置演示数据")
    result["issue_analysis_packages"] = issue_packages
    result["screened_issue_ids"] = screened_issue_ids
    result["analysis_summary"] = {
        "issue_count": len(result.get("issues", [])),
        "screened_count": len(screened_issue_ids),
        "filtered_count": max(0, len(result.get("issues", [])) - len(screened_issue_ids)),
        "conference_count": sum(1 for issue in result.get("issues", []) if _conference_recommendation(result, issue, issue_packages[str(issue.get("id", ""))]["analysis"]).get("should_open")),
    }
    return result


def _safe_name(name: str, fallback: str) -> str:
    value = os.path.basename((name or "").strip())
    value = re.sub(r"[\x00-\x1f]", "_", value)
    value = re.sub(r'[<>:"/\\|?*]', "_", value).strip(" .")
    return value or fallback


def _save_upload(file_storage, target: Path, index: int) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    filename = _safe_name(file_storage.filename, f"upload_{index}")
    path = target / filename
    file_storage.save(path)
    return path


def _as_number(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _clear_expert_cache() -> None:
    with _LOCK:
        _EXPERT_OPINION_CACHE.clear()


@app.get("/")
def index():
    return settlement_demo()


@app.get("/settlement-demo")
def settlement_demo():
    if not TEMPLATE_PATH.exists():
        abort(404, description="settlement_platform.html not found")
    return render_template_string(TEMPLATE_PATH.read_text(encoding="utf-8"))


@app.get("/api/settlement-demo/state")
def state():
    try:
        return jsonify(_state())
    except Exception as exc:
        return jsonify({"error": f"结算审计状态生成失败：{exc}"}), 500


@app.get("/api/settlement-demo/model")
def model():
    try:
        return jsonify(_state().get("structured_model", {}))
    except Exception as exc:
        return jsonify({"error": f"结构化模型生成失败：{exc}"}), 500


@app.get("/api/settlement-demo/model-chain")
def model_chain():
    try:
        return jsonify(_state().get("model_chain", {}))
    except Exception as exc:
        return jsonify({"error": f"模型链生成失败：{exc}"}), 500


@app.get("/api/settlement-demo/nine-grid")
def nine_grid():
    try:
        return jsonify(_state().get("nine_grid", {}))
    except Exception as exc:
        return jsonify({"error": f"九宫格判定生成失败：{exc}"}), 500


@app.get("/api/settlement-demo/cross-model-hints")
def cross_model_hints():
    try:
        return jsonify(_state().get("cross_model_hints", {}))
    except Exception as exc:
        return jsonify({"error": f"跨模型证据链生成失败：{exc}"}), 500


@app.get("/api/settlement-demo/agents")
def agents():
    try:
        return jsonify(_state().get("agent_orchestration", {}))
    except Exception as exc:
        return jsonify({"error": f"智能体编排生成失败：{exc}"}), 500


@app.get("/api/settlement-demo/model-catalog")
def model_catalog():
    try:
        result = _state()
        return jsonify({
            "audit_model_catalog": result.get("audit_model_catalog", {}),
            "false_settlement_training": result.get("false_settlement_training", {}),
            "model_chain": result.get("model_chain", {}),
            "nine_grid": result.get("nine_grid", {}),
            "cross_model_hints": result.get("cross_model_hints", {}),
        })
    except Exception as exc:
        return jsonify({"error": f"模型库生成失败：{exc}"}), 500


@app.post("/api/settlement-demo/analyze")
def run_analysis():
    try:
        return jsonify(_state())
    except Exception as exc:
        return jsonify({"error": f"三表比对执行失败：{exc}"}), 500


@app.post("/api/settlement-demo/upload")
def upload():
    global _ACTIVE_DATA
    files = [item for item in request.files.getlist("files") if item and item.filename]
    if not files:
        return jsonify({"error": "请上传招标文件、合同、变更签证、竣工资料、结算书或资料包"}), 400

    batch = UPLOAD_DIR / datetime.now().strftime("%Y%m%d%H%M%S")
    parsed = []
    for index, item in enumerate(files, 1):
        try:
            path = _save_upload(item, batch, index)
            preview = parse_uploaded_file(path)
            preview["saved_path"] = str(path.relative_to(APP_DIR))
            parsed.append(preview)
        except Exception as exc:
            parsed.append({
                "name": item.filename,
                "type": Path(item.filename).suffix.lstrip("."),
                "document_type": "未分类资料",
                "fields": {},
                "rows": [],
                "warnings": [f"解析失败：{exc}"],
            })

    base_data = _load_data()
    active_data, activated = build_data_from_upload_previews(base_data, parsed)
    stored = []
    for item in parsed:
        clean = dict(item)
        clean.pop("structured_data", None)
        if activated:
            clean.setdefault("warnings", []).append("已纳入当前审计分析数据集。")
        stored.append(clean)

    with _LOCK:
        _UPLOADS.extend(stored)
        del _UPLOADS[:-30]
        if activated:
            _ACTIVE_DATA = active_data
    _clear_expert_cache()
    result = _state()
    result["uploaded_now"] = stored
    result["upload_activated"] = activated
    return jsonify(result)


@app.post("/api/settlement-demo/reset")
def reset_demo():
    global _ACTIVE_DATA
    with _LOCK:
        _ACTIVE_DATA = None
        _UPLOADS.clear()
        _DECISIONS.clear()
        _REMEDIATION_UPDATES.clear()
        _EXPERT_OPINION_CACHE.clear()
    return jsonify(_state())


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:1.5b")


def _extract_ollama_text(data: dict) -> str | None:
    msg = data.get("message", {}) or {}
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()
    return content or None


def _ollama_chat(messages: list[dict], model: str | None = None, temperature: float = 0.2, num_predict: int = 240, timeout: int = 90) -> dict | None:
    payload = json.dumps({
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"content": _extract_ollama_text(data), "raw": data}
    except Exception:
        return None


def _load_jsonish(text: str) -> object | None:
    candidate = (text or "").strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except Exception:
        pass
    if "```" in candidate:
        fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.S | re.I)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except Exception:
                pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except Exception:
            pass
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start:end + 1])
        except Exception:
            pass
    return None


def _role_brief(role: str) -> dict[str, str]:
    mapping = {
        "造价专家": {"focus": "清单计价、工程量、综合单价、核减金额", "risk": "工程量重复、单价偏离、取费失真"},
        "商务专家": {"focus": "合同、变更、索赔、奖罚款、程序效力", "risk": "审批链不闭合、费用重叠、合同扣减缺失"},
        "履约专家": {"focus": "实际完成量、竣工验收、现场收方、移交证据", "risk": "虚列子目、完成量不足、验收证据缺失"},
        "招采供应链专家": {"focus": "市场参考价、材料损耗、供应链价格", "risk": "材料异常、市场价偏离、损耗失真"},
        "财务专家": {"focus": "支付、成本归集、现金流、应扣未扣", "risk": "支付超前、奖罚遗漏、费项归集失真"},
        "法务专家": {"focus": "授权、审批程序、合同效力、责任划分", "risk": "越权审批、程序瑕疵、索赔效力不足"},
    }
    return mapping.get(role, {"focus": "审计事实、证据链和风险控制", "risk": "证据不充分、结论不稳健"})


def _issue_analysis_brief(result: dict, issue: dict) -> dict[str, object]:
    text = " ".join(str(part) for part in [
        issue.get("title", ""),
        issue.get("category", ""),
        issue.get("rule", ""),
        issue.get("judgement", ""),
        issue.get("suggestion", ""),
    ] if part)
    keywords = {
        "工程量": ["工程量", "重复", "清单", "签证", "收方", "实际完成量"],
        "单价": ["单价", "市场价", "调价", "综合单价", "报价"],
        "变更签证": ["变更", "签证", "审批链", "授权", "程序"],
        "实际完成量": ["竣工", "验收", "移交", "现场", "完成量"],
        "索赔": ["索赔", "补偿", "费用重叠", "未批", "批准"],
        "奖罚款": ["奖励", "罚款", "扣减", "应扣", "未扣"],
        "取费": ["取费", "管理费", "费率", "费项", "间接费"],
        "跑冒滴漏": ["损耗", "台班", "领用", "退库", "盘点"],
    }
    focus_map = {
        "工程量": "核对结算工程量、合同工程量与实际完成量是否一致，优先看重复计费与工程量偏差。",
        "单价": "核对结算单价、合同单价和市场参考价是否一致，优先看调价依据和超阈值偏离。",
        "变更签证": "核对变更签证审批链、授权效力和计价程序是否闭合。",
        "实际完成量": "核对竣工验收、现场收方和移交证据是否足以支持申报量。",
        "索赔": "核对索赔事实、批复边界和合同约定，识别未批先计或重叠计价。",
        "奖罚款": "核对奖罚款是否已扣、是否重复计入结算以及责任划分是否明确。",
        "取费": "核对管理费、措施费和费率基数是否符合合同及专项审批。",
        "跑冒滴漏": "核对材料损耗、机械台班和管理费异常，识别跑冒滴漏科目。",
    }
    category = str(issue.get("category") or "").strip()
    issue_type = category or "综合结算疑点"
    hit_words = [word for word in keywords.get(category, []) if word and word in text]
    if not hit_words:
        for name, words in keywords.items():
            if any(word in text for word in words):
                issue_type = name
                hit_words = [word for word in words if word in text]
                break
    if not hit_words:
        hit_words = [category] if category else ["结算疑点"]
    roles = _expert_roles_for_issue(result, issue)
    return {
        "issue_id": issue.get("id", ""),
        "issue_title": issue.get("title", ""),
        "issue_type": issue_type,
        "risk_level": issue.get("risk", ""),
        "risk_amount": issue.get("amount", 0),
        "analysis_focus": focus_map.get(category, "围绕证据链、合同条款和金额偏差展开会谈。"),
        "keywords": hit_words[:4],
        "recommended_roles": roles,
        "question_slots": [
            "问题本质是什么",
            "证据链缺口在哪里",
            "是否存在程序或合同效力风险",
            "主审下一步应如何处理",
        ],
        "knowledge_mode": "问题驱动的专家会谈",
    }


def _screen_issue_candidate(result: dict, issue: dict, analysis: dict[str, object] | None = None) -> dict[str, object]:
    analysis = analysis or _issue_analysis_brief(result, issue)
    risk = str(issue.get("risk") or "")
    category = str(issue.get("category") or "")
    amount = _as_number(issue.get("amount"))
    evidence_count = len(issue.get("evidence", []) or [])
    title = str(issue.get("title") or "")
    score = 35
    if risk == "高风险":
        score += 35
    elif risk == "中风险":
        score += 22
    else:
        score += 8
    if amount >= 300000:
        score += 15
    elif amount >= 100000:
        score += 10
    elif amount >= 50000:
        score += 5
    if evidence_count >= 3:
        score += 10
    elif evidence_count >= 2:
        score += 6
    elif evidence_count == 1:
        score += 2
    if category in {"工程量", "单价", "变更签证", "实际完成量", "索赔", "奖罚款", "取费", "跑冒滴漏"}:
        score += 10
    if any(word in title for word in ("重复", "偏离", "缺失", "未批", "虚列", "异常")):
        score += 5
    keep_for_chief_review = score >= 65 or risk == "高风险"
    reason = "高风险且证据链足以进入主审复核。" if keep_for_chief_review else "当前更适合先留作知识问答或补证观察。"
    if amount < 50000 and evidence_count <= 1 and risk != "高风险":
        reason = "金额和证据强度偏弱，暂不建议直接上主审。"
        keep_for_chief_review = False
    return {
        "screen_score": min(score, 100),
        "keep_for_chief_review": keep_for_chief_review,
        "screen_label": "保留主审复核" if keep_for_chief_review else "先观察/问答",
        "screen_reason": reason,
        "evidence_count": evidence_count,
        "amount_level": "高" if amount >= 300000 else "中" if amount >= 100000 else "低",
        "analysis_focus": analysis.get("analysis_focus", ""),
    }


def _conference_recommendation(result: dict, issue: dict, analysis: dict[str, object]) -> dict[str, object]:
    risk = str(issue.get("risk") or "")
    category = str(issue.get("category") or "")
    should_open = risk == "高风险" or category in {"变更签证", "索赔", "奖罚款", "跑冒滴漏", "单价"}
    reason = {
        "高风险": "高风险事项建议拉起专家圆桌复核。",
        "变更签证": "变更签证存在程序和效力争议，适合专家会谈。",
        "索赔": "索赔事项需要商务、法务和财务联合判断。",
        "奖罚款": "奖罚款常有扣减边界与归集口径争议。",
        "跑冒滴漏": "跑冒滴漏科目通常涉及材料、台班和损耗口径。",
        "单价": "单价偏离需要造价与供应链专家联合说明。",
    }.get(risk if should_open else category, "当前事项可先由问题分析模型和知识库问答完成。")
    return {
        "should_open": should_open,
        "reason": reason,
        "mode": "专家圆桌" if should_open else "知识库问答优先",
        "suggested_roles": analysis.get("recommended_roles", []),
        "suggested_questions": analysis.get("question_slots", []),
    }


def _issue_analysis_package(result: dict, issue: dict) -> dict[str, object]:
    analysis = _issue_analysis_brief(result, issue)
    screening = _screen_issue_candidate(result, issue, analysis)
    conference = _conference_recommendation(result, issue, analysis)
    knowledge_prompt = f"围绕{analysis.get('issue_type', issue.get('category', '结算疑点'))}，解释{issue.get('title', '')}是否可以进入结算，以及应补充哪些证据。"
    return {
        "issue": issue,
        "analysis": analysis,
        "screening": screening,
        "conference": conference,
        "knowledge_prompt": knowledge_prompt,
    }


def _expert_roles_for_issue(result: dict, issue: dict) -> list[str]:
    roles: list[str] = []
    priority = {
        "工程量": ["造价专家", "商务专家", "履约专家"],
        "单价": ["造价专家", "招采供应链专家", "财务专家"],
        "实际完成量": ["履约专家", "造价专家", "商务专家"],
        "变更签证": ["商务专家", "法务专家", "造价专家"],
        "索赔": ["商务专家", "法务专家", "财务专家"],
        "奖罚款": ["商务专家", "财务专家", "法务专家"],
        "取费": ["造价专家", "财务专家", "商务专家"],
        "跑冒滴漏": ["招采供应链专家", "履约专家", "财务专家"],
    }.get(issue.get("category"), ["造价专家", "商务专家", "履约专家"])
    for role in priority:
        if role not in roles:
            roles.append(role)
    return roles[:4]


def _expert_context_text(result: dict, issue: dict) -> str:
    comparisons = {item.get("item_code"): item for item in result.get("comparisons", [])}
    comp = comparisons.get(issue.get("item_code"), {})
    analysis = _issue_analysis_brief(result, issue)
    evidence = issue.get("evidence", []) or []
    evidence_lines = [
        f"- {item.get('document', '')} | {item.get('location', '')} | {item.get('fact', '')}"
        for item in evidence[:6]
    ]
    knowledge = result.get("knowledge", []) or []
    issue_text = " ".join(str(part) for part in [issue.get("title", ""), issue.get("rule", ""), issue.get("judgement", ""), issue.get("suggestion", "")] if part)
    related_knowledge = [
        f"- 《{item.get('title', '')}》{item.get('basis', '')}：{item.get('answer', '')}"
        for item in knowledge
        if any(word and word in issue_text for word in item.get("keywords", []))
    ][:3]
    if not related_knowledge:
        related_knowledge = [f"- 《{item.get('title', '')}》{item.get('basis', '')}：{item.get('answer', '')}" for item in knowledge[:2]]
    comparison_text = ""
    if comp:
        comparison_text = (
            f"招标量 {comp.get('bid', {}).get('quantity', '')}，合同量 {comp.get('contract', {}).get('quantity', '')}，"
            f"实际完成量 {comp.get('actual', {}).get('quantity', '')}，结算量 {comp.get('settlement', {}).get('quantity', '')}；"
            f"合同单价 {comp.get('contract', {}).get('unit_price', '')}，结算单价 {comp.get('settlement', {}).get('unit_price', '')}，"
            f"市场价 {comp.get('market_unit_price', '')}。"
        )
    return "\n".join([
        f"项目：{result.get('project', {}).get('name', '')} / {result.get('project', {}).get('phase', '')}",
        f"疑点：{issue.get('id', '')} / {issue.get('title', '')}",
        f"问题画像：{analysis.get('issue_type', '')} / {analysis.get('analysis_focus', '')}",
        f"类别：{issue.get('category', '')} / 风险：{issue.get('risk', '')} / 金额：{issue.get('amount', 0)} 元",
        f"规则：{issue.get('rule', '')}",
        f"判断：{issue.get('judgement', '')}",
        f"建议：{issue.get('suggestion', '')}",
        f"三表比对：{comparison_text or '无对应清单'}",
        f"证据：\n{chr(10).join(evidence_lines) if evidence_lines else '- 无'}",
        f"制度依据：\n{chr(10).join(related_knowledge)}",
    ])


def _fallback_expert_opinion(role: str, issue: dict, result: dict) -> dict:
    static = result.get("expert_opinions", {}).get(issue.get("id"), [])
    for item in static:
        if item.get("expert") == role:
            opinion = dict(item)
            opinion["source"] = "static"
            return opinion
    brief = _role_brief(role)
    category = issue.get("category", "")
    risk = issue.get("risk", "中")
    recommendation = {
        "造价专家": "按合同、变更和实际完成量复核工程量与单价，先做核减或补证。",
        "商务专家": "补齐审批链与合同扣减关系，未闭合前不应直接进入结算。",
        "履约专家": "补充现场收方、验收或移交证据，确认实际完成量。",
        "招采供应链专家": "核对市场参考价、材料损耗和供应链对账记录。",
        "财务专家": "勾稽支付、奖罚和管理费归集，防止超前支付和遗漏扣减。",
        "法务专家": "核查授权和程序效力，明确未闭合事项的法律风险。",
    }.get(role, "补充证据链后再由主审裁量。")
    opinion = {
        "expert": role,
        "fact": f"{role}基于当前{category}疑点进行复核，核心事实仍指向{issue.get('title', '')}。",
        "basis": brief.get("focus", ""),
        "judgement": f"从{role}视角看，该事项{ '存在' if risk == '高风险' else '仍需' }进一步核验。",
        "risk": "高" if risk == "高风险" else "中" if risk == "中风险" else "低",
        "recommendation": recommendation,
        "status": "待补证" if role != "法务专家" else "待核验",
        "source": "fallback",
        "stance": "支持主审继续核验",
    }
    return opinion


def _generate_expert_opinion(role: str, issue: dict, result: dict) -> dict:
    analysis = _issue_analysis_brief(result, issue)
    prompt = (
        f"你是工程结算审计中的{role}。"
        f"你的专业关注点：{_role_brief(role).get('focus', '')}。"
        f"当前问题画像：{analysis.get('issue_type', '')}；会谈关注点：{analysis.get('analysis_focus', '')}。"
        "请严格依据给定材料输出 JSON 对象，不要输出额外解释。"
        "JSON 字段必须包括 expert, fact, basis, judgement, risk, recommendation, status, stance。"
        "其中 risk 只能是 高/中/低，status 只能是 已确认/待补证/待核验。"
        "\n\n【材料】\n"
        f"{_expert_context_text(result, issue)}"
    )
    response = _ollama_chat(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        num_predict=280,
    )
    if not response or not response.get("content"):
        return _fallback_expert_opinion(role, issue, result)
    parsed = _load_jsonish(response.get("content", ""))
    if isinstance(parsed, dict):
        opinion = {
            "expert": str(parsed.get("expert") or role).strip() or role,
            "fact": str(parsed.get("fact") or "").strip(),
            "basis": str(parsed.get("basis") or "").strip(),
            "judgement": str(parsed.get("judgement") or "").strip(),
            "risk": str(parsed.get("risk") or "").strip() or "中",
            "recommendation": str(parsed.get("recommendation") or "").strip(),
            "status": str(parsed.get("status") or "").strip() or "待补证",
            "stance": str(parsed.get("stance") or "").strip() or "支持主审继续核验",
            "source": "ollama",
        }
        if not opinion["fact"] or not opinion["judgement"]:
            return _fallback_expert_opinion(role, issue, result)
        return opinion
    return _fallback_expert_opinion(role, issue, result)


def _build_llm_expert_opinions(result: dict, issue: dict, force_refresh: bool = False) -> dict:
    analysis = _issue_analysis_brief(result, issue)
    cache_key = json.dumps({
        "issue_id": issue.get("id", ""),
        "decision": result.get("decisions", {}).get(issue.get("id", ""), {}),
        "remediation": result.get("remediation_updates", {}).get(issue.get("id", ""), {}),
        "source": result.get("active_data_source", "内置演示数据"),
        "model": OLLAMA_MODEL,
        "analysis": analysis,
    }, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        if not force_refresh and cache_key in _EXPERT_OPINION_CACHE:
            return _EXPERT_OPINION_CACHE[cache_key]
    roles = _expert_roles_for_issue(result, issue)
    opinions = [_generate_expert_opinion(role, issue, result) for role in roles]
    payload = {
        "issue_id": issue.get("id", ""),
        "issue_title": issue.get("title", ""),
        "analysis_model": analysis,
        "source": "ollama" if any(item.get("source") == "ollama" for item in opinions) else "fallback",
        "model": OLLAMA_MODEL,
        "cache_key": cache_key,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "conference_title": f"{analysis.get('issue_type', '问题')}专家会谈",
        "opinions": opinions,
        "conference": opinions,
        "conclusion": {
            "stance": "建议主审复核后纳入报告" if any(item.get("risk") == "高" for item in opinions) else "建议补证后再定",
            "focus": analysis.get("analysis_focus", ""),
            "next_step": "围绕证据链缺口继续补证并由主审裁量。",
        },
    }
    with _LOCK:
        _EXPERT_OPINION_CACHE[cache_key] = payload
    return payload


def _ask_ollama(question: str, knowledge: list, issue: dict | None) -> str | None:
    """调用本地 Ollama LLM 基于制度条款作答；失败返回 None 供调用方回退关键词检索。"""
    kb_lines = [f"{i}.《{k.get('title','')}》{k.get('basis','')}：{k.get('answer','')}" for i, k in enumerate(knowledge, 1)]
    kb_text = "\n".join(kb_lines) if kb_lines else "（暂无制度条款）"
    issue_text = ""
    if issue:
        issue_text = f"当前审计疑点：{issue.get('title','')}（金额 {issue.get('amount',0)} 元），判定规则：{issue.get('rule','')}。"
    prompt = (
        "你是工程结算审计知识库助手，只能依据下面提供的制度条款回答，不得编造。\n\n"
        f"【制度条款】\n{kb_text}\n\n"
        f"【当前疑点】\n{issue_text or '无'}\n\n"
        f"【用户问题】\n{question}\n\n"
        "请用简洁中文直接给出结论、依据条款和补证建议，不要输出思考过程。"
    )
    response = _ollama_chat([{"role": "user", "content": prompt}], temperature=0.2, num_predict=200)
    if not response:
        return None
    return response.get("content")


@app.post("/api/settlement-demo/knowledge")
def knowledge():
    payload = request.get_json(silent=True) or {}
    result = _state()
    issue = next((item for item in result["issues"] if item["id"] == payload.get("issue_id")), None)
    question = payload.get("question", "")
    llm_answer = _ask_ollama(question, result["knowledge"], issue)
    if llm_answer:
        return jsonify({"answer": llm_answer, "source": "ollama"})
    return jsonify(answer_question(question, result["knowledge"], issue))


@app.get("/api/settlement-demo/expert-opinions/<issue_id>")
def expert_opinions(issue_id: str):
    result = _state()
    issue = next((item for item in result["issues"] if item["id"] == issue_id), None)
    if not issue:
        return jsonify({"error": "缺少或无效的疑点编号"}), 404
    force_refresh = request.args.get("refresh") == "1"
    payload = _build_llm_expert_opinions(result, issue, force_refresh=force_refresh)
    payload["issue"] = issue
    return jsonify(payload)


@app.get("/api/settlement-demo/issue-analysis/<issue_id>")
def issue_analysis(issue_id: str):
    result = _state()
    issue = next((item for item in result["issues"] if item["id"] == issue_id), None)
    if not issue:
        return jsonify({"error": "缺少或无效的疑点编号"}), 404
    return jsonify(_issue_analysis_package(result, issue))


@app.post("/api/settlement-demo/decision")
def decision():
    payload = request.get_json(silent=True) or {}
    issue_id = str(payload.get("issue_id") or "").strip()
    if not issue_id:
        return jsonify({"error": "缺少疑点编号"}), 400
    with _LOCK:
        _DECISIONS[issue_id] = {
            "decision": str(payload.get("decision") or "confirm"),
            "note": str(payload.get("note") or "").strip(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    _clear_expert_cache()
    return jsonify(_state())


@app.post("/api/settlement-demo/remediation")
def remediation():
    payload = request.get_json(silent=True) or {}
    issue_id = str(payload.get("issue_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    if not issue_id and task_id.startswith("rem-"):
        issue_id = task_id[4:]
    if not issue_id:
        return jsonify({"error": "缺少疑点编号或整改任务编号"}), 400
    status = str(payload.get("status") or "整改中").strip()
    allowed_statuses = {"待整改", "整改中", "整改验证", "已销号", "退回补充"}
    if status not in allowed_statuses:
        return jsonify({"error": "整改状态不在允许范围内"}), 400

    current = _state()
    task = next((item for item in current.get("remediation_tasks", {}).get("tasks", []) if item.get("issue_id") == issue_id), None)
    if not task:
        return jsonify({"error": "该疑点尚未由主审确认，不能生成整改任务"}), 400

    with _LOCK:
        _REMEDIATION_UPDATES[issue_id] = {
            "status": status,
            "note": str(payload.get("note") or "").strip(),
            "operator": str(payload.get("operator") or "主审").strip(),
            "owner": str(payload.get("owner") or task.get("owner") or "").strip(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    _clear_expert_cache()
    return jsonify(_state())


def _export_payload(result: dict) -> dict:
    return {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "project": result.get("project", {}),
        "summary": result.get("summary", {}),
        "cost_aggregation": result.get("cost_aggregation", {}),
        "fraud_assessment": result.get("fraud_assessment", {}),
        "false_settlement_training": result.get("false_settlement_training", {}),
        "audit_model_catalog": result.get("audit_model_catalog", {}),
        "agent_orchestration": result.get("agent_orchestration", {}),
        "structured_model": result.get("structured_model", {}),
        "issues": result.get("issues", []),
        "report": result.get("report", {}),
        "decisions": result.get("decisions", {}),
        "remediation_tasks": result.get("remediation_tasks", {}),
    }


def _csv_issues(result: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["编号", "阶段", "类别", "风险", "事项", "金额", "规则", "判断", "建议", "主审状态"],
    )
    writer.writeheader()
    decisions = result.get("decisions", {})
    for issue in result.get("issues", []):
        decision = decisions.get(issue.get("id", ""), {})
        writer.writerow({
            "编号": issue.get("id", ""),
            "阶段": issue.get("phase", ""),
            "类别": issue.get("category", ""),
            "风险": issue.get("risk", ""),
            "事项": issue.get("title", ""),
            "金额": issue.get("amount", 0),
            "规则": issue.get("rule", ""),
            "判断": issue.get("judgement", ""),
            "建议": issue.get("suggestion", ""),
            "主审状态": decision.get("decision", issue.get("chief_status", "")),
        })
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _markdown_report(result: dict) -> bytes:
    project = result.get("project", {})
    summary = result.get("summary", {})
    cost = result.get("cost_aggregation", {})
    fraud = result.get("fraud_assessment", {})
    training = result.get("false_settlement_training", {})
    catalog = result.get("audit_model_catalog", {})
    report = result.get("report", {})
    decisions = result.get("decisions", {})
    remediation = result.get("remediation_tasks", {})
    remediation_summary = remediation.get("summary", {})
    lines = [
        f"# {report.get('title', '工程结算审计报告（草稿）')}",
        "",
        f"- 项目：{project.get('name', '')}",
        f"- 合同编号：{project.get('id', '')}",
        f"- 数据源：{result.get('active_data_source', '内置演示数据')}",
        f"- 报告状态：{report.get('status', '')}",
        "",
        "## 一、审计摘要",
        "",
        f"- 疑点数量：{summary.get('issue_count', 0)}",
        f"- 高风险疑点：{summary.get('high_risk_count', 0)}",
        f"- 规则测算风险金额：{summary.get('risk_amount', 0):,} 元",
        f"- 支付超前风险：{summary.get('payment_gap', 0):,} 元",
        "",
        "## 二、费用归集",
        "",
        f"- 合同总价：{cost.get('contract_total', 0):,} 元",
        f"- 批准变更累计：{cost.get('approved_change_total', 0):,} 元",
        f"- 索赔进入结算：{cost.get('claims_settlement', 0):,} 元，其中未批准 {cost.get('unapproved_claim_amount', 0):,} 元",
        f"- 罚款未扣：{cost.get('penalty_omission', 0):,} 元",
        f"- 管理费超额：{cost.get('management_fee_excess', 0):,} 元",
        "",
        "## 三、虚假结算识别",
        "",
        f"- 模型：{fraud.get('model', '')}",
        f"- 评分：{fraud.get('score', 0)} / 100",
        f"- 等级：{fraud.get('level', '')}",
        f"- 命中说明：{fraud.get('explanation', '')}",
        f"- 训练样本：{training.get('sample_count', 0)} 条，正样本 {training.get('positive_cases', 0)} 条，负样本 {training.get('negative_cases', 0)} 条",
        f"- 学习特征：{'、'.join(training.get('top_features', []))}",
        "",
        "## 四、全生命周期模型库",
        "",
        f"- 模型总数：{catalog.get('summary', {}).get('model_count', 0)}",
        f"- 已命中模型：{catalog.get('summary', {}).get('active_model_count', 0)}",
        f"- 业务端数量：{catalog.get('summary', {}).get('business_end_count', 0)}",
        "",
        "| 模型编号 | 阶段 | 类型 | 业务端 | 模型名称 | 命中 | 风险金额 |",
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for model in catalog.get("models", []):
        lines.append(
            f"| {model.get('id', '')} | {model.get('phase', '')} | {model.get('model_type', '')} | "
            f"{model.get('business_end', '')} | {model.get('name', '')} | "
            f"{model.get('hit_count', 0)} | {model.get('risk_amount', 0):,} |"
        )
    lines.extend([
        "",
        "## 五、疑点清单",
        "",
        "| 编号 | 风险 | 类别 | 事项 | 金额 | 主审状态 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ])
    for issue in result.get("issues", []):
        decision = decisions.get(issue.get("id", ""), {})
        lines.append(
            f"| {issue.get('id', '')} | {issue.get('risk', '')} | {issue.get('category', '')} | "
            f"{issue.get('title', '')} | {issue.get('amount', 0):,} | "
            f"{decision.get('decision', issue.get('chief_status', ''))} |"
        )
    lines.extend([
        "",
        "## 六、整改闭环",
        "",
        f"- 已生成整改任务：{remediation_summary.get('total', 0)} 项",
        f"- 待整改金额：{remediation_summary.get('open_amount', 0):,} 元",
        "",
        "| 整改编号 | 关联疑点 | 责任部门 | 状态 | 金额 |",
        "| --- | --- | --- | --- | ---: |",
    ])
    for task in remediation.get("tasks", []):
        lines.append(
            f"| {task.get('task_id', '')} | {task.get('title', '')} | {task.get('owner', '')} | "
            f"{task.get('status', '')} | {task.get('amount', 0):,} |"
        )
    lines.extend([
        "",
        "## 七、主审提示",
        "",
        "智能体生成的是规则测算、证据链和专家协同建议，正式审计结论必须由主审人工确认后签发。",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


@app.get("/api/settlement-demo/export")
def export():
    try:
        result = _state()
        fmt = (request.args.get("format") or "json").lower()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if fmt == "csv":
            buf = io.BytesIO(_csv_issues(result))
            buf.seek(0)
            return send_file(buf, mimetype="text/csv; charset=utf-8", as_attachment=True, download_name=f"工程结算审计疑点清单_{stamp}.csv")
        if fmt in {"md", "markdown"}:
            buf = io.BytesIO(_markdown_report(result))
            buf.seek(0)
            return send_file(buf, mimetype="text/markdown; charset=utf-8", as_attachment=True, download_name=f"工程结算审计报告草稿_{stamp}.md")
        payload = _export_payload(result)
        buf = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        buf.seek(0)
        return send_file(
            buf,
            mimetype="application/json",
            as_attachment=True,
            download_name=f"工程结算审计导出_{stamp}.json",
        )
    except Exception as exc:
        return jsonify({"error": f"导出失败：{exc}"}), 500


@app.get("/api/settlement-demo/viewer/<doc_key>")
def viewer(doc_key: str):
    doc = _VIEWER_DOCS.get(doc_key)
    if not doc:
        abort(404, description="未找到对应原件视图")
    return jsonify(doc)


if __name__ == "__main__":
    port = int(os.environ.get("SETTLEMENT_PORT", "5100"))
    print(f"工程结算审计智能体已启动：http://127.0.0.1:{port}/settlement-demo")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
