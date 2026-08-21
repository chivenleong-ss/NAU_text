# 工程结算审计智能体

这是独立运行的工程结算审计 Demo，不依赖 `效益审核` 项目，也不使用 5000 端口。

## 启动

在当前目录执行：

```powershell
py -3 app.py
```

默认地址：

```text
http://127.0.0.1:5100/settlement-demo
```

也可以通过环境变量更换端口：

```powershell
$env:SETTLEMENT_PORT = "5101"
py -3 app.py
```

## 目录

- `app.py`：独立 Flask 入口和上传、分析、知识问询、主审决策接口。
- `settlement_audit_engine.py`：确定性文档字段预览、三表比对、异常规则和报告草稿计算。
- `settlement_platform.html`：独立驾驶舱页面。
- `demo_data/settlement_demo.json`：项目、资料、清单、证据、专家、制度和全生命周期 Demo 数据。
- `_jobs/uploads/`：上传资料的本地工作目录。

金额、工程量、单价、偏差和规则疑点由 Python 引擎计算；知识问询机器人只负责制度检索和解释，正式审计结论仍由主审确认。
