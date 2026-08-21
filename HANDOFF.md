# 项目交接说明（无上下文新会话直接看）
## 0. 最新任务交接（新会话必读；下方 §1~§6 及各历史追加节为早期记录，过时信息以本节为准）

### 0.1 我们在做什么

本仓库是「主审驾驶舱视角的工程结算审计多智能体」可运行原型（南京审计大学培训场景，Flask 应用，端口 5100）。

**最近一次任务（刚完成）**：按《终极蓝图 V3.2.md》把《全面数字化商务与工程结算审计系统端到端落地实施方案.md》第三章旧的「三条关联提示链（C1/C2/C3）」整体升维为「九大管理归因与战略审计定论链（G1~G9）」，并同步改造引擎、测试、API，保证 方案-代码-测试 三者一致。

### 0.2 九大链是什么

11 个微观计算模型升维聚合为 9 条高管视角管理归因链，分两大阵列：

- **商务结算合规（4 链）**：G1 先干后谈与未定价变更责任错配 / G2 结算滞后与虚盈实亏时间亏损 / G3 证据治理与六大证据链断点阻断 / G4 层层转包与影子分包价格失控
- **公司治理与战略风控（5 链）**：G5 盲目垫资与履约惯性击穿强制止损红线 / G6“三重一大”与分级授权制度性失守 / G7 诉讼判决瞒报与财务收入虚增失真 / G8 供应链贴息反噬与全口径真实效益侵蚀 / G9“时空穿越”人证分离与现场安全实质悬空

每条链 = 演进路径 + 业务机理与深层痛点 + 智能体三段式公文定论（定性判断 / 量化支撑 / 管理定论）。11 模型 → 九链的聚合映射表在实施方案第三章 §3.2 末尾。

### 0.3 已经完成（本任务）

- **文档**：`模型建设/全面数字化商务与工程结算审计系统端到端落地实施方案.md` 第三章 §3.2 整体替换为九大链正文（ASCII 全景图 + G1~G9 逐条展开 + 聚合映射表）；全文 7 处旧引用同步改掉（第二屏看板标题/关联链可视化、穿透台小节标题、三条关联链动态点亮说明、第八步流水线第 六 步、方案实施总结等）；`模型建设/方案审核与智能体应用可行性分析报告.md` 第 183 行同步。
- **引擎**：`settlement_audit_engine.py` 的 `build_cross_model_hints()` 由 C1/C2/C3 升维为 G1~G9（返回新增 `overview` / `sections` 两大阵列 / `matched_chains` / `unmatched_chains` / `pipeline_status`）；`build_model_chain()` 第 6 步改为「九大管理归因与战略定论链串联」；`build_nine_grid()` docstring 修正为方案 §3.5。
- **测试**：`test_settlement_audit_engine.py` 新增 2 条单测（九链结构 + step6 引用），**19 个测试全绿**（早期交接写的“15 个”已过时）。
- **API 实测**：`/api/settlement-demo/cross-model-hints`、`/model-chain`、`/nine-grid`、`/model-catalog` 均 200。demo 数据下命中 G1~G6、G8、G9；**G7 未命中是预期行为**（G7 只聚合 M1.4 诉讼判决模型，demo 无生效判决），不是 bug，不要修。

### 0.4 当前卡在哪

无硬阻塞。唯一遗留待办（上一会话已确认、本任务未做）：**D.3 前端渲染**——`settlement_platform.html` 还没有渲染三大新能力（模型链 / 九宫格 / 跨模型证据链）。本任务升维后，跨模型证据链必须按九大链两大阵列渲染（G1~G9 + 演进路径 `path` + 三段式 `conclusion`），**不许再出现 C1/C2/C3 文案**。

### 0.5 下一步计划

1. 做 D.3：`settlement_platform.html` 新增三个区块
   - 模型链 → 结构化建模区（`/api/settlement-demo/model-chain`）
   - 九宫格 → 主审驾驶舱/总览区（`/api/settlement-demo/nine-grid`）
   - 跨模型证据链 → 报告区（`/api/settlement-demo/cross-model-hints`，按两大阵列 + 九链渲染）
2. 回归验证：`python -X utf8 -m unittest test_settlement_audit_engine -v`（必须 19 个全绿）；浏览器硬刷新看新前端。
3. 其余按用户新对话要求执行。

### 0.6 运行与验证方式

