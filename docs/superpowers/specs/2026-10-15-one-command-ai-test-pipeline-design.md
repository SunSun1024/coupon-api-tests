# 一条命令完成 AI 用例生成、执行与报告：设计

状态：两阶段模型方案已获用户审查通过并实现；Mock 模型和本地 Coupon API 流程已通过自动化验证。

实施清单见 [实现计划](../plans/2026-10-15-one-command-ai-test-pipeline.md)。

## 目标

用户在 CLI 输入本次业务测试重点后，一条命令完成用例生成、执行计划生成、严格校验、本地 Coupon API 执行和 HTML/JSON 报告。用户不手写 Mapping、不手动审核每次运行、不另开 Uvicorn 终端。

```bash
python -m ai_testgen run \
  --requirements examples/ai_testgen/coupon-redemption.md \
  --openapi examples/ai_testgen/coupon-openapi.json \
  --focus "验证同一订单只能成功核销一张优惠券" \
  --output-dir artifacts/ai_testgen
```

## 采用的方案

分成两个模型阶段，以降低结构化输出长度风险，并让每个阶段有清楚的输入与校验边界：

1. 用现有 `TestCaseGenerator` 根据需求、OpenAPI 和 `--focus` 生成并校验业务用例。
2. 将**已校验用例**、正式业务规则和受信任 OpenAPI 发给同一模型服务，要求输出现有执行 DSL（请求、响应提取、状态码断言、JSON 字段断言）。这不是让模型返回 Python 或 shell 命令。
3. 本地 Pydantic、来源引用、操作白名单、OpenAPI、请求/响应 Schema、变量先后顺序、路径安全与断言引用校验执行计划。
4. 程序为校验通过的数据产生机器校验凭据，标明检查器版本和输入内容摘要；不冒充人工审核。只有校验通过的操作能交给固定执行器。
5. CLI 在启动 Pytest 前创建**本次运行独有的临时 SQLite**，通过已有 ASGI in-process HTTP session 调用真实 Coupon FastAPI 路由，不需要网络端口或已运行的服务。Pytest 每个用例使用新用户和订单，各自清理创建的数据；运行结束删除临时数据库。
6. 每条生成用例（包括无法制定安全计划的用例）都被参数化到 Pytest：计划失败在执行 HTTP fixture 前作为明确的失败结果记录。复用现有 pytest-html/Allure 插件，另写总览 HTML/JSON 包含两阶段状态、模型用量、校验问题、全部逐条用例结果及失败请求信息；总览链接到 pytest-html 的详细报告。

一次 `run` 至少包含两个模型阶段；生成阶段沿用当前分批策略，因此请求数可能多于一次，截断拆批也计入生成阶段用量。执行计划通常用一次结构化模型调用完成。第二阶段同样记录原始输入、输出、模型名、Prompt 版本、用量和错误。报告列出两阶段成本，不在日志回显 API Key。

被测模型不能指定 Base URL、主机、端口、代理、Header、Cookie、Python、SQL 或 shell。目标代码固定为本地优惠券应用；工具只接受已登记 Coupon API 操作并复验契约。请求重定向关闭，代理禁用，所有请求设超时，写操作不重试。执行计划若不合法，相关用例记为计划校验失败；整个运行继续产出报告，并以非零退出码表示不完整/失败，不要求用户手工修补。

### 为什么用两个模型阶段

将自然语言用例和可执行请求混在一个响应中会变长，现有生成器已经需要在截断时拆批。保留第一阶段当前稳定的 TestSuite 输出，再通过第二阶段生成精简、受严格模型约束的操作序列，可分开判断“业务用例生成失败”和“转换计划不合法”。代价是第二阶段增加一次模型服务计费和耗时；两阶段原文均被保存以便排查。

## 结果状态与报告

每个生成用例都在汇总报告出现一种状态：

- `passed`：执行计划校验通过，真实 API 响应符合映射断言。
- `assertion_failed`：请求成功发出，但状态码或 JSON 断言不符，报告保留期望/实际值。
- `plan_invalid`：模型没有给出合法或安全的执行计划，0 HTTP 调用，并明确字段错误。
- `execution_error`：服务、响应或数据准备出错。
- `generation_failed`：第一阶段没有为该样本产出可评估用例。

计划校验失败的用例也作为 Pytest 失败项显示，并写入总览 HTML/JSON，不会被静默跳过。整体退出码为 0 仅当所有选中用例均有有效执行计划、Pytest 及资源清理都通过；否则为非零。总览报告始终写出：即使第一阶段报错或没有用例也有 generation_failed 结果；完整运行的 Pytest HTML 报告另作为详细报告链接，Allure 保留逐用例步骤与失败 HTTP exchanges。

