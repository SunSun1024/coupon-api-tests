# 优惠券接口自动化测试项目

这是一个用于学习和演示接口自动化测试的项目。项目包含一个基于 FastAPI 和 SQLite 的优惠券服务，以及用 Pytest 编写的 HTTP 接口测试。测试通过真实 HTTP 请求创建、领取、核销和查询优惠券，并检查接口响应与业务状态。

## 项目功能

- 创建优惠券，校验金额、库存和过期时间。
- 用户领取优惠券，校验有效期、库存和重复领取。
- 校验过期优惠券无法领取或核销，并检查失败后数据状态未改变。
- 并发抢最后一张优惠券时，只允许一个用户领取成功。
- 用户核销已领取的优惠券，校验最低消费，并保证实付金额不会小于 0。
- 查询用户优惠券，验证不同用户之间的数据隔离。
- 使用 JSON 文件驱动无效创建请求和满减核销场景。
- 使用 Pydantic 响应模型校验成功响应中的字段、类型和状态值。
- 使用 fixture 管理 API 会话和测试数据清理。
- 第一次发送接口请求前检查 API 服务是否可用，并提示如何启动服务。
- 在本地生成 Allure 或 pytest-html 测试报告。
- 通过 GitHub Actions 在 push、pull request 和每日定时任务中运行回归测试，并发布最新的 Allure HTML 报告。

## 业务流程

```text
创建优惠券 → 用户领取 → 用户核销 → 查询用户优惠券状态
```

金额字段使用“分”为单位的整数。例如，100 元表示为 `10000`。优惠金额不会超过订单金额，因此实付金额不会为负数。优惠券状态为 `claimed`（已领取）或 `redeemed`（已核销）。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/coupons` | 创建优惠券 |
| `POST` | `/users/{user_id}/coupons/{coupon_id}/claims` | 用户领取优惠券 |
| `POST` | `/users/{user_id}/coupons/{user_coupon_id}/redemptions` | 核销用户已领取的优惠券 |
| `GET` | `/users/{user_id}/coupons/{user_coupon_id}` | 查询用户优惠券状态 |

核销请求需要提供 `order_id` 和 `order_amount_cents`。成功响应会返回实际抵扣金额 `applied_discount_cents` 和实付金额 `payable_amount_cents`。

## 项目结构

```text
coupon_api/                  FastAPI 服务和 SQLite 数据库逻辑
  main.py                    优惠券接口及业务规则
  schemas.py                 请求字段和基础校验
  database.py                SQLite 连接、建表和数据库路径
tests/
  conftest.py                服务就绪检查、base_url、HTTP 会话和清理 fixture
  data/                      JSON 参数化测试数据
  support/
    coupon_api_client.py     集中的 API 请求客户端
    data_cleanup.py          清理当前测试创建的数据
    response_schemas.py      成功响应的数据结构校验
  test_*.py                  接口、服务就绪检查和响应模型测试
ai_testgen/                  AI 候选测试用例生成与校验模块
  config.py                  LLM 环境配置和安全校验
  llm_client.py              OpenAI-compatible Chat Completions 客户端
  models.py                  Pydantic 结构化输出模型
  generator.py               Prompt、来源追踪和生成结果持久化
  validator.py               用例、重复项和 OpenAPI 路径校验
  planner.py                  第二阶段受限执行计划生成
  pipeline.py                 一键生成、校验、执行和报告编排
  report.py                   HTML/JSON 汇总报告
  cli.py                     generate/run 命令行入口
examples/ai_testgen/         优惠券核销需求和 OpenAPI 示例
docs/
  testing-framework.md       fixture、数据清理、报告和 CI 的补充说明
.github/workflows/
  coupon-api-tests.yml       GitHub Actions 回归测试工作流
```

## 环境准备

项目 CI 使用 Python 3.14。建议本地也使用相同版本，减少本地和 CI 的环境差异。先进入项目目录并创建虚拟环境：

```bash
cd coupon-api-tests
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 启动服务并运行测试

