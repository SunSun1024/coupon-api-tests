# AI 测试用例生成模块实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to execute this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有优惠券接口测试仓库中实现一个独立的 AI 测试用例生成工具：从需求、业务规则、约束和可选 OpenAPI 输入生成有来源追踪的结构化候选用例，完成质量校验并分开保存原始输出和通过校验的结果。

**Architecture:** 新增 `ai_testgen` Python 包。Pydantic v2 模型定义模型输出契约；OpenAI Python SDK 的 Chat Completions 客户端处理结构化输出、JSON 降级、超时、有限重试和 token usage；生成器负责上下文、prompt 版本、输入摘要和两类文件持久化；独立校验器负责跨字段业务校验、重复识别、来源追踪及可选 OpenAPI 路径和方法验证。CLI 只显式触发生成，不接入 pytest 的执行或收集。

**Tech Stack:** Python 3.14、Pydantic v2、OpenAI Python SDK（兼容 Chat Completions）、标准库 `argparse`/`json`/`hashlib`/`logging`/`pathlib`/`datetime`、pytest。

## 文件变更范围

- Modify: `requirements.txt`（增加 OpenAI Python SDK）
- Modify: `.gitignore`（忽略本地生成产物）
- Modify: `README.md`（安装、环境变量、CLI 示例及设计说明）
- Create: `ai_testgen/__init__.py`
- Create: `ai_testgen/__main__.py`
- Create: `ai_testgen/config.py`
- Create: `ai_testgen/models.py`
- Create: `ai_testgen/llm_client.py`
- Create: `ai_testgen/generator.py`
- Create: `ai_testgen/validator.py`
- Create: `ai_testgen/cli.py`
- Create: `examples/ai_testgen/coupon-redemption.md`
- Create: `examples/ai_testgen/coupon-openapi.json`
- Create: `tests/test_ai_testgen_models.py`
- Create: `tests/test_ai_testgen_validator.py`
- Create: `tests/test_ai_testgen_llm_client.py`
- Create: `tests/test_ai_testgen_generator.py`
- Create: `tests/test_ai_testgen_cli.py`

## Task 1: Pydantic 结构化输出模型

**Files:** `ai_testgen/models.py`, `tests/test_ai_testgen_models.py`

- [x] 先写测试：合法 TestSuite、必填字段、严格类型、枚举、步骤/预期结果、case ID 和额外字段拒绝。
- [x] 运行模型测试，确认因模块/模型尚不存在而失败。
- [x] 实现 `TestStep`、`TestCase`、`TestSuite`、来源引用、token usage、生成元数据和校验报告。
- [x] 重跑模型测试并确认通过。

## Task 2: 用例质量校验器

**Files:** `ai_testgen/validator.py`, `tests/test_ai_testgen_validator.py`

- [x] 先写测试：步骤连续且有预期结果、来源引用有效、case ID 唯一、精确重复拒绝、OpenAPI method/path 合法性、缺少 OpenAPI 或有未验证预期时生成警告。
- [x] 确认测试在实现前失败。
- [x] 实现 OpenAPI operation 索引、标准化重复指纹和验证报告；结构/业务问题通过明确异常报告，不包含模型原文。
- [x] 重跑校验器测试并确认通过。

## Task 3: 环境配置与 LLM Client

**Files:** `ai_testgen/config.py`, `ai_testgen/llm_client.py`, `requirements.txt`, `tests/test_ai_testgen_llm_client.py`

- [x] 先写测试：必需 env、超时/重试配置、native structured output、JSON object fallback、非法 JSON、Schema 错误、服务异常分类、usage 记录、敏感信息不进入日志。
- [x] 确认 Mock 测试在实现前失败。
- [x] 使用 `OpenAI` SDK 注入式 client；配置支持 native/auto/JSON-object/prompt 输出模式；只对明确不支持原生 schema 的服务错误降级，避免吞掉鉴权或网络错误。
- [x] 映射模型调用失败、JSON 解码失败和输出 Schema 错误为不同异常；错误日志只记录安全的错误类型/状态码和 token 数，不记录请求、响应、密钥或原始异常文本。
- [x] 重跑 LLM Client 测试并确认通过。

## Task 4: 生成编排、校验与双路持久化

**Files:** `ai_testgen/generator.py`, `tests/test_ai_testgen_generator.py`

- [x] 先写 Mock 生成测试：需求和规则来源 ID、Prompt 版本、输入 SHA-256、模型和 UTC 时间、token usage、原始与校验结果分开写入；失败只写 raw；不触网。
- [x] 确认测试在实现前失败。
- [x] 实现 `GenerationRequest`、可追踪 source catalog、版本化 prompt、generation ID 和原子 JSON 文件写入。
- [x] 校验通过后才写 validated artifact；校验存在可理解警告时保存警告和待澄清问题。
- [x] 重跑生成器测试并确认通过。

## Task 5: 最小 CLI、示例与完整验证

**Files:** `ai_testgen/cli.py`, `ai_testgen/__main__.py`, `examples/ai_testgen/*`, `tests/test_ai_testgen_cli.py`, `README.md`, `.gitignore`

- [x] 先写 CLI 测试：`--help`、读取本地文本/OpenAPI JSON、缺少配置的安全提示及生成器调用 Mock。
- [x] 确认测试在实现前失败。
- [x] 提供 `python -m ai_testgen generate ...` 示例；需要用户明确设置 LLM 环境变量时才产生模型 API 请求。
- [x] 更新 README，说明实际 Python/Pydantic/LLM SDK 用法、输出目录、错误类别、安全注意事项和面试考点；忽略 `artifacts/ai_testgen/`。
- [x] 运行所有新增 AI 模块测试（33 passed）和全项目测试收集（71 collected）；未执行需要本地 API/外网的既有集成测试，确认本模块未接入接口测试执行流程。

## 完成标准

- 所有 LLM 请求可由 Mock 完整覆盖，测试/CI 不需要 LLM 凭据或联网。
- 输出结构经过 Pydantic 类型检查与独立业务检查；不完整需求通过问题/未验证预期显式呈现。
- OpenAPI 存在时检查 API method/path；没有时不假装已经验证。
- 原始模型输出与已校验测试集单独落盘，保存 prompt/model/time/requirement version/token usage/source catalog 元数据。
- API Key、需求正文、原始响应不进入日志；CLI 只输出安全状态与 artifact 路径。
- 本次不实现自动执行、RAG、UI、多 Agent、pytest 参数化接入或审核批准流程。


## 执行结果

- 本机无法解析 PyPI 域名，因此 OpenAI SDK 未能在本环境实际安装；依赖已加入 `requirements.txt`，LLM API 路径由 Mock 覆盖。
- 未运行要求 Uvicorn 服务或外网的既有集成测试；本次 AI 模块测试不依赖这些服务。
