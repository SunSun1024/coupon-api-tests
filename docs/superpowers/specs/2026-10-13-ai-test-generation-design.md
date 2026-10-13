# AI 辅助接口测试用例生成模块设计

## 背景与目标

第一阶段已建立 FastAPI 优惠券示例服务和 Pytest 接口自动化测试。第二阶段在现有框架上增加一个独立的 AI 测试用例生成模块：从自然语言需求和/或本地 API 文档提取测试点，生成固定结构的候选用例；通过结构、业务规则和重复检查后，由人工审核，再转换为现有 Pytest 数据驱动测试可以执行的数据。

本设计优先复用现有 `CouponApiClient`、fixture、断言、数据清理、参数化和报告流程。首个可执行场景聚焦已有 JSON 参数化覆盖的优惠券核销边界。后续可以按相同的版本化 Schema 增加优惠券创建校验、领取、归属隔离和过期场景。

## 当前项目分析

### 目录和职责

- `coupon_api/main.py` 实现优惠券创建、领取、核销、查询接口及业务规则。
- `coupon_api/schemas.py` 使用 Pydantic 定义创建和核销请求字段及基本校验。
- `coupon_api/database.py` 管理 SQLite 数据库路径、连接、事务和初始化。
- `tests/conftest.py` 提供 `base_url`、服务就绪检查、Requests 会话、API 客户端和数据清理 fixture；失败用例还会把 HTTP 交换详情附到 Allure。
- `tests/support/coupon_api_client.py` 统一请求构造、超时、请求/响应记录和创建数据登记。
- `tests/support/response_schemas.py` 使用 Pydantic 模型校验成功响应的字段、类型和状态。
- `tests/data/` 保存创建无效参数和核销边界的 JSON 数据；对应测试模块负责准备业务状态并执行断言。
- `.github/workflows/coupon-api-tests.yml` 在 push、PR、定时和手动触发时运行回归测试，并生成、保存和发布 Allure 报告。

### Pytest、fixture 和参数化

`base_url` 和 `api_ready` 使用 session 作用域；HTTP 会话、API 客户端和清理登记器默认为 function 作用域。一个测试函数可以通过同一个 API 客户端发送多条 HTTP 请求；新测试函数会获得新的 function fixture。SQLite 文件是否共享由 `COUPON_DB_PATH` 决定，不由 fixture 作用域决定。

当前数据驱动测试在模块导入时用 `json.load` 读取数据，再通过 `pytest.mark.parametrize` 展开用例。创建校验 JSON 有 6 条数据、核销 JSON 有 4 条数据；两种文件使用不同字段，并各自对应固定测试函数。因此，在受支持的数据文件中增加一条数据后，下次 pytest 收集时会产生一个新用例；当前框架还没有通用的 JSON/YAML 流程解释器。当前 `pytest --collect-only -q` 共收集 38 个用例，这只是收集验证，不代表用例已执行。

### 客户端、数据、断言和运行环境

接口测试复用 `CouponApiClient` 完成真实 HTTP 请求，并直接断言状态码、错误字段、响应结构、业务结果及查询后的数据库状态。成功响应通过 Pydantic 模型检查。每个业务用例通过唯一标识隔离用户、订单和优惠券，清理 fixture 只删除本次登记创建的数据。业务测试按 `regression` 标记，关键流程可同时有 `smoke` 标记；独立的 JSONPlaceholder 示例标记为 `smoke`，GitHub Actions 明确运行 `regression` 集合。

当前配置使用 `COUPON_API_URL` 和 `COUPON_DB_PATH`。没有独立的 Python 日志框架；API 客户端记录交换详情，失败的测试调用将详情附到 Allure。项目支持 pytest-html、Allure 和 GitHub Actions Pages 报告。GitHub Actions 运行已批准的回归用例，不应在普通测试或 CI 中调用 LLM。

## 设计选择

考虑了三种方式：

