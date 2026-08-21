#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结算审核智能体 - Web后端服务
轻量级 Flask 服务，提供 REST API 与静态资源托管
端口: 5100
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from settlement_audit_engine import SettlementAuditEngine
import json
import os
from datetime import datetime
import uuid

app = Flask(__name__, static_folder='.')
CORS(app)

# 初始化审计引擎
engine = SettlementAuditEngine()

# ============================================================================
# 静态资源路由
# ============================================================================

@app.route('/')
def index():
    """托管前端页面"""
    return send_from_directory('.', 'settlement_platform.html')

@app.route('/<path:path>')
def static_files(path):
    """托管其他静态资源"""
    return send_from_directory('.', path)

# ============================================================================
# API 路由 - 数据初始化
# ============================================================================

@app.route('/api/init', methods=['POST'])
def init_data():
    """初始化示例数据"""
    try:
        result = engine.import_sample_data()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# API 路由 - 11个核心模型
# ============================================================================

@app.route('/api/models/1.1/<contract_id>', methods=['GET'])
def model_1_1(contract_id):
    """模型1.1: 确权率偏离度"""
    result = engine.model_1_1_authorization_rate_deviation(contract_id)
    return jsonify(result)

@app.route('/api/models/1.2/<contract_id>', methods=['GET'])
def model_1_2(contract_id):
    """模型1.2: 久竣未结时间型亏损"""
    result = engine.model_1_2_delay_settlement_time_loss(contract_id)
    return jsonify(result)

@app.route('/api/models/1.3/<contract_id>', methods=['GET'])
def model_1_3(contract_id):
    """模型1.3: 签证滞后/索赔失脱"""
    result = engine.model_1_3_visa_claim_timeout(contract_id)
    return jsonify(result)

@app.route('/api/models/1.4/<contract_id>', methods=['GET'])
def model_1_4(contract_id):
    """模型1.4: 法院判决收入冲销"""
    result = engine.model_1_4_lawsuit_income_cancellation(contract_id)
    return jsonify(result)

@app.route('/api/models/2.1/<contract_id>', methods=['GET'])
def model_2_1(contract_id):
    """模型2.1: 超合同5%三重一大穿透"""
    result = engine.model_2_1_exceed_contract_5_percent(contract_id)
    return jsonify(result)

@app.route('/api/models/2.2/<contract_id>', methods=['GET'])
def model_2_2(contract_id):
    """模型2.2: 包干禁止签证"""
    result = engine.model_2_2_lumpsum_forbidden_visa(contract_id)
    return jsonify(result)

@app.route('/api/models/2.3/<contract_id>', methods=['GET'])
def model_2_3(contract_id):
    """模型2.3: 材料超耗150%扣款"""
    result = engine.model_2_3_material_overconsumption_150(contract_id)
    return jsonify(result)

@app.route('/api/models/2.4/<contract_id>', methods=['GET'])
def model_2_4(contract_id):
    """模型2.4: 未签先进场时序倒置"""
    result = engine.model_2_4_work_before_sign(contract_id)
    return jsonify(result)

@app.route('/api/models/2.5/<contract_id>', methods=['GET'])
def model_2_5(contract_id):
    """模型2.5: 代工未扣与负结算清收"""
    result = engine.model_2_5_agent_work_not_deducted(contract_id)
    return jsonify(result)

@app.route('/api/models/3.1/<contract_id>', methods=['GET'])
def model_3_1(contract_id):
    """模型3.1: 竣工存货未报耗"""
    result = engine.model_3_1_completion_inventory_unreported(contract_id)
    return jsonify(result)

@app.route('/api/models/3.2/<contract_id>', methods=['GET'])
def model_3_2(contract_id):
    """模型3.2: 隐性贴息利息侵蚀"""
    result = engine.model_3_2_hidden_interest_erosion(contract_id)
    return jsonify(result)

@app.route('/api/models/3.3/<contract_id>', methods=['GET'])
def model_3_3(contract_id):
    """模型3.3: 招采付款倒挂"""
    result = engine.model_3_3_procurement_payment_inversion(contract_id)
    return jsonify(result)

# ============================================================================
# API 路由 - 三表穿透比对
# ============================================================================

@app.route('/api/comparison/<contract_id>', methods=['GET'])
def three_table_comparison(contract_id):
    """三表穿透比对"""
    result = engine.three_table_comparison(contract_id)
    return jsonify(result)

# ============================================================================
# API 路由 - 真问题候选包生成
# ============================================================================

@app.route('/api/issues/generate/<contract_id>', methods=['POST'])
def generate_issues(contract_id):
    """第一层：生成真问题候选包"""
    result = engine.generate_true_issue_package(contract_id)
    return jsonify(result)

