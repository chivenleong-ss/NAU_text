# 项目交接说明（无上下文新会话直接看）

## 1. 我们在做什么

构建一个 **「主审驾驶舱视角的工程结算审计多智能体」演示系统**（南京审计大学培训场景）。

核心设计（用户反复确认过的）：**专家不是真人登录，而是配了不同背景规则的智能体**。流程 = 导入资料 → 各专家分析 → 核查 → 确认的问题给主审复核。

终极蓝图已写在 `..\工程全生命周期结算审计多智能体系统（Multi-Agent System）架构设计方案.md`（1704 行，已读完）。当前项目是它的可运行原型。

## 2. 代码在哪

- `结算审核智能体\app.py` — Flask 后端入口，端口 **5100**
- `结算审核智能体\settlement_platform.html` — 单页前端（内嵌 CSS/JS）
- `结算审核智能体\settlement_audit_engine.py` — 确定性计算引擎
- `结算审核智能体\demo_data\settlement_demo.json` — 演示数据（南方产业园二期项目）
- `结算审核智能体\test_settlement_audit_engine.py` — 15 个 unittest，全绿

## 3. 已经完成的

- 三表比对引擎（工程量/单价/金额），疑点检测、反舞弊评分、知识问答
- 多智能体编排（主审驾驶舱 + 规则比对 + 造价/商务/履约专家 + 知识库机器人）
- **双栏穿透核对窗**：左原件右结构化字段，BBOX 高亮，字段置信度 + 人工纠偏，疑点可穿透到原件（3 个疑点已挂：BG-018/ZQ-023/NF-026）
- 知识库小机器人接入**真实 LLM**（本地 Ollama `deepseek-r1:1.5b`，`source=ollama` 已验证）
- 交付导出三件套（json/csv/md）+ 主审复核闭环（decision → remediation）
- 15 个测试全通过

## 4. 当前卡在哪

**无硬阻塞**：服务器能跑、LLM 能回、测试全绿。剩下的全是"功能空白"不是 bug：

- 专家智能体仍是**静态意见**，没接 LLM 自主推理
- 无 PDF/扫描件 OCR 解析（只吃 CSV/Excel/JSON 表格）
- 无真向量库（知识直接塞 prompt）
- 蓝图里的「四大难题」（先干后谈 / 虚盈实亏 / 证据治理 / 分包乱象）一个都没实现

## 5. 下一步计划（按性价比）

1. **把 5 大专家从静态意见升级成真 LLM 专家**（复用已打通的 Ollama 通道，改动最小、效果最明显）—— 推荐先做
2. 四大难题里先做 **「问题一：先干后谈」**（只靠"进场时间 vs 批复时间"两条时间戳，数据成本最低、演示直观）
3. 对齐 Canonical Schema
4. 向量库 / OCR（最重，最后做）

## 6. 踩过的坑（绝对不要再踩）

1. **端口 5100 有僵尸 python 进程跑旧代码**：Flask `debug=False` 不自动重载，改完代码必须 `taskkill //F //PID <pid>` 再重启，否则"改了不生效"。排查：`netstat -ano | grep 5100`
2. **浏览器缓存**：`app.py` 已加 `Cache-Control: no-store`；若再"看不到新按钮"，先 Ctrl+Shift+R 硬刷新
3. **deepseek-r1:1.5b 答案在 `thinking` 字段、`content` 为空**：`_ask_ollama` 必须回退读 `thinking`，否则返回 None 静默降级成关键词匹配，看起来像"没接 LLM"
4. **LLM 慢（33~45s）**：1.5b CPU 推理 + thinking 模型；`think:False` 对该 Ollama 版本无效。要快就 `ollama pull qwen2.5:1.5b`（非 thinking）
5. **Git Bash 的 `/tmp` ≠ Windows temp**：curl 写 `/tmp/x.json` Python 找不到；测试用 Python urllib 直接调，别走文件 round-trip
6. **中文终端乱码**：GBK 编码，打印中文加前缀 `PYTHONIOENCODING=utf-8`
7. **绝不用 LLM 做算量**：蓝图核心结论——算量/规则必须 Python 确定性硬算，LLM 只做语义定性 + 报告 + 问答