接口测试需要先启动 FastAPI 服务。可以在 VS Code 中打开两个终端。

终端一启动服务：

```bash
cd coupon-api-tests
source .venv/bin/activate
python -m uvicorn coupon_api.main:app --reload
```

默认服务地址是 `http://127.0.0.1:8000`，也可以在浏览器打开 `http://127.0.0.1:8000/docs` 查看 FastAPI 文档。

终端二运行优惠券回归测试：

```bash
cd coupon-api-tests
source .venv/bin/activate
python -m pytest -q -m regression tests
```

测试默认通过 `COUPON_API_URL` 使用 `http://127.0.0.1:8000`。如果服务运行在其他地址，可以在运行测试的终端设置环境变量，例如：

```bash
export COUPON_API_URL="http://127.0.0.1:8001"
```

使用优惠券 API 的测试第一次创建客户端时会检查服务状态。如果服务没有启动，Pytest 会提前停止并打印启动命令。

`COUPON_DB_PATH` 可以指定 SQLite 数据库文件路径。服务和测试清理逻辑必须使用相同的数据库文件；本地默认使用项目根目录的 `coupon.db`。GitHub Actions 会为每次运行设置独立数据库。

## 常用测试命令

```bash
# 运行全部测试
python -m pytest -q

# 只运行优惠券回归测试
python -m pytest -q -m regression tests

# 查看回归测试会收集哪些用例，不实际发送请求
python -m pytest --collect-only -q -m regression tests

# 运行单个用例并显示更详细的失败信息
python -m pytest -vv tests/test_coupon_flow.py::test_failed_redemption_keeps_coupon_claimed
```

`regression` 用例覆盖优惠券业务规则、数据隔离、JSON 数据驱动场景、响应结构和清理逻辑。`smoke` 标记用于快速检查关键流程；其中还包含访问 JSONPlaceholder 的外部示例接口，因此运行这部分用例需要网络连接。优惠券接口测试需要本地服务保持运行。

## 测试数据与隔离

`tests/data/coupon_create_invalid_cases.json` 保存创建优惠券的无效参数，例如缺少名称、折扣为 0 或过期时间没有时区。`tests/data/coupon_redemption_cases.json` 保存满减核销边界，例如订单金额低于门槛、刚好达到门槛以及优惠金额大于订单金额。

Pytest 会把 JSON 中的每条 case 参数化为独立测试。测试为用户、订单和优惠券生成唯一标识，避免不同数据行互相影响。`api_client` 会登记本次测试成功创建的优惠券和领取记录；测试结束时，清理 fixture 只删除登记过的记录，保留数据库中其他已有数据。

`base_url` 和 `api_ready` 是 session 作用域，因为一次测试运行中服务地址和就绪状态通常不变；`api_session`、`api_client` 和数据清理 fixture 是 function 作用域，让每个测试函数使用独立的 HTTP 会话和清理登记器。

## 生成测试报告

### Allure 报告

先安装 Allure CLI（需要 Node.js），再运行测试并生成 HTML 报告：

```bash
npm install --global allure
python -m pytest -q -m regression tests \
  --alluredir=allure-results --clean-alluredir
allure generate allure-results -o allure-report
allure open allure-report
```

`allure-results` 是测试产生的原始结果，`allure-report` 是可浏览的 HTML 报告。

如果使用 `api_client` 的测试在执行阶段失败，Allure 用例详情会附上该用例发出的请求和响应 JSON，包括 HTTP 方法、路径、请求 JSON、状态码与响应正文；连接错误会附上错误类型和消息。只有失败用例会附加这些信息，请求头和 Cookie 不会写入附件。

### pytest-html 报告

```bash
python -m pytest -q -m regression tests \
  --html=coupon-report.html --self-contained-html
```

生成后可以在浏览器中打开 `coupon-report.html`。

## GitHub Actions