1. **直接生成现有 JSON。** 开发量最小，但模型输出与单个测试函数的字段耦合，难以追踪需求、演进 Schema 和区分候选与已批准数据。
2. **生成独立候选格式，再校验、审核和转换。** 中间多一个小型转换层，但能固定输出契约、保留需求来源，并保证只有人工批准的数据会进入 Pytest。此方案符合当前目标，作为推荐设计。
3. **新建通用 API 步骤解释器。** 可覆盖任意接口流程，但会重复实现现有客户端、fixture 和断言机制，并扩大安全边界。首版不采用；未来只有在固定场景适配器无法满足需求时再评估。

## 推荐架构

模块由显式命令启动，不嵌入 Pytest 收集或执行过程。自然语言需求使用本地 Markdown 或文本文件；API 文档首版使用本地 OpenAPI JSON。用户可以提供其中一种或同时提供两种。OpenAPI 用于明确路径、方法、字段和类型，业务需求用于说明最低消费、抵扣上限等行为。若输入没有说明某项业务预期，模型必须列出假设或未决点，人工澄清前不能批准对应案例。

第一版直接通过 HTTP 调用一个 LLM API，不采用 LangChain、LangGraph 或多 Agent。服务商尚未指定；默认实现一个可替换的 OpenAI-compatible 客户端，配置项为 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。现有 `requests` 可用于 HTTP 调用，Pydantic 可用于 Schema 校验，首版无需额外 AI 框架依赖。

```text
本地需求 / OpenAPI JSON
  → ai_testgen 命令行
  → LLM 生成测试点和候选案例
  → JSON 解析、Schema 校验、业务检查、去重
  → artifacts/ai_testgen/pending/ 中的候选文件
  → 人工审核并选择批准案例
  → 转换为 tests/data/ai_generated_coupon_redemption_cases.json
  → 现有 pytest 参数化用例与 API fixtures 执行
  → 现有数据清理、断言、Allure 和 GitHub Actions 报告
```

### 复用和修改范围

直接复用 `CouponApiClient`、function/session fixtures、响应模型、数据清理器、现有核销参数化测试的业务流程、Allure 和 CI。API 客户端不接受模型生成的 URL 或 HTTP 方法；执行器只调用固定的优惠券场景测试。

新增 `ai_testgen/` 包，按单一职责实现配置、Pydantic 输出模型、LLM HTTP 客户端、提示词/生成编排、校验去重、批准转换和 CLI 入口。新增离线单元测试覆盖这些模块，并标记为 `regression`，让现有 CI 在不访问 LLM 的情况下运行它们。小幅修改 `tests/test_coupon_redemption_data.py`，使其读取原有人工用例与独立的已批准 AI 用例；新增的批准数据文件初始为空。候选目录加入 `.gitignore`，防止待审核输出被 Pytest 或 CI 自动收集。README 增加本地配置和生成、审核、批准、运行说明。CI 工作流不增加 LLM 步骤，也不保存 LLM 密钥。

## 候选用例 Schema 建议

外层测试集包含 `schema_version`、来源元数据、测试点和案例。案例使用严格类型和受限枚举；首版执行适配器支持 `coupon_redemption`。后续场景通过新增带版本的场景模型扩展，不允许自由格式的操作步骤。

```json
{
  "schema_version": "1.0",
  "source": {
    "files": ["requirements/coupon-redemption.md", "openapi/coupon-api.json"],
    "input_sha256": "hash-of-input-files",
    "model": "configured-model",
    "prompt_version": "coupon-redemption-v1"
  },
  "test_points": [
    {
      "id": "coupon-redemption-minimum",
      "requirement": "订单金额低于最低消费时不能核销"
    }
  ],
  "cases": [
    {
      "case_id": "below_minimum_spend",
      "title": "低于最低消费时保持已领取状态",
      "scenario_type": "coupon_redemption",
      "priority": "P1",
      "category": "boundary",
      "covers": ["coupon-redemption-minimum"],
      "setup": {
        "minimum_order_amount_cents": 10000,
        "discount_amount_cents": 2000
      },
      "input": {
        "order_amount_cents": 9999
      },
      "expected": {
        "status_code": 409,
        "coupon_status": "claimed",
        "applied_discount_cents": null,
        "payable_amount_cents": null
      },
      "reason": "订单金额未达到最低消费，失败后优惠券仍应保持已领取状态"
    }
  ]
}
```