- 工作目录：`e:\南审培训\结算审核智能体\`
- 后端启动：`python -X utf8 app.py`（端口 5100）
- 单元测试：`python -X utf8 -m unittest test_settlement_audit_engine -v`
- 引擎自检：`python -X utf8 -c "import json; from settlement_audit_engine import analyze; r=analyze(json.load(open('demo_data/settlement_demo.json', encoding='utf-8'))); h=r['cross_model_hints']; print(h['total_chains'], h['matched_chains'], h['unmatched_chains'])"`
- 前端访问：浏览器 `http://localhost:5100/`（改动后 Ctrl+Shift+R 硬刷新）

### 0.7 踩过的坑（绝对不要再踩）

1. **行首缩进修复绝不要用 editor 的 old_text/new_text 替换**：上次会话连续 4 次把 old/new 生成成同文（前导空格系统性丢失），全部 no-op。改行首缩进一律用短 Python 脚本按 `line.startswith(...)` 确定性补空格，用完即删。
2. **大段中文 + ASCII 图替换不要用 editor 大块编辑**：用 `read_files` 拿精确行号边界 + Python 按行号切片拼接最稳；注意 `Path.read_text()` 不支持 `newline` 参数，要用 `open(path, newline='')` 保行尾。
3. **editor 的 old_text 别带 `\n` 或依赖行尾**：HANDOFF.md 追加时曾因行尾差异匹配失败；往文件末尾追加内容用 `insert_line=<总行数+1>` 最稳。
4. **PowerShell 终端输出会被 shell integration 截断且常报 exit code 1**：命令其实执行了，别因 code 1 误判失败；以 Python 的 print / 返回值 / unittest 结果为准。
5. **HANDOFF.md 早期章节信息已过时**：§1~§6 及各历史追加节里的“15 个测试”“四大难题没实现”“C1/C2/C3”均已过期；新会话以 §0 为准。
6. **前端看不到新东西先硬刷新**（Ctrl+Shift+R），别急着怀疑后端没生效；端口 5100 有僵尸 python 进程时先 `taskkill //F //PID <pid>` 再重启。
7. **G7 未命中不是 bug**（见 0.3），别“顺手修掉”。

## 1. 我们在做什么

构建一个 **「主审驾驶舱视角的工程结算审计多智能体」演示系统**（南京审计大学培训场景）。

核心设计（用户反复确认过的）：**专家不是真人登录，而是配了不同背景规则的智能体**。流程 = 导入资料 → 各专家分析 → 核查 → 确认的问题给主审复核。

终极蓝图已写在 `..\工程全生命周期结算审计多智能体系统（Multi-Agent System）架构设计方案.md`（1704 行，已读完）。当前项目是它的可运行原型。

## 2. 代码在哪

- `结算审核智能体\app.py` — Flask 后端入口，端口 **5100**
- `结算审核智能体\settlement_platform.html` — 单页前端（内嵌 CSS/JS）
- `结算审核智能体\settlement_audit_engine.py` — 确定性计算引擎
- `结算审核智能体\demo_data\settlement_demo.json` — 演示数据（南方产业园二期项目）
- `结算审核智能体\test_settlement_audit_engine.py` — 19 个 unittest，全绿

## 3. 已经完成的

- 三表比对引擎（工程量/单价/金额），疑点检测、反舞弊评分、知识问答
- 多智能体编排（主审驾驶舱 + 规则比对 + 造价/商务/履约专家 + 知识库机器人）
- **双栏穿透核对窗**：左原件右结构化字段，BBOX 高亮，字段置信度 + 人工纠偏，疑点可穿透到原件（3 个疑点已挂：BG-018/ZQ-023/NF-026）
- 知识库小机器人接入**真实 LLM**（本地 Ollama `deepseek-r1:1.5b`，`source=ollama` 已验证）
- 交付导出三件套（json/csv/md）+ 主审复核闭环（decision → remediation）
- 19 个测试全通过

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
# 本次会话追加交接（模型链 / 九宫格 / 跨模型提示链）

> 以下记录最近一次会话的任务进展，接在原有项目交接之后。文件已改好并实测通过。

## A. 本次会话在做什么

按方案文档 **§3.1「双轨为经、穿透为纬」三维多模型关联图谱** 与 **§3.2 模型间核心关联提示链**，在 `settlement_audit_engine.py` 新增：

