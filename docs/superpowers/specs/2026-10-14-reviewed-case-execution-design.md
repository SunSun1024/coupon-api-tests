# 已审核 AI 用例接入 Pytest：设计草案

状态：用户已确认，已实现并完成本地离线回归（2026-10-15）。验证证据及 TCP 验证限制见对应实施计划。

## 目标与已有实现

执行链路：AI 生成 → 原有 Schema/业务校验 → 人工审核业务用例及执行映射 → Adapter 转换 → Pytest 参数化执行 → JSON 结果及现有 Allure/HTML 报告。

已检查的复用点：

- `tests/support/coupon_api_client.py`：Requests Session、超时、请求响应 exchanges、成功创建/领取后的清理登记。
- `tests/conftest.py`：function 级客户端与清理 fixture、session 级 base_url/就绪检查、失败请求的 Allure 附件。
- `tests/support/response_schemas.py`：四类成功响应的 Pydantic 模型。
- `tests/test_coupon_redemption_data.py`：加载 JSON、使用 case_id 参数化、通过 API 准备独立数据及查询状态。
- `.github/workflows/coupon-api-tests.yml`：本机临时 SQLite、启动 Uvicorn、回归测试与 Allure HTML。
- `ai_testgen/models.py`：自然语言 TestCase/TestStep，不能直接作为 HTTP 执行指令。

当前缺口：客户端可以使用任意 base_url；Requests 默认允许重定向；原用例没有机器可执行的请求体、参数引用和断言值。直接从自然语言推断执行含义会产生不可靠请求。

## 选择的方案

独立 `ai_testexec` 包实现严格数据模型、人工审核记录、Case Adapter、目标策略、执行器、结果记录及 CLI。执行器注入现有 API Client，不从测试目录导入业务实现。

第一阶段采用人工填写的显式执行映射。Adapter 不进行自然语言关键词匹配、代码生成或额外 LLM 推断。没有映射的用例输出 `needs_manual_mapping`，说明缺少的字段；不伪造可执行步骤。

候选替代方案：让 LLM 生成第二份结构化执行计划，再人工审核。它可以减少填写工作，但增加模型调用与语义错误风险，暂不采用。

## 输入与输出契约

### 自然语言用例

保持现有 TestCase/TestSuite 契约。读取 validated 产物后重新检查其结构和引用，不将文件位于 validated 目录视为可信证明。记录源 run_id、case_id、源用例摘要及执行输入的规范化 JSON 摘要。

### 执行映射

映射单独存储，不写进自然语言 action/expected_result 文本。包括版本、源用例标识、目标环境名称、运行时数据声明和有序操作。

只允许以下操作类型，使用 Pydantic 判别联合与 extra=forbid：

1. `http_request`：唯一 step_id、已登记 operation_id、method、路径模板、path/query 参数、JSON 请求体及响应字段提取声明。
2. `assert_status`：引用之前的请求 step_id，预期 HTTP 状态码。
3. `assert_json`：引用之前的请求 step_id、JSON Pointer、严格相等的预期 JSON 值。

首期仅开放优惠券创建、领取、核销和查询四个接口。接口登记表必须与本地 OpenAPI 的 Method/path/operation_id 一致。未知操作拒绝转换。

变量采用结构化引用对象，不执行字符串表达式。可引用之前 HTTP 响应的已声明字段。测试 UUID、订单 ID、未来时间来自固定的可信 fixture 工厂；不允许任意函数名、模块名、环境变量名或脚本。

同一 case 内引用同一个订单变量才能验证不可叠加。不同 case 的变量上下文完全独立。

### 审核记录

人工在检查业务用例和执行映射后通过显式审核命令产生独立记录，包括 reviewer、UTC 时间、源用例摘要、执行映射摘要和 OpenAPI 摘要。生成模型的输出字段不能作为审核记录。

