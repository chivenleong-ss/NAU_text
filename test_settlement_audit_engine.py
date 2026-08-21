import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from settlement_audit_engine import (
    analyze,
    answer_question,
    compare_three_tables,
    parse_uploaded_file,
)
from app import app


BASE_DIR = Path(__file__).resolve().parent


class SettlementAuditEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(
            (BASE_DIR / "demo_data" / "settlement_demo.json").read_text(encoding="utf-8")
        )
        cls.result = analyze(cls.data)

    def test_three_table_comparison_keeps_contract_and_market_dimensions(self):
        rows = compare_three_tables(self.data)
        concrete = next(row for row in rows if row["item_code"] == "010501002")
        self.assertEqual(concrete["contract"]["quantity"], 3200)
        self.assertEqual(concrete["settlement"]["quantity"], 3200)
        self.assertAlmostEqual(
            concrete["unit_price_deviation_settlement_vs_contract"],
            0.1187,
            places=4,
        )
        self.assertAlmostEqual(
            concrete["unit_price_deviation_settlement_vs_market"],
            0.0655,
            places=4,
        )

    def test_issue_detection_covers_settlement_and_lifecycle_risks(self):
        ids = {item["id"] for item in self.result["issues"]}
        self.assertTrue(
            {
                "post-quantity-001",
                "post-actual-001",
                "post-price-001",
                "post-claim-CL-001",
                "post-reward-RP-001",
                "post-penalty-RP-002",
                "post-fee-001",
            }.issubset(ids)
        )
        self.assertEqual(self.result["summary"]["issue_count"], 11)
        self.assertEqual(self.result["summary"]["risk_amount"], 4072000)
        self.assertEqual(self.result["summary"]["payment_gap"], 420000)

    def test_cost_aggregation_and_fraud_model_are_deterministic(self):
        cost = self.result["cost_aggregation"]
        self.assertEqual(cost["claims_requested"], 900000)
        self.assertEqual(cost["claims_approved"], 100000)
        self.assertEqual(cost["unapproved_claim_amount"], 800000)
        self.assertEqual(cost["penalty_omission"], 350000)
        self.assertEqual(cost["management_fee_excess"], 1036000)

        fraud = self.result["fraud_assessment"]
        self.assertEqual(fraud["score"], 100)
        self.assertEqual(fraud["level"], "高风险")
        self.assertEqual(len(fraud["hits"]), 5)

    def test_document_type_classification_and_json_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "项目结算书.json"
            path.write_text(
                json.dumps(
                    [{"项目编码": "010101001", "工程量": 10000, "单价": 30}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            preview = parse_uploaded_file(path)
        self.assertEqual(preview["document_type"], "结算书")
        self.assertEqual(preview["fields"]["row_count"], 1)
        self.assertIn("项目编码", preview["fields"]["columns"])

    def test_knowledge_answer_can_be_tied_to_issue(self):
        response = answer_question(
            "签证缺少项目商务经理签认能否计入结算？",
            self.result["knowledge"],
            next(x for x in self.result["issues"] if x["id"] == "post-change-001"),
        )
        self.assertIn("审批链", response["answer"])
        self.assertIn("ZQ-023", response["answer"])
        self.assertGreaterEqual(len(response["matches"]), 1)

    def test_issue_analysis_endpoint_exposes_screening_and_question_slots(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/issue-analysis/post-change-001")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
        self.assertTrue(payload["screening"]["keep_for_chief_review"])
        self.assertEqual(payload["conference"]["mode"], "专家圆桌")
        self.assertIn("变更签证", payload["analysis"]["issue_type"])
        self.assertIn("变更", payload["knowledge_prompt"])
        self.assertGreaterEqual(len(payload["analysis"]["question_slots"]), 4)

    def test_state_exposes_analysis_summary_and_screened_ids(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/state")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
        self.assertIn("analysis_summary", payload)
        self.assertGreaterEqual(payload["analysis_summary"]["issue_count"], 11)
        self.assertGreaterEqual(payload["analysis_summary"]["screened_count"], 1)
        self.assertIn("screened_issue_ids", payload)
        self.assertIn("post-change-001", payload["screened_issue_ids"])

    def test_structured_model_is_exposed_by_api(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/model")
            self.assertEqual(response.status_code, 200)
            model = response.get_json()
        self.assertEqual(model["summary"]["document_count"], 7)
        self.assertEqual(model["summary"]["item_count"], 6)
        self.assertEqual(model["summary"]["issue_count"], 11)
        self.assertEqual(model["summary"]["historical_pattern_count"], 5)
        self.assertEqual(model["summary"]["historical_case_count"], 6)
        self.assertEqual(model["summary"]["audit_model_count"], 10)
        self.assertEqual(model["project"]["name"], "南方产业园二期项目")
        self.assertGreaterEqual(len(model["cost_components"]["claims"]), 2)
        self.assertEqual(
            {(x["phase"], x["model_type"]) for x in model["lifecycle_models"]},
            {("事前", "预警类"), ("事中", "预警类"), ("事后", "问题类")},
        )

    def test_model_catalog_and_training_cover_lifecycle_business_ends(self):
        catalog = self.result["audit_model_catalog"]
        training = self.result["false_settlement_training"]
        self.assertEqual(catalog["summary"]["model_count"], 10)
        self.assertEqual(catalog["summary"]["phase_counts"]["事前"], 2)
        self.assertEqual(catalog["summary"]["phase_counts"]["事中"], 3)
        self.assertEqual(catalog["summary"]["phase_counts"]["事后"], 5)
        self.assertGreaterEqual(catalog["summary"]["business_end_count"], 7)
        self.assertGreaterEqual(catalog["summary"]["active_model_count"], 6)
        self.assertEqual(training["sample_count"], 6)
        self.assertEqual(training["positive_cases"], 4)
        self.assertEqual(training["negative_cases"], 2)
        self.assertEqual(training["training_status"], "已训练")
        self.assertIn("重复计费", training["top_features"])

    def test_model_catalog_endpoint_exposes_training_summary(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/model-catalog")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
        self.assertEqual(payload["audit_model_catalog"]["summary"]["model_count"], 10)
        self.assertEqual(payload["false_settlement_training"]["sample_count"], 6)

    def test_export_endpoint_returns_audit_snapshot(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/export")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/json")
            payload = json.loads(response.data.decode("utf-8"))
        self.assertEqual(payload["summary"]["issue_count"], 11)
        self.assertEqual(payload["fraud_assessment"]["score"], 100)
        self.assertEqual(payload["agent_orchestration"]["summary"]["task_count"], 11)
        self.assertIn("structured_model", payload)
        self.assertIn("report", payload)
        self.assertIn("remediation_tasks", payload)
        self.assertIn("audit_model_catalog", payload)
        self.assertIn("false_settlement_training", payload)

    def test_export_endpoint_returns_csv_issue_list(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/export?format=csv")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/csv")
            text = response.data.decode("utf-8-sig")
        self.assertIn("编号,阶段,类别,风险,事项,金额", text)
        self.assertIn("post-quantity-001", text)
        self.assertIn("土方开挖重复计费", text)

    def test_export_endpoint_returns_markdown_report(self):
        with app.test_client() as client:
            response = client.get("/api/settlement-demo/export?format=md")
            self.assertEqual(response.status_code, 200)
            self.assertIn(response.mimetype, {"text/markdown", "text/plain"})
            text = response.data.decode("utf-8")
        self.assertIn("# 南方产业园二期项目工程结算审计报告", text)
        self.assertIn("## 四、全生命周期模型库", text)
        self.assertIn("## 五、疑点清单", text)
        self.assertIn("训练样本", text)
        self.assertIn("虚假结算", text)

    def test_agent_orchestration_assigns_issue_work(self):
        orchestration = self.result["agent_orchestration"]
        self.assertEqual(orchestration["summary"]["task_count"], 11)
        self.assertEqual(orchestration["summary"]["expert_count"], 6)
        self.assertEqual(orchestration["summary"]["knowledge_bot_count"], 1)
        task = next(x for x in orchestration["tasks"] if x["issue_id"] == "post-quantity-001")
        roles = {agent["role"] for agent in task["agents"]}
        self.assertTrue({"主审驾驶舱", "规则比对智能体", "制度知识库机器人", "造价专家", "商务专家", "履约专家"}.issubset(roles))

    def test_chief_decision_generates_remediation_closeout_task(self):
        with app.test_client() as client:
            client.post("/api/settlement-demo/reset")
            try:
                response = client.post(
                    "/api/settlement-demo/decision",
                    json={"issue_id": "post-quantity-001", "decision": "confirm", "note": "按重复计量核减"},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                tasks = payload["remediation_tasks"]["tasks"]
                self.assertEqual(payload["remediation_tasks"]["summary"]["total"], 1)
                self.assertEqual(payload["remediation_tasks"]["summary"]["open_amount"], 300000)
                self.assertEqual(tasks[0]["issue_id"], "post-quantity-001")
                self.assertEqual(tasks[0]["status"], "待整改")
                self.assertIn("项目商务部", tasks[0]["owner"])

                response = client.post(
                    "/api/settlement-demo/remediation",
                    json={"issue_id": "post-quantity-001", "status": "整改验证", "note": "已提交核减单"},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                task = payload["remediation_tasks"]["tasks"][0]
                self.assertEqual(task["status"], "整改验证")
                self.assertEqual(task["latest_update"]["note"], "已提交核减单")
                self.assertEqual(payload["remediation_tasks"]["summary"]["verified"], 1)
            finally:
                reset = client.post("/api/settlement-demo/reset")
                self.assertEqual(reset.get_json()["remediation_tasks"]["summary"]["total"], 0)

    def test_uploaded_tables_can_drive_analysis(self):
        contract = "项目编码,项目名称,单位,工程量,单价\nX001,测试土方,m3,100,10\n"
        actual = "项目编码,项目名称,单位,工程量,单价\nX001,测试土方,m3,90,10\n"
        settlement = "项目编码,项目名称,单位,工程量,单价\nX001,测试土方,m3,110,13\n"
        with app.test_client() as client:
            try:
                response = client.post(
                    "/api/settlement-demo/upload",
                    data={
                        "files": [
                            (io.BytesIO(contract.encode("utf-8-sig")), "合同清单.csv"),
                            (io.BytesIO(actual.encode("utf-8-sig")), "竣工验收.csv"),
                            (io.BytesIO(settlement.encode("utf-8-sig")), "结算书.csv"),
                        ]
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertTrue(payload["upload_activated"])
                self.assertEqual(payload["active_data_source"], "上传表格资料包")
                self.assertEqual(payload["structured_model"]["summary"]["item_count"], 1)
                ids = {item["id"] for item in payload["issues"]}
                self.assertIn("generic-quantity-X001", ids)
                self.assertIn("generic-price-X001", ids)
            finally:
                client.post("/api/settlement-demo/reset")

    def test_uploaded_zip_package_can_drive_analysis(self):
        contract = "项目编码,项目名称,单位,工程量,单价\nZ001,测试钢筋,t,100,3000\n"
        actual = "项目编码,项目名称,单位,工程量,单价\nZ001,测试钢筋,t,80,3000\n"
        settlement = "项目编码,项目名称,单位,工程量,单价\nZ001,测试钢筋,t,120,3600\n"
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("合同清单.csv", contract.encode("utf-8-sig"))
            archive.writestr("竣工验收.csv", actual.encode("utf-8-sig"))
            archive.writestr("结算书.csv", settlement.encode("utf-8-sig"))
        package.seek(0)

        with app.test_client() as client:
            try:
                response = client.post(
                    "/api/settlement-demo/upload",
                    data={"files": [(package, "结算资料包.zip")]},
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertTrue(payload["upload_activated"])
                self.assertEqual(payload["active_data_source"], "上传表格资料包")
                self.assertEqual(payload["uploaded_now"][0]["fields"]["file_count"], 3)
                self.assertEqual(payload["structured_model"]["summary"]["item_count"], 1)
                ids = {item["id"] for item in payload["issues"]}
                self.assertIn("generic-quantity-Z001", ids)
                self.assertIn("generic-price-Z001", ids)
            finally:
                client.post("/api/settlement-demo/reset")


if __name__ == "__main__":
    unittest.main()