1. **11 个核心业务模型执行引擎** —— `run_core_models(data)`，按事前(M1)/事中(M2)/事后(M3) 批量执行审计模型并逐条输出命中项 `{hit,title,amount,detail,risk}`。
2. **8 步模型链（执行流水线）** —— `build_model_chain(core_results, issues)`，将 13 个模型归入 9 个 step 节点，每步统计 `hit_count / hit_amount / hits`。
3. **九宫格判定（风险指数 R × 真实效益指数 E）** —— `build_nine_grid(core_results, data)`，输出 3×3 分区（双优/观察/稳定/危险…）、`zone/diagnosis/action`、`risk_details/benefit_details`、`summary.zone_color`。
4. **三条跨模型证据链（C1/C2/C3）** —— `build_cross_model_hints(core_results, issues)`：
   - C1 招采倒挂→材料超耗→结算超付（M3.3/M2.3/M2.1）
   - C2 确权滞后→久竣未结→变更签证滞后（M1.1/M1.2/M1.3）
   - C3 存货未报耗→隐性贴息→虚盈实亏（M3.1/M3.2/M1.4）

**总体目标**：接入 `analyze()` 与 `build_structured_model()` 的输出，再进 API/前端。

## B. 已完成的改动（文件：`settlement_audit_engine.py`）

| 函数 | 状态 | 说明 |
|---|---|---|
| `_default_audit_models()` | ✅ 已扩展 | 现为 **13 个模型**：`M-PRE-FILTER` + `M1.1~M1.4` + `M2.1~M2.5` + `M3.1~M3.3` |
| `run_core_models(data)` | ✅ 完整 | 前置过滤（ZZ 前缀/异常状态）+ 全部 11 个模型，返回 `dict[str, list[hit]]` |
| `build_model_chain(core_results, issues)` | ✅ 完成 | 9 个 step 节点，`total_hits / pipeline_status` |
| `build_nine_grid(core_results, data)` | ✅ 完成 | R/E 指数、3×3 分区、`zone_color`（已修反转 bug） |
| `build_cross_model_hints(core_results, issues)` | ✅ 完成 | 三条证据链，含 `hit_models / all_hit / hit_amount` |
| `build_structured_model(...)` | ✅ 已接入 | 签名新增 `model_chain/nine_grid/cross_model_hints` 三参数；返回 dict 顶层在 `"fraud_assessment"` 之后、`"summary"` 之前新增三个键 |
| `analyze(...)` | ✅ 已接入 | 调用四个新函数；返回 dict 顶层新增 `core_results / model_chain / nine_grid / cross_model_hints` |
| `build_audit_model_catalog(...)` | ✅ 已修复 | 原引用 `M-POST-005`（已不存在）改为 `model.get("model_type")=="整改类"` |

**实测结果**（`demo_data/settlement_demo.json`，命令见下方 C.3）：
- `analyze()` 正常返回；`structured_model` 含全部新键
- `model_chain`：9 steps、total_hits=13
- `nine_grid`：zone=双优区、quadrant=(1,1)（1 基显示）、zone_color=green
- `cross_model_hints`：C1=4 命中（M3.3/M2.3/M2.1）、C2=4（M1.1/M1.2/M1.3）、C3=2（M3.1/M3.2；M1.4 因 demo 数据无生效判决不触发，**属正常不是 bug**）

## C. 当前未完成 / 卡点

1. **`app.py` 尚未暴露新能力的 API 端点**（最高优先，上次被打断处）：在 `/api/settlement-demo/model` 端点（约 193~198 行）后新增
   - `GET /api/settlement-demo/model-chain` → `jsonify(_state().get("model_chain", {}))`
   - `GET /api/settlement-demo/nine-grid` → `jsonify(_state().get("nine_grid", {}))`
   - `GET /api/settlement-demo/cross-model-hints` → `jsonify(_state().get("cross_model_hints", {}))`
   并在 `GET /api/settlement-demo/model-catalog`（约 209~218 行）返回 dict 加上这三个键。参考 `agents` 端点写法。
2. **前端 `settlement_platform.html` 尚未渲染**模型链/九宫格/跨模型提示链（先确认本期是否做 UI）。
3. **测试未跑通（环境问题）**：Flask 未安装 → `test_settlement_audit_engine.py` 里 `from app import app` 直接失败；pytest 也未装。**先 `pip install flask`，再跑** `python -X utf8 -m unittest test_settlement_audit_engine -v`。
4. **`demo_data/settlement_demo.json` 本次有改动**（补了 M3.* / 九宫格相关字段），建议对照方案稿复核字段名与阈值。