审核覆盖具体内容，不能只批准 case_id。用例、映射或契约发生变化，原审核失效。Adapter 转换前和执行器执行前均核对审核与摘要。执行器还校验确定性转换得到的执行计划，不能信任可编辑文件中的 approved 布尔值。

本机审核文件用于防止误执行，不构成身份认证或防恶意本机用户篡改的审批系统。可信代码、配置和审核记录由项目维护者管理；本阶段不建设签名或远程权限系统。

### 转换结果

每条转换结果为 ready、needs_manual_mapping 或 conversion_failed，并附机器可读原因。ready 用例包含 execution_case_id、源 run_id/case_id、内容摘要、审核摘要和完整执行数据。没有 ready 标记不能执行；标记本身也不能替代再次校验。

## 校验与请求边界

检查顺序：

1. 本地文件及 Pydantic 结构校验。
2. 源用例与审核记录核对。
3. 操作白名单、接口登记表、本地 OpenAPI 契约校验。
4. 重复、变量依赖、断言引用和 JSON Pointer 校验。
5. 可信环境和目标地址检查。
6. 生成每个 HTTP 请求前解析变量，再对实际参数、请求体重新做 Schema 校验。
7. 通过所有请求检查后才允许发出 HTTP 请求。

请求 Schema 使用 Pydantic 与标准库实现的有限离线校验器，支持当前示例契约使用的本地 components 引用、required、严格类型、范围、枚举、const、oneOf/anyOf/allOf、数组约束、date-time 及 additionalProperties。此实现不是完整的 JSON Schema 标准实现；未支持的关键字（例如 pattern、dependentRequired）、其他 format、递归引用、认证和 header/cookie 参数均显式拒绝。拒绝外部引用，不下载 URL。无法验证的 Schema 能力不能跳过校验。本机无法访问 PyPI，不新增 jsonschema 依赖。

Schema 不合法的请求数据属于转换/执行输入错误。本阶段不提供绕过请求校验来发送畸形参数的模式；正常业务拒绝如重复核销的参数本身符合 Schema，因此可测试。

执行数据只能指定环境名称，不能定义主机或端口。默认环境只允许 `http://127.0.0.1:8000` 的 Coupon 测试服务。新增环境必须修改维护者控制的策略；已有 COUPON_API_URL 不能绕过策略。

请求只接受相对路径模板和经过验证的单个路径参数；拒绝绝对 URL、协议相对 URL、路径穿越、分隔符注入、查询串或 fragment 混入路径。禁止模型提供任意 headers、代理、Cookie 或认证配置。

Requests 禁用重定向及环境代理；所有请求有有界超时，写请求不自动重试。目标检查必须发生在 api_ready 的 /docs 检查之前，防止就绪检查先访问非法目标。

本阶段复用本地 SQLite 数据清理，因此执行环境必须与清理数据库相匹配。远程环境需要可靠清理适配器后才开放。

## 客户端、Fixture 与 Pytest 集成

为 CouponApiClient 增加受控的公开请求入口，复用 exchanges 记录与已知操作的资源登记，不让执行器调用私有函数或重复实现日志。原有手写测试方法保持兼容。

执行器接收客户端接口、环境策略、契约和单 case 运行时上下文。执行逻辑是固定类型分派，不使用 eval、exec、动态导入或模型提供的 Python。

新增 Pytest JSON 数据入口，将每个 ready case 参数化，ID 包含源 case_id 与执行标识。加载和审核失败在网络 fixture 启动前报出；每个 case 使用 function 级 api_client 和 cleanup。

用例结束时保持现有资源清理；失败用例也执行清理。清理只处理本次成功创建并登记的资源，不能由模型传任意数据库语句或待删除 ID。

复用现有 pytest_runtest_makereport 与 api_client.exchanges 附件，并增加用例关联信息、失败步骤和结果 JSON 的 Allure 附件。无需另写独立报告平台。

## 错误和关联记录

转换报告记录 case_id、字段位置、错误分类和原因，不伪装成测试通过或测试跳过。

