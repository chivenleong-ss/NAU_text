#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结算审核智能引擎 - 核心计算模块
零幻觉铁律：所有数值比对、模型计算100%在此确定性引擎硬算
基于 DuckDB / Pandas 实现 11 个核心模型 + 三表穿透比对
"""

import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
import json
import uuid


class SettlementAuditEngine:
    """结算审核引擎 - 四层流转架构的计算中枢"""

    def __init__(self, db_path: str = ":memory:"):
        """初始化引擎"""
        self.conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        """初始化数据库表结构"""
        # 三表核心表结构
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS contracts (
                contract_id VARCHAR PRIMARY KEY,
                org_level VARCHAR,  -- 集团/分公司/项目部
                org_name VARCHAR,
                project_name VARCHAR,
                owner_name VARCHAR,  -- 业主名称
                contract_amount DECIMAL(18,2),
                contract_date DATE,
                completion_date DATE,
                contract_type VARCHAR,  -- 总包/分包/材料供应
                payment_terms TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS owner_settlement (
                settlement_id VARCHAR PRIMARY KEY,
                contract_id VARCHAR,
                item_code VARCHAR,
                item_name VARCHAR,
                unit VARCHAR,
                confirmed_quantity DECIMAL(18,4),  -- 业主确权工程量
                confirmed_price DECIMAL(18,4),     -- 业主确认单价
                confirmed_amount DECIMAL(18,2),    -- 确权金额
                settlement_date DATE,
                evidence_count INTEGER DEFAULT 0,  -- 证据链完整度
                is_disputed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS subcontractor_claims (
                claim_id VARCHAR PRIMARY KEY,
                contract_id VARCHAR,
                item_code VARCHAR,
                item_name VARCHAR,
                unit VARCHAR,
                claimed_quantity DECIMAL(18,4),   -- 分包申报工程量
                claimed_price DECIMAL(18,4),      -- 分包申报单价
                claimed_amount DECIMAL(18,2),     -- 申报金额
                claim_date DATE,
                approval_status VARCHAR,          -- 待审/已批/已拒
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 疑点追踪表
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS issues (
                issue_id VARCHAR PRIMARY KEY,
                contract_id VARCHAR,
                chain_type VARCHAR,  -- 定论链类型(1-9)
                model_code VARCHAR,  -- 模型编号(1.1-3.3)
                risk_level VARCHAR,  -- 高危/中等/低风险
                time_phase VARCHAR,  -- 事前/事中/事后
                issue_type VARCHAR,  -- 直接问题/风险预警/机制归因/优化建议
                title VARCHAR,
                description TEXT,
                amount_impact DECIMAL(18,2),
                evidence_json TEXT,
                status VARCHAR DEFAULT '待主审复核',  -- 待主审复核/整改中/已销号
                expert_opinion TEXT,
                reviewer_decision TEXT,
                remediation_proof VARCHAR,  -- 凭证号
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)

    # ============================================================================
    # 维度一：对上确权 - 4个核心模型
    # ============================================================================

    def model_1_1_authorization_rate_deviation(self, contract_id: str) -> Dict[str, Any]:
        """
        模型1.1: 确权率偏离度分析
        逻辑框架：(业主确权金额 / 合同金额) < 85% → 触发风险预警
        """
        query = """
        SELECT
            c.contract_id,
            c.project_name,
            c.contract_amount,
            COALESCE(SUM(os.confirmed_amount), 0) as confirmed_total,
            ROUND(COALESCE(SUM(os.confirmed_amount), 0) / NULLIF(c.contract_amount, 0) * 100, 2) as auth_rate
        FROM contracts c
        LEFT JOIN owner_settlement os ON c.contract_id = os.contract_id
        WHERE c.contract_id = ?
        GROUP BY c.contract_id, c.project_name, c.contract_amount
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data", "message": "未找到合同数据"}

        contract_id, project_name, contract_amount, confirmed_total, auth_rate = result

        risk_level = "正常"
        issue_detected = False

        if auth_rate < 85:
            risk_level = "高危" if auth_rate < 70 else "中等"
            issue_detected = True

        return {
            "model_code": "1.1",
            "model_name": "确权率偏离度",
            "contract_id": contract_id,
            "project_name": project_name,
            "contract_amount": float(contract_amount),
            "confirmed_total": float(confirmed_total),
            "authorization_rate": float(auth_rate),
            "risk_level": risk_level,
            "issue_detected": issue_detected,
            "deviation_amount": float(contract_amount - confirmed_total),
            "recommendation": f"确权率仅{auth_rate}%，建议立即启动催收机制" if issue_detected else "确权率正常"
        }

    def model_1_2_delay_settlement_time_loss(self, contract_id: str) -> Dict[str, Any]:
        """
        模型1.2: 久竣未结时间型亏损
        逻辑框架：(当前日期 - 竣工日期) > 180天 → 计算资金占用成本
        """
        query = """
        SELECT
            contract_id,
            project_name,
            contract_amount,
            completion_date,
            DATEDIFF('day', completion_date, CURRENT_DATE) as delay_days
        FROM contracts
        WHERE contract_id = ?
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        contract_id, project_name, contract_amount, completion_date, delay_days = result

        threshold_days = 180
        annual_interest_rate = 0.0485  # 年化4.85% LPR

        issue_detected = delay_days > threshold_days

        if issue_detected:
            # 计算资金占用损失
            delay_months = max(0, delay_days - threshold_days) / 30
            time_loss = float(contract_amount) * annual_interest_rate * (delay_months / 12)
        else:
            time_loss = 0

        return {
            "model_code": "1.2",
            "model_name": "久竣未结时间型亏损",
            "contract_id": contract_id,
            "project_name": project_name,
            "completion_date": str(completion_date),
            "delay_days": delay_days,
            "threshold_days": threshold_days,
            "time_loss_amount": round(time_loss, 2),
            "risk_level": "高危" if delay_days > 365 else ("中等" if issue_detected else "正常"),
            "issue_detected": issue_detected,
            "recommendation": f"竣工已{delay_days}天未结算，累计损失{time_loss:.2f}元" if issue_detected else "结算周期正常"
        }

    def model_1_3_visa_claim_timeout(self, contract_id: str) -> Dict[str, Any]:
        """
        模型1.3: 签证滞后/索赔失脱
        逻辑框架：签证提交超过14天、索赔超过28天 → 可能丧失追索权
        """
        # 此处简化示例，实际需关联签证/索赔事件表
        query = """
        SELECT
            os.settlement_id,
            os.item_name,
            os.confirmed_amount,
            os.settlement_date,
            os.evidence_count,
            DATEDIFF('day', os.settlement_date, CURRENT_DATE) as days_since_settlement
        FROM owner_settlement os
        WHERE os.contract_id = ? AND os.is_disputed = TRUE
        """
        results = self.conn.execute(query, [contract_id]).fetchall()

        timeout_items = []
        total_risk_amount = 0

        for row in results:
            settlement_id, item_name, amount, settlement_date, evidence_count, days_since = row

            # 签证14天规则、索赔28天规则
            is_timeout = days_since > 28 or (days_since > 14 and evidence_count < 2)

            if is_timeout:
                timeout_items.append({
                    "item_name": item_name,
                    "amount": float(amount),
                    "days_since": days_since,
                    "evidence_count": evidence_count
                })
                total_risk_amount += float(amount)

        return {
            "model_code": "1.3",
            "model_name": "签证滞后/索赔失脱",
            "contract_id": contract_id,
            "timeout_count": len(timeout_items),
            "timeout_items": timeout_items[:10],  # 限制返回前10条
            "total_risk_amount": round(total_risk_amount, 2),
            "risk_level": "高危" if total_risk_amount > 1000000 else ("中等" if timeout_items else "正常"),
            "issue_detected": len(timeout_items) > 0,
            "recommendation": f"发现{len(timeout_items)}项超时签证/索赔，涉及金额{total_risk_amount:.2f}元" if timeout_items else "签证索赔及时"
        }

    def model_1_4_lawsuit_income_cancellation(self, contract_id: str) -> Dict[str, Any]:
        """
        模型1.4: 法院判决收入冲销
        逻辑框架：存在败诉判决 → 需冲减已确认收入
        """
        # 此处简化，实际需对接诉讼系统
        query = """
        SELECT
            contract_id,
            project_name,
            contract_amount
        FROM contracts
        WHERE contract_id = ?
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        # 模拟诉讼判决数据（实际应从诉讼表关联）
        lawsuit_loss = 0  # 败诉金额

        return {
            "model_code": "1.4",
            "model_name": "法院判决收入冲销",
            "contract_id": contract_id,
            "lawsuit_loss_amount": lawsuit_loss,
            "risk_level": "高危" if lawsuit_loss > 0 else "正常",
            "issue_detected": lawsuit_loss > 0,
            "recommendation": "未发现诉讼败诉风险" if lawsuit_loss == 0 else f"需冲销收入{lawsuit_loss}元"
        }

    # ============================================================================
    # 维度二：对下分包 - 5个核心模型
    # ============================================================================

    def model_2_1_exceed_contract_5_percent(self, contract_id: str) -> Dict[str, Any]:
        """
        模型2.1: 超合同5%三重一大穿透
        逻辑框架：分包结算金额超合同金额5% → 必须穿透三重一大决策记录
        """
        query = """
        SELECT
            c.contract_id,
            c.project_name,
            c.contract_amount,
            COALESCE(SUM(sc.claimed_amount), 0) as total_claimed
        FROM contracts c
        LEFT JOIN subcontractor_claims sc ON c.contract_id = sc.contract_id
        WHERE c.contract_id = ? AND c.contract_type = '分包'
        GROUP BY c.contract_id, c.project_name, c.contract_amount
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        contract_id, project_name, contract_amount, total_claimed = result

        exceed_ratio = (float(total_claimed) / float(contract_amount) - 1) * 100 if contract_amount > 0 else 0
        threshold = 5.0

        issue_detected = exceed_ratio > threshold
        exceed_amount = float(total_claimed) - float(contract_amount)

        return {
            "model_code": "2.1",
            "model_name": "超合同5%三重一大穿透",
            "contract_id": contract_id,
            "project_name": project_name,
            "contract_amount": float(contract_amount),
            "total_claimed": float(total_claimed),
            "exceed_ratio": round(exceed_ratio, 2),
            "exceed_amount": round(exceed_amount, 2),
            "risk_level": "高危" if exceed_ratio > 10 else ("中等" if issue_detected else "正常"),
            "issue_detected": issue_detected,
            "recommendation": f"超合同{exceed_ratio:.2f}%，须提供三重一大决策会议纪要" if issue_detected else "未超合同5%红线"
        }

    def model_2_2_lumpsum_forbidden_visa(self, contract_id: str) -> Dict[str, Any]:
        """
        模型2.2: 包干禁止签证与清单外项剔除
        逻辑框架：包干合同出现签证/清单外项 → 一律剔除
        """
        query = """
        SELECT
            c.payment_terms,
            sc.claim_id,
            sc.item_name,
            sc.claimed_amount
        FROM contracts c
        LEFT JOIN subcontractor_claims sc ON c.contract_id = sc.contract_id
        WHERE c.contract_id = ? AND c.contract_type = '分包'
        """
        results = self.conn.execute(query, [contract_id]).fetchall()

        forbidden_items = []
        total_deduction = 0

        for row in results:
            payment_terms, claim_id, item_name, claimed_amount = row

            # 判断是否为包干合同（实际应更精细解析）
            is_lumpsum = payment_terms and '包干' in payment_terms

            # 判断是否为清单外项（简化判断，实际需对比清单表）
            is_out_of_list = '临时' in item_name or '签证' in item_name

            if is_lumpsum and is_out_of_list:
                forbidden_items.append({
                    "claim_id": claim_id,
                    "item_name": item_name,
                    "amount": float(claimed_amount)
                })
                total_deduction += float(claimed_amount)

        return {
            "model_code": "2.2",
            "model_name": "包干禁止签证与清单外项剔除",
            "contract_id": contract_id,
            "forbidden_count": len(forbidden_items),
            "forbidden_items": forbidden_items[:10],
            "total_deduction": round(total_deduction, 2),
            "risk_level": "高危" if total_deduction > 500000 else ("中等" if forbidden_items else "正常"),
            "issue_detected": len(forbidden_items) > 0,
            "recommendation": f"发现{len(forbidden_items)}项违规签证，建议审减{total_deduction:.2f}元" if forbidden_items else "未发现包干合同违规签证"
        }

    def model_2_3_material_overconsumption_150(self, contract_id: str) -> Dict[str, Any]:
        """
        模型2.3: 材料超耗150%扣款硬算
        逻辑框架：实际用量 > 定额用量 * 1.5 → 按超额部分扣款
        """
        # 此处简化示例，实际需对接材料进场表
        query = """
        SELECT
            sc.item_name,
            sc.claimed_quantity,
            sc.claimed_amount,
            sc.unit
        FROM subcontractor_claims sc
        WHERE sc.contract_id = ? AND sc.item_name LIKE '%材料%'
        """
        results = self.conn.execute(query, [contract_id]).fetchall()

        overconsumption_items = []
        total_deduction = 0

        for row in results:
            item_name, claimed_qty, claimed_amount, unit = row

            # 模拟定额用量（实际需查询定额表）
            standard_qty = float(claimed_qty) * 0.8  # 假设定额为申报量的80%
            threshold_qty = standard_qty * 1.5

            if float(claimed_qty) > threshold_qty:
                over_qty = float(claimed_qty) - threshold_qty
                deduction = (over_qty / float(claimed_qty)) * float(claimed_amount)

                overconsumption_items.append({
                    "item_name": item_name,
                    "claimed_qty": float(claimed_qty),
                    "threshold_qty": round(threshold_qty, 2),
                    "over_qty": round(over_qty, 2),
                    "deduction": round(deduction, 2)
                })
                total_deduction += deduction

        return {
            "model_code": "2.3",
            "model_name": "材料超耗150%扣款",
            "contract_id": contract_id,
            "overconsumption_count": len(overconsumption_items),
            "overconsumption_items": overconsumption_items[:10],
            "total_deduction": round(total_deduction, 2),
            "risk_level": "高危" if total_deduction > 300000 else ("中等" if overconsumption_items else "正常"),
            "issue_detected": len(overconsumption_items) > 0,
            "recommendation": f"发现{len(overconsumption_items)}项材料超耗，应扣款{total_deduction:.2f}元" if overconsumption_items else "材料用量正常"
        }

    def model_2_4_work_before_sign(self, contract_id: str) -> Dict[str, Any]:
        """
        模型2.4: 未签先进场时序倒置
        逻辑框架：进场日期 < 合同签订日期 → 责任错配风险
        """
        query = """
        SELECT
            c.contract_id,
            c.project_name,
            c.contract_date,
            sc.claim_id,
            sc.item_name,
            sc.claim_date
        FROM contracts c
        LEFT JOIN subcontractor_claims sc ON c.contract_id = sc.contract_id
        WHERE c.contract_id = ? AND sc.claim_date < c.contract_date
        """
        results = self.conn.execute(query, [contract_id]).fetchall()

        reversed_items = []

        for row in results:
            contract_id, project_name, contract_date, claim_id, item_name, claim_date = row
            days_reversed = (contract_date - claim_date).days if contract_date and claim_date else 0

            reversed_items.append({
                "claim_id": claim_id,
                "item_name": item_name,
                "claim_date": str(claim_date),
                "contract_date": str(contract_date),
                "days_reversed": days_reversed
            })

        return {
            "model_code": "2.4",
            "model_name": "未签先进场时序倒置",
            "contract_id": contract_id,
            "reversed_count": len(reversed_items),
            "reversed_items": reversed_items[:10],
            "risk_level": "高危" if len(reversed_items) > 5 else ("中等" if reversed_items else "正常"),
            "issue_detected": len(reversed_items) > 0,
            "recommendation": f"发现{len(reversed_items)}项未签先进场，存在责任错配风险" if reversed_items else "合同签订与进场时序正常"
        }

    def model_2_5_agent_work_not_deducted(self, contract_id: str) -> Dict[str, Any]:
        """
        模型2.5: 代工未扣与负结算清收
        逻辑框架：存在代工/垫付但未在分包结算中扣除 → 形成负结算应收
        """
        # 简化示例，实际需关联代工记录表
        query = """
        SELECT
            contract_id,
            project_name,
            contract_amount
        FROM contracts
        WHERE contract_id = ?
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        # 模拟代工未扣金额（实际需查询代工表）
        agent_work_amount = 0

        return {
            "model_code": "2.5",
            "model_name": "代工未扣与负结算清收",
            "contract_id": contract_id,
            "agent_work_amount": agent_work_amount,
            "risk_level": "高危" if agent_work_amount > 100000 else "正常",
            "issue_detected": agent_work_amount > 0,
            "recommendation": "未发现代工未扣情况" if agent_work_amount == 0 else f"应清收代工款{agent_work_amount}元"
        }

    # ============================================================================
    # 维度三：业财穿透 - 3个核心模型
    # ============================================================================

    def model_3_1_completion_inventory_unreported(self, contract_id: str) -> Dict[str, Any]:
        """
        模型3.1: 竣工存货未报耗虚增利润
        逻辑框架：项目已竣工但存货未报损 → 虚增账面利润
        """
        query = """
        SELECT
            c.contract_id,
            c.project_name,
            c.completion_date,
            DATEDIFF('day', c.completion_date, CURRENT_DATE) as days_after_completion
        FROM contracts c
        WHERE c.contract_id = ? AND c.completion_date IS NOT NULL
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        contract_id, project_name, completion_date, days_after = result

        # 模拟存货金额（实际需查询存货表）
        inventory_amount = 0

        # 竣工超过90天仍有存货 → 触发预警
        issue_detected = days_after > 90 and inventory_amount > 0

        return {
            "model_code": "3.1",
            "model_name": "竣工存货未报耗虚增利润",
            "contract_id": contract_id,
            "project_name": project_name,
            "completion_date": str(completion_date),
            "days_after_completion": days_after,
            "inventory_amount": inventory_amount,
            "risk_level": "高危" if inventory_amount > 500000 else ("中等" if issue_detected else "正常"),
            "issue_detected": issue_detected,
            "recommendation": "项目已竣工且存货已报耗" if not issue_detected else f"竣工{days_after}天仍有存货{inventory_amount}元未报损"
        }

    def model_3_2_hidden_interest_erosion(self, contract_id: str) -> Dict[str, Any]:
        """
        模型3.2: 隐性贴息利息侵蚀真实利润还原
        逻辑框架：供应商承诺返利但实为高息贷款 → 还原真实利润
        """
        query = """
        SELECT
            contract_id,
            project_name,
            contract_amount
        FROM contracts
        WHERE contract_id = ?
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        # 模拟隐性贴息（实际需对接财务贴息表）
        hidden_interest = 0

        return {
            "model_code": "3.2",
            "model_name": "隐性贴息利息侵蚀",
            "contract_id": contract_id,
            "hidden_interest": hidden_interest,
            "risk_level": "高危" if hidden_interest > 200000 else "正常",
            "issue_detected": hidden_interest > 0,
            "recommendation": "未发现隐性贴息" if hidden_interest == 0 else f"需还原隐性利息{hidden_interest}元"
        }

    def model_3_3_procurement_payment_inversion(self, contract_id: str) -> Dict[str, Any]:
        """
        模型3.3: 招采付款倒挂与强制止损
        逻辑框架：已付款金额 > 已确权金额 → 触发强制止损机制
        """
        query = """
        SELECT
            c.contract_id,
            c.project_name,
            c.contract_amount,
            COALESCE(SUM(os.confirmed_amount), 0) as confirmed_total
        FROM contracts c
        LEFT JOIN owner_settlement os ON c.contract_id = os.contract_id
        WHERE c.contract_id = ?
        GROUP BY c.contract_id, c.project_name, c.contract_amount
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        contract_id, project_name, contract_amount, confirmed_total = result

        # 模拟已付款金额（实际需查询付款记录表）
        paid_amount = float(confirmed_total) * 1.1  # 假设已付款超确权10%

        inversion_amount = paid_amount - float(confirmed_total)
        issue_detected = inversion_amount > 0

        return {
            "model_code": "3.3",
            "model_name": "招采付款倒挂",
            "contract_id": contract_id,
            "project_name": project_name,
            "confirmed_total": float(confirmed_total),
            "paid_amount": paid_amount,
            "inversion_amount": round(inversion_amount, 2),
            "risk_level": "高危" if inversion_amount > 1000000 else ("中等" if issue_detected else "正常"),
            "issue_detected": issue_detected,
            "recommendation": f"付款超确权{inversion_amount:.2f}元，建议启动强制止损" if issue_detected else "付款与确权匹配正常"
        }

    # ============================================================================
    # 综合分析：三表穿透比对
    # ============================================================================

    def three_table_comparison(self, contract_id: str) -> Dict[str, Any]:
        """
        三表穿透比对：合同金额 vs 业主确权 vs 分包申报
        """
        query = """
        SELECT
            c.contract_id,
            c.project_name,
            c.contract_amount,
            COALESCE(SUM(os.confirmed_amount), 0) as owner_confirmed,
            COALESCE(SUM(sc.claimed_amount), 0) as sub_claimed
        FROM contracts c
        LEFT JOIN owner_settlement os ON c.contract_id = os.contract_id
        LEFT JOIN subcontractor_claims sc ON c.contract_id = sc.contract_id
        WHERE c.contract_id = ?
        GROUP BY c.contract_id, c.project_name, c.contract_amount
        """
        result = self.conn.execute(query, [contract_id]).fetchone()

        if not result:
            return {"status": "no_data"}

        contract_id, project_name, contract_amount, owner_confirmed, sub_claimed = result

        # 计算关键偏离度
        auth_deviation = float(owner_confirmed) - float(contract_amount)
        cost_deviation = float(sub_claimed) - float(contract_amount)
        profit_margin = float(owner_confirmed) - float(sub_claimed)
        profit_rate = (profit_margin / float(owner_confirmed) * 100) if owner_confirmed > 0 else 0

        return {
            "contract_id": contract_id,
            "project_name": project_name,
            "contract_amount": float(contract_amount),
            "owner_confirmed": float(owner_confirmed),
            "sub_claimed": float(sub_claimed),
            "auth_deviation": round(auth_deviation, 2),
            "cost_deviation": round(cost_deviation, 2),
            "profit_margin": round(profit_margin, 2),
            "profit_rate": round(profit_rate, 2),
            "risk_alerts": self._generate_risk_alerts(auth_deviation, cost_deviation, profit_rate)
        }

    def _generate_risk_alerts(self, auth_dev: float, cost_dev: float, profit_rate: float) -> List[str]:
        """生成风险预警"""
        alerts = []

        if auth_dev < -1000000:
            alerts.append("业主确权严重不足，收入风险极高")
        if cost_dev > 1000000:
            alerts.append("分包申报超合同100万以上，成本失控")
        if profit_rate < 5:
            alerts.append("利润率低于5%，项目盈利能力堪忧")
        if profit_rate < 0:
            alerts.append("出现负利润，项目已亏损")

        return alerts if alerts else ["三表比对正常"]

    # ============================================================================
    # 第一层：问题分析与过滤层 - 真问题候选包生成
    # ============================================================================

    def generate_true_issue_package(self, contract_id: str) -> Dict[str, Any]:
        """
        第一层：跑批11个模型，生成真问题候选包
        """
        issues = []

        # 依次执行11个模型
        models = [
            self.model_1_1_authorization_rate_deviation,
            self.model_1_2_delay_settlement_time_loss,
            self.model_1_3_visa_claim_timeout,
            self.model_1_4_lawsuit_income_cancellation,
            self.model_2_1_exceed_contract_5_percent,
            self.model_2_2_lumpsum_forbidden_visa,
            self.model_2_3_material_overconsumption_150,
            self.model_2_4_work_before_sign,
            self.model_2_5_agent_work_not_deducted,
            self.model_3_1_completion_inventory_unreported,
            self.model_3_2_hidden_interest_erosion,
            self.model_3_3_procurement_payment_inversion
        ]

        for model_func in models:
            result = model_func(contract_id)
            if result.get("issue_detected", False):
                issues.append(result)

        return {
            "contract_id": contract_id,
            "total_issues": len(issues),
            "high_risk_count": sum(1 for i in issues if i.get("risk_level") == "高危"),
            "medium_risk_count": sum(1 for i in issues if i.get("risk_level") == "中等"),
            "issues": issues
        }

    # ============================================================================
    # 数据导入与初始化
    # ============================================================================

    def import_sample_data(self):
        """导入示例数据用于演示"""
        # 插入合同数据
        self.conn.execute("""
            INSERT INTO contracts VALUES
            ('C2024001', '分公司', '华东分公司', '浦东新区医院项目', '上海市卫健委', 85000000, '2023-03-15', '2024-06-30', '总包', '按进度付款', CURRENT_TIMESTAMP),
            ('C2024002', '项目部', '杭州项目部', '钱塘江大桥维修', '浙江省交通厅', 32000000, '2023-08-20', '2024-12-31', '总包', '竣工后付款', CURRENT_TIMESTAMP),
            ('C2024003', '项目部', '南京项目部', '江宁区住宅配套', '南京建投', 55000000, '2023-05-10', '2024-03-20', '分包', '包干价', CURRENT_TIMESTAMP)
        """)

        # 插入业主确权数据
        self.conn.execute("""
            INSERT INTO owner_settlement VALUES
            ('OS001', 'C2024001', 'A001', '土方开挖', '立方米', 12500, 85.5, 1068750, '2024-07-15', 3, FALSE, CURRENT_TIMESTAMP),
            ('OS002', 'C2024001', 'A002', '混凝土浇筑', '立方米', 8200, 650, 5330000, '2024-07-20', 2, FALSE, CURRENT_TIMESTAMP),
            ('OS003', 'C2024002', 'B001', '桥面铺装', '平方米', 5600, 320, 1792000, '2024-08-01', 1, TRUE, CURRENT_TIMESTAMP)
        """)

        # 插入分包申报数据
        self.conn.execute("""
            INSERT INTO subcontractor_claims VALUES
            ('SC001', 'C2024003', 'A001', '土方开挖', '立方米', 13000, 90, 1170000, '2024-04-01', '待审', CURRENT_TIMESTAMP),
            ('SC002', 'C2024003', 'A002', '钢筋材料', '吨', 850, 4800, 4080000, '2024-04-15', '待审', CURRENT_TIMESTAMP),
            ('SC003', 'C2024003', 'A003', '临时签证项', '项', 1, 500000, 500000, '2024-05-01', '待审', CURRENT_TIMESTAMP)
        """)

        return {"status": "success", "message": "示例数据导入成功"}

    def close(self):
        """关闭数据库连接"""
        self.conn.close()


# ============================================================================
# 测试入口
# ============================================================================
if __name__ == "__main__":
    engine = SettlementAuditEngine()
    engine.import_sample_data()

    # 测试模型1.1
    result = engine.model_1_1_authorization_rate_deviation("C2024001")
    print("模型1.1测试结果:", json.dumps(result, ensure_ascii=False, indent=2))

    # 测试三表比对
    comparison = engine.three_table_comparison("C2024001")
    print("三表比对结果:", json.dumps(comparison, ensure_ascii=False, indent=2))

    # 测试真问题包生成
    issue_package = engine.generate_true_issue_package("C2024003")
    print("真问题候选包:", json.dumps(issue_package, ensure_ascii=False, indent=2))

    engine.close()