## D. 下一步计划（按优先级）

1. 补 `app.py` 三个新端点 + model-catalog 扩展（改完跑 C.3 自检脚本）。
2. `pip install flask` 后跑完整 unittest，修复回归。
3. 按需在 `settlement_platform.html` 渲染三大新能力（先与用户确认）。
4. 复核 demo json 与方案一致性。
5. 确认无用后可清理根目录会话碎片文件（见踩坑第 7 条）。

## E. 本次会话踩过的坑（绝对不要再踩）

1. **`editor` 的 `insert_line` 不会自动加前导缩进**，`new_text` 首行必须自己写全空格；且插入位置若已有内容会**插出重复行**。本次在 `analyze()` 里插 `core_results = ...` 曾造成重复 4 行 + IndentationError。教训：插入后立即 `read_files` 复查该区域，确认只有一份且缩进正确。
2. **`editor` 的 `old_text` 必须与文件逐字符一致**（含缩进/引号，不含 `\n` 转义）。用带转义的旧文本匹配会报 `No replacement performed`。教训：先 `read_files` 拿精确原文再替换。
3. **在字典 return 块内插入新键容易插错层级**：本次把三行新键插进了 `"summary": {...}` **内部**，导致 summary 结构损坏。教训：插入前先读整个 return 块定位顶层键位置，插完 `list(r['structured_model'].keys())` 验证。
4. **`old_text` 出现多处副本会失败**（`multiple occurrences`）：重复块要带上上下文（上一行/下一行）使其唯一。
5. **PowerShell 无 `head`**，用 `Select-Object -First N`；pytest 不存在时用 unittest。
6. **Windows 默认 GBK 编码读 UTF-8 文件会 `UnicodeDecodeError`**：所有 `python -c`/脚本读文件加 `-X utf8` 或显式 `encoding='utf-8'`。
7. **根目录 `_e2.txt/_e3.txt/_e4.txt/_e_m.txt/_a1.txt/_a2.txt/_e_a.txt` 是历史会话编辑导出碎片，不是源码**：搜索会混入结果（如 `M-POST-005` 命中这些 txt），别把它们当基线，确认无用再删。
8. **demo 数据 `M1.4` 本来就不触发**，C3 只命中 M3.1/M3.2 且 `all_hit=False` 是预期状态，别为此改代码。
9. **`M-POST-005` 旧模型 ID 已移除**，任何继续引用它的代码都要检查是否已适配 13 模型新集合。

## F. 关键代码位置（当前文件行号）

- `run_core_models` ≈ 581 行
- `build_model_chain` ≈ 731 行
- `build_nine_grid` ≈ 789 行
- `build_cross_model_hints` ≈ 846 行
- `build_structured_model` ≈ 1172 行（签名新参数 ≈1181；返回新键 ≈1265）
- `analyze` ≈ 1280 行（调用新函数 ≈1291；返回新键 ≈1312 起）

## G. 快速自检命令（不依赖 flask）

```
cd "e:\南审培训\结算审核智能体"
python -X utf8 -c "import json; from settlement_audit_engine import analyze; \
r=analyze(json.load(open('demo_data/settlement_demo.json', encoding='utf-8'))); \
print('OK' if r.get('model_chain') and r.get('nine_grid') and r.get('cross_model_hints') else 'FAIL')"
```
# 本次会话追加交接（第三章升维：九大管理归因与战略审计定论链）

> 用户新要求：**《模型建设/全面数字化商务与工程结算审计系统端到端落地实施方案.md》第三章内容有误**（还是旧的“三条关联提示链”），按用户提供的“核心替换输出”整体升维为 **九大管理归因与战略审计定论链**。本次会话已完成文档 + 引擎 + 测试 + API 验证，D.3 前端渲染留待下一会话。

## A. 本次会话改了什么