执行结果记录 execution_run_id、generation_run_id、source_case_id、execution_case_id、输入及审核摘要、开始/结束时间、每步结果及对应 request_id。审核拒绝也保留可观察的输入摘要和标识，以 trace_verified=false 标注未经核对，记录值不能反过来作为授权。

分类：

- conversion_failed：缺失字段、未知接口/操作、非法 Method、Schema 错误、重复映射。
- review_required/review_invalid：未审核或内容变化。
- execution_error：连接/超时、响应解析或变量提取失败、运行时请求数据校验失败。
- assertion_failed：明确记录预期值、实际值和对应响应 step_id。
- passed：所有操作、断言和必要清理流程正常完成。

请求超时不能被写成 HTTP 状态码；断言失败也不能被统一归为请求异常。报告保留已有敏感信息处理边界，不把密钥或认证头写入附件。

重复识别至少包括同一源内容与 case_id 重复，以及忽略展示 ID 后执行内容完全一致。动态 UUID 不参与使相同计划逃避重复检查。自然语言语义近似不保证自动识别。

## 验收测试矩阵

| 场景 | 预期证据 |
| --- | --- |
| 正常生成并执行 | Mock 生成、原有校验、显式审核、转换、参数化执行及结果关联完整；另在本地 Coupon API 验证不可叠加 |
| 缺少必填字段 | 转换失败，字段路径明确，HTTP 调用次数为 0 |
| 不存在的 API 路径 | 契约/登记表拒绝，HTTP 调用次数为 0 |
| 非法 HTTP Method | 数据模型或接口组合校验拒绝，HTTP 调用次数为 0 |
| 不符合 Schema 的参数 | 静态转换或变量解析后校验拒绝；错误请求不发送 |
| 不支持的操作 | 判别联合拒绝，HTTP 调用次数为 0 |
| 断言不符合预期 | assertion_failed，保留步骤、实际/预期和 exchanges |
| 目标接口超时 | execution_error，超时原因明确，不重试写操作 |
| 相同用例重复生成 | 重复源/规范化执行内容被识别，不重复执行 |
| 未审核用例执行 | 转换和执行两层均拒绝，包含 api_ready 在内的 HTTP 次数为 0 |
| 审核后篡改 | 源、映射、OpenAPI 或执行计划变更被识别 |
| 外部目标/重定向/路径注入 | 首次非法请求及重定向后续请求均不发生 |
| 独立和可重复执行 | 连续运行两次，用户/订单不同，资源正确清理 |

离线单元测试不调用真实 LLM。完整模块验证包括适配器、执行器、Pytest 子进程结果与报告附件；真实本地 HTTP 集成使用隔离 SQLite 数据库，避免污染现有测试记录。

## 计划文件边界

- `ai_testexec/models.py`：执行映射、审核、转换和结果模型。
- `ai_testexec/adapter.py`：确定性转换、来源验证与重复检查。
- `ai_testexec/review.py`：审核生成与内容绑定。
- `ai_testexec/contracts.py`：接口登记表和本地 Schema 校验。
- `ai_testexec/policy.py`：环境、目标、路径和网络选项检查。
- `ai_testexec/executor.py`：固定操作分派、变量上下文与结果记录。
- `ai_testexec/cli.py` 和 `__main__.py`：预览、审核、转换命令。
- `tests/test_reviewed_ai_cases.py`：现有 fixture 下的参数化执行入口。
- `tests/test_ai_testexec_*.py`：上述验收矩阵。
- `tests/conftest.py`、`tests/support/coupon_api_client.py`：最小兼容扩展。
- `examples/ai_testexec/`：不可叠加的自然语言源及未审核执行映射示例。
- `README.md`、`requirements.txt`：使用教程及必要依赖。

本阶段不实现 RAG、UI、多 Agent、自然语言任意自动翻译、任意接口或任意代码执行。