`.github/workflows/coupon-api-tests.yml` 在 push 和 pull request 时自动运行回归测试，并每天北京时间 09:00 定时巡检（GitHub Actions 使用 UTC，计划时间是 01:00 UTC；高负载时可能延迟）。也可以在仓库的 **Actions → Coupon API tests → Run workflow** 中手动触发。

最新 Allure 报告可以直接在浏览器打开：[查看 Coupon API Allure 报告](https://sunsun1024.github.io/coupon-api-tests/)。仓库是公开的，因此报告也公开可访问。首次使用前，请到 **Settings → Pages → Build and deployment**，将来源设为 **GitHub Actions**。

Actions 运行记录仍会保留 `coupon-api-allure-report` artifact。它已经是 HTML 报告，但需要下载并解压后打开 `index.html`；上面的 Pages 链接会直接打开最新报告。每次默认分支发布都会替换 Pages 上的旧报告。Pull request 会运行测试并保存 artifact，但不会发布网页报告。

## 补充文档

关于 fixture、数据清理和 CI 配置的更多说明，请阅读 [`docs/testing-framework.md`](docs/testing-framework.md)。

## AI 测试用例生成器

`ai_testgen` 根据业务需求、可选的 OpenAPI JSON、业务规则和测试约束生成结构化候选用例。它只负责生成与校验，不会执行 API 请求，也不会把生成内容自动加入 pytest 数据文件。生成命令只有在你显式运行时才调用模型；CI 中的 AI 模块测试全部使用 Mock，不需要 LLM 密钥。

安装依赖后，在终端配置模型服务。`LLM_API_KEY` 和 `LLM_MODEL` 必填；`LLM_BASE_URL` 用于 OpenAI-compatible 服务，连接 OpenAI 官方 API 时可以省略。可选配置包括超时、重试次数、结构化输出模式、最大输出 Token 数和单次生成用例数：

```bash
export LLM_API_KEY="你的模型服务 API Key"
export LLM_MODEL="服务商提供的模型名称"
# 使用兼容 OpenAI Chat Completions 的第三方服务时设置：
export LLM_BASE_URL="https://模型服务提供的地址/v1"
export LLM_TIMEOUT_SECONDS="30"
export LLM_MAX_RETRIES="2"
export LLM_OUTPUT_MODE="auto"
export LLM_MAX_OUTPUT_TOKENS="8192"
export LLM_MAX_TEST_CASES="6"
export LLM_MAX_CASES_PER_REQUEST="2"
```

用仓库提供的优惠券需求和 OpenAPI 示例生成用例：

```bash
python -m ai_testgen generate \
  --requirements examples/ai_testgen/coupon-redemption.md \
  --openapi examples/ai_testgen/coupon-openapi.json \
  --focus "优先生成同一订单尝试使用第二张优惠券并验证拒绝的场景" \
  --output-dir artifacts/ai_testgen
```

`--focus` 接收一段命令行自然语言，只调整本次用例覆盖的优先级，不会改变需求文档或 API 契约中的业务规则。生成器把它单独放进 Prompt 和结果产物，并纳入输入 SHA-256；用例的 `source_refs` 仍需引用需求、规则、约束或 API 来源。如果只想生成这一类场景，可以同时把 `LLM_MAX_TEST_CASES` 设为 `1`。

也可以通过 `--business-rules`、`--constraints` 提供单独的文本文件，通过 `--requirement-version` 指定需求版本。没有指定版本时，工具会用需求、规则、约束、覆盖重点和 API 文档内容计算 SHA-256 版本。运行 `python -m ai_testgen --help` 可以查看参数；查看帮助不需要设置模型密钥。

`LLM_OUTPUT_MODE=auto` 会先调用 SDK 的 Pydantic 原生结构化输出；只有服务明确以 HTTP 400 表示不支持该结构化输出格式时，才会退回 JSON Object 模式。设为 `native` 可关闭降级，`json_object` 直接请求 JSON，并把 Pydantic 生成的 JSON Schema 作为 Prompt 约束；`prompt` 则只通过 Prompt 要求 JSON。JSON Mode 只保证 JSON 格式，不保证字段和类型符合 Schema，因此本地仍会用 Pydantic 严格校验。兼容服务支持的格式因服务商和模型而异，应按实际能力选择。

默认每次最多生成 6 条用例，`LLM_MAX_TEST_CASES` 可设为 1–50；超出数量的模型响应会被校验拒绝。`LLM_MAX_CASES_PER_REQUEST` 默认是 2，可设为 1–10，用来限制单次模型调用生成多少条。遇到明确的 `finish_reason=length` 时，生成器会把大于 1 条的批次二分后重试；单条仍被截断时会清楚失败，不会保存 validated 结果。超时、鉴权或 JSON/Schema 错误不会触发拆分重试。

`LLM_MAX_OUTPUT_TOKENS` 默认是 8192，可设为 256–65536，并会作为 `max_tokens` 发送给 Chat Completions API。分批可能增加 API 调用次数和输入 Token 用量；CLI 会报告累计 prompt/completion/total tokens 及调用次数，raw artifact 也会逐次保存响应和状态。OpenAPI prompt 会保留操作路径、方法、参数、请求/响应结构、状态码、4xx 响应示例和被引用的组件；会移除成功响应示例、未引用组件和服务器等生成用例不需要的内容。本地 `#/components/...` 引用会递归展开，外部 URL 引用不会下载。原始 OpenAPI 仍用于本地路径校验和输入摘要。

每次运行会在 `artifacts/ai_testgen/raw/` 保存模型原始文本和元数据，在 `artifacts/ai_testgen/validated/` 保存通过校验的 TestSuite、来源目录和校验报告。业务校验失败时仍保留 raw 文件，但不会生成 validated 文件。`test-case-generation-v7` 会把“用例明确声称要验证、但来源没有依据”的预期放入 `unverifiable_expectations`；自动执行会阻止仍含有这类预期的用例。若某个可选响应细节并未列入用例断言，它不会单独阻止执行；模型可以把相关疑问放到套件级 `clarification_questions`。记录的追踪元数据包括 Prompt 版本、模型名称、生成时间、需求版本、输入摘要和 token 用量。该输出目录已加入 `.gitignore`。

需求和 API 文档会作为模型请求内容发送给你配置的服务商。请先移除真实用户信息、凭据及不应外传的内容。程序不把 API Key、输入正文或原始模型响应写入日志；原始模型响应会按功能要求保存在本地忽略目录中。

只运行 AI 模块离线测试：

```bash
python -m pytest -q tests/test_ai_testgen_*.py
```

实现使用了 Python 标准库的 `dataclasses`、`argparse`、`json`、`hashlib`、`datetime`、`pathlib` 和 `logging`；Pydantic v2 负责严格字段、Literal 枚举、嵌套模型与错误定位。OpenAI Python SDK 通过 `OpenAI(..., base_url=..., timeout=..., max_retries=...)` 调用 Chat Completions；每个小批次用 `TestCaseBatch` 作为结构化响应模型，合并并校验后再生成最终 `TestSuite`。程序汇总每次 API 返回的 prompt/completion/total token usage。AI 测试使用依赖注入的 Mock，覆盖客户端和生成器，不调用真实模型。


遇到的问题：单次请求生成2条用例，token上限设置为4096，总会显示模型的返回超出上限，导致最终并未返回正确的content，
解决方法：
  关键修复是：SDK 原生结构化输出遇到 finish_reason=length 时会抛出专用异常；客户端现在能从异常中取回截断文本和 Token 用量，并交给生成器拆分批次。

  设计上把两个数量限制分开了：LLM_MAX_TEST_CASES 限制整次生成的总用例数，LLM_MAX_CASES_PER_REQUEST 限制单次模型调用的用例数，默认是 2。前者控制业务范围，后者降低单次响应过长的概率。

  一次生成的流程是：

  1. 生成器先按单次上限请求一批用例。
  2. 如果模型明确因 Token 上限截断，生成器记录这次失败的响应和用量，再把批次拆小重试。例如请求 2 条被截断，就分别请求 1 条。
  3. 后续请求会带上已生成用例的 ID 和标题，减少重复。
  4. 所有批次合并后再统一做业务校验。每次响应保存在 raw，只有合并结果通过校验才写入 validated。如果单条用例仍被截断，程序会报错并保留原始记录，不会生成不完整的校验结果。

  为支持批次响应，我增加了 TestCaseBatch：单次响应可以为空，表示模型认为没有更多有依据的场景；最终的 TestSuite 仍要求至少有一条用例。这样既能允许分批，也不会为了凑数量让模型编造场景。

  Prompt 也做了精简：只传接口路径、方法、参数、请求和响应结构、状态码及被引用的组件；保留 4xx 响应示例中的实际错误值，移除成功响应示例和未引用组件。这样模型可以依据已记录的业务错误文案，同时避免把冗长的成功样例重复塞进请求。精简按 OpenAPI 对象类型处理，因此 Schema 中恰好名为 example、servers 等字段的业务属性仍会保留。根级和接口级认证要求及其安全方案也会保留。


--focus。你现在可以在命令行直接写自然语言，不需要图形界面，也不会修改需求文档。

  实现上，CLI 把 --focus 当作普通字符串传给生成器。生成器会将它单独放进 Prompt，并明确告诉模型：focus 只用于优先选择测试场景，不能改写需求文档或 OpenAPI 里的业务规则；用例的 source_refs 仍需引用这些正式来源。focus 也会写入 raw 和 validated 产物，并参与输入摘要，方便追踪本次生成的依据。

面试中可能会围绕这些实现追问：

- 为什么把 LLM Client、生成器、Pydantic 模型和业务校验器分层？怎样 Mock 外部依赖？
- Pydantic v2 的 `BaseModel`、`model_validate()`、`model_dump()`、严格类型和 `Literal` 分别解决什么问题？
- JSON Schema Structured Outputs 与普通 JSON Mode 有什么区别？为什么仍需要本地 Pydantic 和业务校验？
- 超时、重试应放在哪一层？哪些异常可以重试，为什么不应无限重试或把所有错误都降级？
- 如何在需求变更后追踪生成结果？输入哈希、需求版本、Prompt 版本、模型名和 Token usage 各自有什么价值？
- 如何避免模型补充未定义的状态码或业务规则？怎样把待澄清问题和无法验证的预期反馈给产品/开发？
- 如何做重复用例检测、OpenAPI method/path 检查，以及验证失败后的诊断？
- 如何保护 API Key 和业务数据，并确保 CI 不依赖真实 LLM 服务？

## AI 测试用例生成与执行

推荐使用 `run` 命令完成生成、执行和报告。整个流程不需要手动编写或匹配执行映射，也不需要另开 Uvicorn 终端。

### 使用步骤

**1. 配置模型服务。** 在项目终端激活虚拟环境，并设置模型服务提供的 API Key 和模型名；使用 SiliconFlow 等兼容服务时，再设置 Base URL：

```bash
cd /Users/sunsun/coupon-api-tests
source .venv/bin/activate
export LLM_API_KEY="你的 API Key"
export LLM_MODEL="服务商提供的模型名"
export LLM_BASE_URL="https://服务商提供的地址/v1"
```

**2. 输入本次测试重点并运行。** 下面的 `--focus` 只指定覆盖重点，不会修改正式业务规则：

```bash
python -m ai_testgen run \
  --requirements examples/ai_testgen/coupon-redemption.md \
  --openapi examples/ai_testgen/coupon-openapi.json \
  --focus '重点验证同一个订单先成功核销第一张券，再尝试用另一张券核销同一订单；第二次返回409，第一张仍为redeemed，第二张仍为claimed' \
  --output-dir artifacts/ai_testgen
```

原先需要人工完成的“把用例对应到接口请求和断言”现在由第二个模型阶段自动生成执行计划，再由本地校验器检查。第一阶段生成业务用例，必要时会分批请求；通过校验的用例随后由 Pytest 调用本地 FastAPI，使用临时数据库执行。

**3. 查看报告。** 命令会打印报告路径。打开 `summary.html` 查看各阶段和每条用例的结果；`test-report.html` 是 Pytest 详细报告。文件位于 `artifacts/ai_testgen/runs/<run_id>/`。

若用例含有无法验证的预期或无法安全转换，报告会说明原因；这类用例不会发出 API 请求，命令会以失败状态结束。每次运行的模型输出、token 用量和报告都会单独保存。

<details>
<summary>高级用法：人工审核和自定义执行映射</summary>

以下内容仅用于需要手工审核、自定义映射的场景；普通一键流程不需要执行这些步骤。

### 人工审核流程

以下用“同一订单最多只能成功核销一张优惠券”为例。业务规则已经写在 `examples/ai_testgen/coupon-redemption.md` 中。`--focus` 输入的是**这次希望重点覆盖的场景**；如果要增加或修改业务规则，应先更新需求文件，再生成用例。

**1. 在终端写下这次想重点测试的内容。** 在项目目录打开终端并激活虚拟环境。确认已按前文配置 `LLM_API_KEY`、`LLM_MODEL` 和服务商需要的 `LLM_BASE_URL`，然后运行：

```bash
cd /Users/sunsun/coupon-api-tests
source .venv/bin/activate
mkdir -p artifacts/ai_testexec
export LLM_MAX_TEST_CASES="1"

TEST_FOCUS='重点验证同一订单先成功核销一张优惠券，再尝试核销第二张；确认第二次返回409，第一张仍为redeemed，第二张仍为claimed。'

python -m ai_testgen generate \
  --requirements examples/ai_testgen/coupon-redemption.md \
  --openapi examples/ai_testgen/coupon-openapi.json \
  --focus "$TEST_FOCUS" \
  --output-dir artifacts/ai_testgen
```

引号内的文字就是这次输入的测试重点。命令会显示 `validated_output=...`；只有通过结构和业务校验的文件才能进入下一步。记下它显示的路径，下面用 `SOURCE_FILE` 表示这个路径。若结果带有 warning 或待澄清内容，先检查生成文件，并按前文补充正式需求后重新生成。

**2. 将业务用例和执行步骤对上。** 打开刚才生成的 `SOURCE_FILE`，找到 `metadata.run_id`，再找到标题和目标与本次测试相符的用例，记下它的 `case_id`。如果没有相符用例，先调整测试重点或需求文件重新生成。

把同一订单不可叠加的执行步骤复制到单独文件：

```bash
cp examples/ai_testexec/mapping.json artifacts/ai_testexec/mapping.json
```

用 VS Code 打开 `artifacts/ai_testexec/mapping.json`，将文件开头的 `source_run_id` 改成刚才的 `metadata.run_id`，并将 `cases` 中的 `source_case_id` 改成选中用例的 `case_id`。逐步检查映射里的请求顺序、优惠金额、订单金额、状态码和响应字段断言都符合生成的用例。这个示例映射只覆盖“同一订单使用第二张优惠券”的场景；其他场景需要编写并审核对应的映射。

在终端设置 `SOURCE_FILE`。把右侧路径替换为生成命令实际打印的 `validated_output` 路径：

```bash
SOURCE_FILE="artifacts/ai_testgen/validated/粘贴实际生成的文件名.json"
```

先预览，确认终端输出的业务用例、请求步骤和预期结果一致。预览只做本地校验，不发送 HTTP 请求：

```bash
python -m ai_testexec preview \
  --source "$SOURCE_FILE" \
  --mapping artifacts/ai_testexec/mapping.json
```

**3. 确认后审核并转换。** 查看业务用例和映射后运行审核命令。审核文件绑定生成结果、映射和 OpenAPI 的内容；其中任一内容发生变化，都需要重新审核和转换。

```bash
python -m ai_testexec review \
  --source "$SOURCE_FILE" \
  --mapping artifacts/ai_testexec/mapping.json \
  --reviewer "sunsun" \
  --output artifacts/ai_testexec/review.json

python -m ai_testexec adapt \
  --source "$SOURCE_FILE" \
  --mapping artifacts/ai_testexec/mapping.json \
  --review artifacts/ai_testexec/review.json \
  --output artifacts/ai_testexec/execution.json
```

转换报告会指出每条用例是 `ready`、`needs_manual_mapping` 还是 `conversion_failed`。只有 `ready` 的用例会写入执行文件；尚未解释清楚的业务预期不会自动变成可执行断言。如果生成命令显示 `validation=passed_with_warnings`，先检查对应警告；确认已审查后，在 `review` 命令中加上 `--acknowledge-warnings`。该选项不会把仍无法验证的预期变成可执行用例。

**4. 在第一个终端启动本地 API。** 服务和 Pytest 必须使用同一个数据库文件，这样测试结束时才能清理本次创建的数据：

```bash
export COUPON_DB_PATH="$PWD/artifacts/ai_testexec/coupon-tests.db"
python -m uvicorn coupon_api.main:app --host 127.0.0.1 --port 8000
```

**5. 在第二个终端运行已审核用例并生成 HTML 报告。** 进入相同项目目录，激活虚拟环境，并设置与第一个终端相同的数据库路径：

```bash
cd /Users/sunsun/coupon-api-tests
source .venv/bin/activate
export COUPON_DB_PATH="$PWD/artifacts/ai_testexec/coupon-tests.db"
export COUPON_API_URL="http://127.0.0.1:8000"

python -m pytest -q tests/test_reviewed_ai_cases.py \
  --ai-execution artifacts/ai_testexec/execution.json \
  --ai-review artifacts/ai_testexec/review.json \
  --ai-openapi examples/ai_testgen/coupon-openapi.json \
  --ai-results artifacts/ai_testexec/results \
  --alluredir=artifacts/ai_testexec/allure-results \
  --html=artifacts/ai_testexec/report.html --self-contained-html
```

测试结束后，在浏览器打开 `artifacts/ai_testexec/report.html` 查看 HTML 报告；Allure 原始结果保存在 `artifacts/ai_testexec/allure-results/`。如果已安装 Allure CLI，还可以生成可浏览的 Allure HTML：

```bash
allure generate artifacts/ai_testexec/allure-results \
  -o artifacts/ai_testexec/allure-report --clean
allure open artifacts/ai_testexec/allure-report
```

每条 `ready` 用例会作为独立的 Pytest 参数化用例执行，复用现有 API 客户端、资源清理和失败诊断。未传 `--ai-execution` 时，该入口不会执行 AI 用例；现有手写回归和离线模块测试照常运行。

`--openapi` 默认使用 `examples/ai_testgen/coupon-openapi.json`。请查看 `clarification_questions`，确认是否需要补充正式需求；如果某条用例的 `unverifiable_expectations` 非空，该用例会被标记为 `plan_invalid`，补齐来源规则后重新生成才能自动执行。如果转换状态是 `needs_manual_mapping`，检查映射中的 `source_case_id` 以及请求和断言是否覆盖了对应场景。执行失败时，HTML/Allure 报告与 `artifacts/ai_testexec/results/` 中的 JSON 结果会记录失败步骤、预期和实际值。

执行映射第一阶段仅支持三种操作：

| 操作 | 含义 |
| --- | --- |
| `http_request` | 已登记的创建、领取、核销或查询接口；请求体、路径参数和响应提取声明 |
| `assert_status` | 引用前一个请求，检查状态码；每个请求后必须立即检查状态码 |
| `assert_json` | 用 JSON Pointer 检查响应字段，严格比较类型和值 |

值采用明确的结构：`{"kind":"literal","value":10000}` 是字面量；`{"kind":"ref","name":"coupon1"}` 引用先前响应或可信 fixture 的值。`captures` 中的 `{"coupon1":"/coupon_id"}` 表示从响应提取字段。JSON Pointer 支持 `/status`、`/items/0`，键中的 `/` 和 `~` 分别写成 `~1` 和 `~0`。变量不能嵌入 Python 表达式。fixture 工厂固定为 `user_id`、`order_id`、`coupon_name`、`expires_at`，每条用例生成自己的数据。

接入你自己的真实 AI 产物时，把 `--source` 替换为 validated 文件；编写映射并使 `source_run_id` 对应它的 `metadata.run_id`、`source_case_id` 对应用例 ID，再走 preview/review/adapt。自然语言相似不代表数据就可执行，所有请求参数和断言仍需你审查。

目标策略只允许 `local-coupon` 的 `http://127.0.0.1:8000` 和四个登记接口。执行文件不能指定主机、认证头、代理或任意超时，实际请求禁用重定向和环境代理；Method、路径、参数在转换期检查，响应引用的真实值在请求前再次检查。本机审核记录是防误执行机制，不是带身份认证的远程审批系统。

契约校验支持当前 OpenAPI 3.1 示例中的严格类型、必填字段、范围、长度、枚举、const、组合规则、数组、带秒和时区的 date-time、additionalProperties 和本地引用。它是有限离线校验器；其他关键字/format、外部或递归引用、header/cookie 和认证接口显式拒绝，不能当作完整 JSON Schema 实现。query 首期只支持 `form`、`explode=true` 的非 null 标量，布尔值编码为 `true/false`；对象、数组和其他序列化方式拒绝转换。映射的 `json_body: null` 表示不发送请求体，不能用它满足必需的请求体，也不表示发送 JSON 正文 `null`。首期不发送违反请求 Schema 的畸形参数；参数校验测试需要后续专门审核的模式。

转换报告位于 execution 输出旁的 `.conversion.json`；结果位于 `--ai-results`。结果区分 `assertion_failed`、`execution_error`、`review_required`、`review_invalid` 和 `conversion_failed`，记录失败步骤、对应请求的 `request_id` 及源运行/用例/执行 ID。即使审核失败，也保留观察到的标识、规范化输入 SHA-256 和文件路径；`trace_verified=false` 表示这些标识尚未通过审核核对，不能作为执行授权。失败时复用原有 exchanges 附件，成功和失败都附上结果关联 JSON；清理失败会更新结果为 execution_error。

转换失败且没有 ready 用例时，如果指定的输出路径已有旧执行文件，CLI 会将其移到 `.stale-<随机ID>.json` 备份，转换报告的 `invalidated_output` 记录备份名称，避免把旧成功文件误当作本次结果。输入、审核、执行输出、转换报告和去重数据库不能使用相同路径。

默认去重数据库 `case-registry.sqlite3` 位于执行输出旁，`--registry` 可指定其他路径。它识别不同生成运行的相同执行内容，忽略展示 ID 和运行时 UUID；同一已审核用例重复转换、重复回归允许。它不保证识别所有自然语言语义近似的用例。

运行离线验证（真实 FastAPI 路由与隔离 SQLite 通过 ASGI 测试传输执行，无付费模型或外网）：

```bash
python -m pytest -q tests/test_ai_testgen_*.py tests/test_ai_testexec_*.py
```

设计细节见 [审核用例执行设计](docs/superpowers/specs/2026-10-14-reviewed-case-execution-design.md)，实施记录见 [实现计划](docs/superpowers/plans/2026-10-14-reviewed-case-execution.md)。

</details>