1. **文档**：`模型建设/全面数字化商务与工程结算审计系统端到端落地实施方案.md` 第三章 §3.2 由「模型间核心关联提示链（三条）」整体替换为「九大管理归因与战略审计定论链」：将 11 个微观计算模型升维聚合成九大高管视角管理归因与战略定论链，分**商务结算合规（4 链 G1~G4）** + **公司治理与战略风控（5 链 G5~G9）** 两大阵列；每条链 = 演进路径 + 业务机理与深层痛点 + 智能体三段式公文定论（定性判断 / 量化支撑 / 管理定论）；末尾附「11 微观模型 → 九大管理归因链」聚合映射表。同步改掉全文残留：第二屏看板标题/关联链可视化、第二屏小节标题、点亮说明、第八步流水线第 六 步、方案实施总结（三处）。
2. **可行性分析报告**：第 183 行「关联链可视化」→「定论链可视化（G1~G4 + G5~G9，ECharts 桑基图）」。
3. **引擎** `settlement_audit_engine.py`：`build_cross_model_hints` 由 C1/C2/C3 升维为 **G1~G9**（每条含 section/name/path/description/models/conclusion{qualitative,quantitative,management}/severity/action）；返回新增 overview / sections（两大阵列）/ matched_chains / unmatched_chains / pipeline_status；`build_model_chain` 第 6 步改为「九大管理归因与战略定论链串联」；`build_nine_grid` docstring 修正为「方案 §3.5」。
4. **测试**：新增 2 条单测（九链结构 + step6 引用），**19 个测试全绿**。

## B. 实测结果（demo_data/settlement_demo.json + Flask test_client）

- `cross_model_hints`：total_chains=9；sections = 商务结算合规×4 + 公司治理与战略风控×5
- 命中：G1(5) G2(4) G3(2) G4(5) G5(1) G6(1) G8(1) G9(1)；未命中：**G7**（只映射 M1.4，demo 无生效判决不触发，正常非 bug）
- 三个端点均 200：`/api/settlement-demo/model-chain`（step6=九大管理归因与战略定论链串联）、`/nine-grid`、`/cross-model-hints`；`/model-catalog` 含三键

## C. 九链与旧三链对照（引擎聚合模型映射）

| 新链 | 旧三链关系 | 引擎聚合模型 |
|---|---|---|
| G1 先干后谈与未定价变更责任错配链 | 由 C2 的 M1.3 段 + M2.4/M2.2 升级 | M2.4 / M1.3 / M2.2 |
| G2 结算滞后与虚盈实亏时间亏损链 | 由 C2 的 M1.1/M1.2 + C3 的 M3.1/M3.2 升级 | M1.1 / M1.2 / M3.1 / M3.2 |
| G3 证据治理与六大证据链断点阻断链 | 新增（六链拓扑） | M2.2 / M1.4 |
| G4 层层转包与影子分包价格失控链 | 由 C1 升级 | M3.3 / M2.1 / M2.3 / M2.5 |
| G5 盲目垫资与履约惯性击穿强制止损红线链 | C1 的 M3.3 段单列 | M3.3 |
| G6 “三重一大”与分级授权制度性失守链 | C1 的 M2.1 段单列 | M2.1 |
| G7 诉讼判决瞒报与财务收入虚增失真链 | C3 的 M1.4 段单列 | M1.4 |
| G8 供应链贴息反噬与全口径真实效益侵蚀链 | C3 的 M3.2 段单列 | M3.2 |
| G9 “时空穿越”人证分离与现场安全实质悬空链 | 新增（视频时空碰撞模型） | M2.4 |

## D. 未完成 / 下一步（D.3 前端渲染）

`settlement_platform.html` 仍**未渲染**三大新能力。按上一会话已确认做 D.3，且设计已升维为九大链，前端应渲染：
- 模型链 → 结构化建模区（`/api/settlement-demo/model-chain`）
- 九宫格 → 主审驾驶舱/总览区（`/api/settlement-demo/nine-grid`）
- 跨模型证据链 → 报告区（`/api/settlement-demo/cross-model-hints`），**必须按九大链两大阵列（G1~G9 + 演进路径 + 三段式公文定论）渲染，不再使用 C1/C2/C3 文案**

## E. 本次会话新增踩坑

1. `editor` 连续多次把 `old_text`/`new_text` 生成成同文（前导空格系统性丢失），缩进修复空操作；改用 `_fix_test_indent.py` 按 `line.startswith(...)` 确定性补 4 空格才成功。教训：**改行首缩进别依赖手写 old/new 文本，用短脚本处理**。
2. 大段中文 + ASCII 图替换（§3.2 约 27 行）用 `read_files` 拿边界 + Python 按行号切片拼接最稳；`Path.read_text()` 不支持 `newline` 参数，用 `open(..., newline="")`。