需要说明：结构化计划合法只说明操作可安全执行，不能逻辑上保证模型对业务的理解正确。错误断言会作为失败用例报告，用户可以从 action、expectation、请求及实际响应定位模型质量问题。运行环境只允许本地 Coupon API。

## 数据与兼容性

每次 run 使用唯一 run_id，并在原有 raw/validated 之外保存 `execution-plan/raw`、`execution-plan/validated`、`execution/`、`results/`、`allure-results/` 和 `report.html`。机器校验凭据不同于人工 `ReviewReceipt`，绑定 source、完整 plan、OpenAPI 摘要及验证器版本；任何输入变化都需要重跑校验。原有 `generate` 仍只生成候选，不执行 API；原有显式人工 review/preview/adapt 路径继续可用。新 `run` 命令是自然语言输入到执行报告的完整自动入口。

所有产物保存在 `.gitignore` 下的 `artifacts/ai_testgen/`；失败时也保存已有阶段的原文和结构化原因。第二阶段失败不会删除第一阶段已保存的 raw 和 validated。报告目录中每次运行独立归档，不能覆盖以前的结果。

## 文件设计

| 文件 | 修改内容 |
| --- | --- |
| 新增 `ai_testgen/planner.py` | 两阶段中的执行计划 Prompt 与受限结构化响应模型，记录第二次 LLM 调用 |
| 新增 `ai_testgen/pipeline.py` | 串联生成、计划、校验、隔离环境、pytest 与各阶段失败处理 |
| 新增 `ai_testgen/report.py` | 汇总各阶段状态、用量、用例结果，生成始终可用的 HTML/JSON 总览 |
| 修改 `ai_testgen/cli.py` | 新增 `run` 子命令，调用 pipeline，并显示最终总览和详细报告路径 |
| 修改 `ai_testexec/models.py` | 新增自动计划校验凭据/阶段汇总模型，与人工 ReviewReceipt 区分 |
| 修改 `ai_testexec/adapter.py`、`review.py` | 复用来源/OpenAPI/映射验证；增加机器凭据绑定，不伪造 reviewer |
| 修改 `ai_testexec/pytest_plugin.py`、`executor.py` | 接受经验证的自动执行产物并记录所有状态，单计划失败也能进入报告 |
| 修改 `tests/conftest.py` | 新增由自动 pipeline 显式开启的 in-process Coupon ASGI fixture；普通测试行为不变 |
| 新增 `tests/test_ai_testgen_pipeline.py` | 将所有生成用例作为 Pytest 参数；无效计划记录为失败项，跳过网络 fixture |
| 复用 `tests/support/asgi_coupon_session.py`、`tests/support/coupon_api_client.py` | 真实应用路由、路径白名单、请求日志和资源清理 |
| 修改 `README.md` | 新增一条命令的完整流程；现有人工执行路径只作为高级/显式模式说明 |
| 修改 `.gitignore` | 确保两阶段和测试报告产物均在 artifacts 下 |

遵循项目已有运行目录和人工修改保护，不覆盖其它未提交文件；不提交或推送。

## 验收

- `run --help` 不要求 API Key；缺少密钥时运行阶段指出缺哪个变量，不打印秘密。
- 使用模型 Mock 的完整流水线生成、计划、执行报告，无真实模型网络调用。
- 第一阶段 Schema 错误、截断、业务验证失败；第二阶段非法 JSON、缺失字段、不支持的 operation、错误路径、未知变量、请求 Schema 不匹配，各阶段均保留 raw、错误类别和汇总报告。
- 非法计划发出请求的次数为 0；尝试任意域名、端口、header 或 Python 时拒绝。
- 有效的同订单双券计划在 Coupon API 中执行：第一张成功，第二张 409，状态与黄金业务规则一致，并在报告中逐条显示。
- 状态码错误、JSON 断言失败、请求超时与清理失败分别产生可定位结果。
- 同一 run 内各用例隔离，重复运行两次使用独立 SQLite，目录结束后删除；不会使用或污染开发数据库。
- 两个阶段的原始请求/输出及 token 数汇总完整；API Key/认证信息不会进入快照。
- 一条命令完成后得到 HTML 和 JSON 报告，不需要人工编辑执行映射、人工审核收据或单独启动服务。
- 保持既有普通 `ai_testgen generate`、手写 pytest 回归以及显式人工审核流程兼容。

## 设计边界

首版只支持仓库中 Coupon API 的注册接口和当前支持的 HTTP/JSON 字段。遇到未支持业务操作或无法建立断言的用例，自动记为 `plan_invalid`，让报告说明能力缺口；不执行模型生成代码，不假称所有任意 API 都能安全自动化。