Pydantic 模型由此定义并输出 JSON Schema。生成结果至少校验必填项、严格类型、已知场景和枚举、唯一 `case_id`、测试点引用完整、金额范围及预期状态的一致性。`case_id` 限定为短小的字母、数字和下划线标识，避免把任意文本当成测试 ID。来源元数据保存输入文件路径及内容摘要、模型名称和提示词版本，便于追踪生成依据。规范化后完全相同的案例拒绝重复；近似案例只作为审核提示。模型生成的解释、来源和测试点用于审核；转换器只把经批准案例映射为现有数据文件要求的字段。

## 校验、审核和执行边界

候选用例默认处于待审核状态，保存在 `artifacts/ai_testgen/pending/`，该目录不在 `tests/` 下。审核者检查需求来源、测试点覆盖、输入数据、预期状态和模型列出的假设，然后显式批准选中的案例。批准命令再次执行全部校验、检查与现有人工/AI 案例重复，并生成版本控制下的可执行 JSON。人工审查 Git diff 后运行 Pytest；候选文件本身从不执行。

执行端不使用 `eval`、不导入生成代码、不从数据中解析 Python，也不允许模型指定任意 URL、方法、fixture 或 SQL。已批准数据只进入固定 `coupon_redemption` 测试函数，由现有 API 客户端与 fixtures 管理真实请求和数据清理。模型输出错误、Schema 不符或业务含糊时，生成/批准命令明确失败，不降级为任意执行。

## 密钥、隐私和错误处理

`LLM_API_KEY` 只从进程环境读取，错误信息、日志和候选文件都不得包含密钥。生成器设置请求超时；网络错误、限流、服务端错误和无效 JSON 提供清晰诊断，有限重试，不在 pytest 中自动重试生成。只有用户显式运行生成命令才产生模型调用和费用。需求或 API 文档会发送给所配置的外部模型服务，运行前必须移除真实用户数据、凭据和不应外传的信息。报告与测试数据继续使用合成数据，避免在公开 Pages 报告中泄露内容。

## 分阶段开发与验收

### 阶段 1：Schema 和离线校验

实现版本化 Pydantic 模型、核销场景语义检查、来源映射和精确去重。验收标准：有效样例可通过；缺字段、错类型、重复 ID、未支持场景、金额/预期矛盾和悬空测试点被拒绝；所有测试均不访问模型 API。

### 阶段 2：直接 LLM API 和生成命令

实现环境变量配置、OpenAI-compatible HTTP 客户端、版本化提示词和本地需求/OpenAPI 输入。生成测试点、候选 JSON 和来源元数据。验收标准：mock HTTP 测试覆盖成功、超时、API 错误及非法输出；密钥不进入日志和文件；只调用显式命令会访问模型 API。

### 阶段 3：候选审核与批准转换

实现候选校验、选择案例和转换为现有核销数据格式的命令。验收标准：未批准案例不能转换；批准案例在转换前重复校验；重复案例被拒绝；候选文件不会被 pytest 收集。

### 阶段 4：接入现有 Pytest 和报告

让现有核销参数化测试读取人工维护数据与已批准 AI 数据，保持现有 API 客户端、fixtures、断言、清理和 Allure 流程。验收标准：基线回归用例仍可运行；每个批准案例形成独立、可识别的 pytest 项；单案例和全部 regression 命令均可运行；CI 不要求 LLM 密钥。

### 阶段 5：生成质量评测

建立固定需求样例和审核基准，记录 Schema 通过率、需求到测试点/案例的覆盖、重复率、人工接受或修改比例及批准用例的 CI 执行结果。验收标准：每个高优先级需求可追踪到测试点和用例，评测数据可重复运行并区分模型输出质量与被测 API 的通过/失败结果。变异测试可在该阶段之后作为独立增强项。

## 设计确认后的推进顺序

先完成阶段 1 的 Schema/离线校验，再实现 LLM 调用、人工批准转换和 Pytest 接入，最后建设质量评测。每个阶段单独验证并向用户解释关键设计选择；在设计文档审阅通过前不开始实现。