@app.route('/api/issues/list', methods=['GET'])
def list_issues():
    """获取疑点清单"""
    try:
        org_level = request.args.get('org_level', '')
        risk_level = request.args.get('risk_level', '')

        query = "SELECT * FROM issues WHERE 1=1"
        params = []

        if org_level:
            query += " AND contract_id IN (SELECT contract_id FROM contracts WHERE org_level = ?)"
            params.append(org_level)

        if risk_level:
            query += " AND risk_level = ?"
            params.append(risk_level)

        query += " ORDER BY created_at DESC LIMIT 100"

        results = engine.conn.execute(query, params).fetchall()

        issues = []
        for row in results:
            issues.append({
                "issue_id": row[0],
                "contract_id": row[1],
                "chain_type": row[2],
                "model_code": row[3],
                "risk_level": row[4],
                "time_phase": row[5],
                "issue_type": row[6],
                "title": row[7],
                "description": row[8],
                "amount_impact": float(row[9]) if row[9] else 0,
                "status": row[11],
                "created_at": str(row[14])
            })

        return jsonify({"status": "success", "issues": issues})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# API 路由 - 专家会审
# ============================================================================

@app.route('/api/experts/discuss', methods=['POST'])
def expert_discussion():
    """第二层：专家圆桌讨论"""
    data = request.json
    issue_id = data.get('issue_id')
    user_question = data.get('question', '')

    # 模拟专家讨论结果（实际应调用Dify RAG）
    expert_opinions = {
        "commercial_expert": "从商务角度看，该事项违反了合同包干条款，建议全额审减。",
        "legal_expert": "法律风险中等，建议补充相关证据链，避免后续争议。",
        "financial_expert": "财务影响约50万元，建议启动整改并锁定后续付款。",
        "construction_expert": "现场实际情况可能存在特殊原因，建议现场核实后再做裁决。"
    }

    return jsonify({
        "status": "success",
        "issue_id": issue_id,
        "expert_opinions": expert_opinions,
        "consensus": "建议审减60%，剩余40%待现场核实后确定",
        "timestamp": datetime.now().isoformat()
    })

# ============================================================================
# API 路由 - 主审公文复核
# ============================================================================

@app.route('/api/review/decision', methods=['POST'])
def reviewer_decision():
    """第三层：主审裁决"""
    data = request.json
    issue_id = data.get('issue_id')
    decision = data.get('decision')  # 认定/不认定/需补充证据
    reasoning = data.get('reasoning', '')
    deduction_amount = data.get('deduction_amount', 0)

    try:
        # 更新疑点状态
        engine.conn.execute("""
            UPDATE issues
            SET reviewer_decision = ?,
                status = ?,
                amount_impact = ?
            WHERE issue_id = ?
        """, [
            json.dumps({"decision": decision, "reasoning": reasoning}, ensure_ascii=False),
            '整改中' if decision == '认定' else '已关闭',
            deduction_amount,
            issue_id
        ])

        return jsonify({
            "status": "success",
            "message": "主审裁决已记录",
            "issue_id": issue_id,
            "decision": decision
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# API 路由 - 整改销号
# ============================================================================

@app.route('/api/remediation/close', methods=['POST'])
def close_remediation():
    """第四层：整改销号（强制凭证卡口）"""
    data = request.json
    issue_id = data.get('issue_id')
    proof_number = data.get('proof_number', '')  # 凭证号
    proof_type = data.get('proof_type', '')      # 冲账凭证/退款水单

    if not proof_number:
        return jsonify({
            "status": "error",
            "message": "销号必须提供凭证号，严禁无凭证销号"
        }), 400

    try:
        engine.conn.execute("""
            UPDATE issues
            SET status = '已销号',
                remediation_proof = ?,
                closed_at = CURRENT_TIMESTAMP
            WHERE issue_id = ?
        """, [
            json.dumps({"proof_number": proof_number, "proof_type": proof_type}, ensure_ascii=False),
            issue_id
        ])

        return jsonify({
            "status": "success",
            "message": "整改已销号，财务支付锁已解除",
            "issue_id": issue_id,
            "proof_number": proof_number
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# API 路由 - 知识库问答（RAG）
# ============================================================================

@app.route('/api/knowledge/query', methods=['POST'])
def knowledge_query():
    """统一互动讨论层：制度法规问答"""
    data = request.json
    question = data.get('question', '')
    context_issue_id = data.get('context_issue_id', '')

    # 模拟RAG问答结果（实际应调用Dify RAG）
    answer = f"根据《建设工程施工合同示范文本》第13.2条，{question}的情况下，承包人应在事件发生后14天内提交书面签证申请..."

    related_clauses = [
        {"clause": "GB50500-2013 第3.2.1条", "content": "包干合同不得调整合同价款..."},
        {"clause": "建市[2019]51号文", "content": "竣工结算应在竣工验收后60天内完成..."}
    ]

    return jsonify({
        "status": "success",
        "question": question,
        "answer": answer,
        "related_clauses": related_clauses,
        "timestamp": datetime.now().isoformat()
    })

# ============================================================================
# API 路由 - 统计看板数据
# ============================================================================

@app.route('/api/dashboard/stats', methods=['GET'])
def dashboard_stats():
    """主审驾驶舱统计数据"""
    try:
        # 获取筛选参数
        org_level = request.args.get('org_level', '')

        # 统计疑点数量
        stats = engine.conn.execute("""
            SELECT
                COUNT(*) as total_issues,
                SUM(CASE WHEN risk_level = '高危' THEN 1 ELSE 0 END) as high_risk,
                SUM(CASE WHEN risk_level = '中等' THEN 1 ELSE 0 END) as medium_risk,
                SUM(CASE WHEN status = '待主审复核' THEN 1 ELSE 0 END) as pending_review,
                SUM(CASE WHEN status = '整改中' THEN 1 ELSE 0 END) as in_remediation,
                SUM(CASE WHEN status = '已销号' THEN 1 ELSE 0 END) as closed,
                SUM(amount_impact) as total_amount_impact
            FROM issues
        """).fetchone()

        # 九大定论链分布
        chain_stats = engine.conn.execute("""
            SELECT
                chain_type,
                COUNT(*) as count,
                SUM(amount_impact) as total_impact
            FROM issues
            GROUP BY chain_type
        """).fetchall()

        chains = {}
        for row in chain_stats:
            chains[row[0]] = {"count": row[1], "total_impact": float(row[2]) if row[2] else 0}

        return jsonify({
            "status": "success",
            "total_issues": stats[0] if stats else 0,
            "high_risk_count": stats[1] if stats else 0,
            "medium_risk_count": stats[2] if stats else 0,
            "pending_review": stats[3] if stats else 0,
            "in_remediation": stats[4] if stats else 0,
            "closed": stats[5] if stats else 0,
            "total_amount_impact": float(stats[6]) if stats and stats[6] else 0,
            "chain_distribution": chains
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# API 路由 - 合同列表
# ============================================================================

@app.route('/api/contracts/list', methods=['GET'])
def list_contracts():
    """获取合同列表"""
    try:
        results = engine.conn.execute("""
            SELECT
                contract_id,
                org_level,
                org_name,
                project_name,
                owner_name,
                contract_amount,
                contract_date,
                completion_date,
                contract_type
            FROM contracts
            ORDER BY contract_date DESC
        """).fetchall()

        contracts = []
        for row in results:
            contracts.append({
                "contract_id": row[0],
                "org_level": row[1],
                "org_name": row[2],
                "project_name": row[3],
                "owner_name": row[4],
                "contract_amount": float(row[5]),
                "contract_date": str(row[6]),
                "completion_date": str(row[7]) if row[7] else None,
                "contract_type": row[8]
            })

        return jsonify({"status": "success", "contracts": contracts})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# API 路由 - 报告生成
# ============================================================================

@app.route('/api/report/generate', methods=['POST'])
def generate_report():
    """生成审计报告"""
    data = request.json
    contract_id = data.get('contract_id')
    report_type = data.get('report_type', 'full')  # full/summary/executive

    # 获取该合同的所有疑点
    issues = engine.conn.execute("""
        SELECT * FROM issues WHERE contract_id = ? ORDER BY risk_level DESC, amount_impact DESC
    """, [contract_id]).fetchall()

    # 生成报告摘要
    report = {
        "report_id": str(uuid.uuid4()),
        "contract_id": contract_id,
        "report_type": report_type,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_issues": len(issues),
            "high_risk_count": sum(1 for i in issues if i[4] == '高危'),
            "total_deduction": sum(float(i[9]) for i in issues if i[9])
        },
        "recommendations": [
            "建议立即启动整改程序，锁定后续付款",
            "对高危疑点进行专家会审，确保裁决公正性",
            "完善证据链管理，避免类似问题再次发生"
        ]
    }

    return jsonify({"status": "success", "report": report})

# ============================================================================
# 错误处理
# ============================================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "message": "API endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"status": "error", "message": "Internal server error"}), 500

# ============================================================================
# 启动服务
# ============================================================================

if __name__ == '__main__':
    print("=" * 80)
    print("结算审核智能体 Web服务启动中...")
    print("访问地址: http://localhost:5100")
    print("=" * 80)

    # 初始化示例数据
    engine.import_sample_data()
    print("[✓] 示例数据已加载")

    app.run(host='0.0.0.0', port=5100, debug=True)
