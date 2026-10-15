# AI 测试用例生成评测系统设计

状态：设计待用户确认；文中的优惠券风险清单是待审核候选，尚不是 Golden Dataset。确认设计与确认黄金风险点是两个独立动作。

## 1. 目标与方案选择

评估生成用例的结构、业务有效性、风险覆盖、重复与可执行性。结果应有可追溯证据，能在不再调用模型的情况下重新计算。暂不建设 UI、RAG、LLM 裁判、自动执行编排。

采用独立 `ai_testeval` 包：版本化黄金数据 → 固定实验配置 → 采集生成证据 → 自动检查及人工审核 → 离线评测 → 模型/Prompt 对比。

方案比较：

| 方案 | 优点 | 局限 |
| --- | --- | --- |
| 文件数据集 + 原始输出快照 + 确定性评测 + 人工语义审核（采用） | 无新服务依赖，证据可检查，能复算 | 业务语义与风险覆盖需要人工审核 |
| 自动规则与关键词直接给覆盖分 | 成本低 | 提及“重复核销”不等于真正测试该风险，容易虚高 |
| 增加 LLM 裁判或向量匹配 | 可以辅助大规模筛选 | 仍需要人工标定，增加成本、评测波动及另一套偏差，首版不引入 |

技术使用现有 Python、Pydantic v2 和 pytest，以及标准库 argparse、json、hashlib、pathlib、datetime。优先复用现有生成器、校验器与 Adapter，不引入数据库或外部评测平台。

## 2. 当前代码事实与接入边界

- `ai_testgen.generator.TestCaseGenerator` 会拆批，并保留各批次的原文；只读取 validated 会遗漏 JSON/Schema/业务校验失败。
- 纯超时等全部没有原文的失败目前可能没有 raw 文件。新采集器必须在运行样本前建立记录，失败后也完成归档。
- `GenerationMetadata` 已有模型名、Prompt/需求版本、输入摘要和 token 统计，但没有精确请求快照及完整配置。
- `generate_structured()` 是逻辑调用。SDK 重试与 native→JSON 降级可能发生在其内部，现有 llm_call_count 不等于 HTTP 请求数。
- `ai_testexec.SourceArtifact` 使用严格字段定义，执行审核绑定内容摘要。因此评测元数据放在独立文件，不直接往现有 validated 对象添加字段。
- `CaseAdapter.adapt()` 可重新验证审核、映射和契约并返回 ready。它不能判断自然语言用例的业务预期正确，也不能证明映射语义正确。

第一版正式采集固定 `max_retries=0`，显式选择 json_object/prompt，禁止 auto 隐式降级。这样可以在本地解析前保留响应原文；现有 native SDK 解析异常可能丢失原文，暂不用于正式基准采集。保留生成器现有的截断拆批，并记录每次尝试。已有普通生成命令行为保持兼容。

## 3. 数据结构

所有结构使用 Pydantic 严格类型和 extra=forbid；版本、内容摘要及引用关系必须校验。

### GoldenDataset 与 GoldenSample

`GoldenDataset`：schema_version、dataset_id、dataset_version、description、samples。

每个 `GoldenSample` 包含：

- sample_id、requirement_id、requirement_version、完整 requirements 文本。
- business_rules：有稳定 rule_id 的规则文本及出处。
- api_constraints：接口约束及 OpenAPI 内容快照。
- generation_focus：可选固定覆盖重点；它是实验输入的一部分。
- risks：人工维护的 risk_id、说明、严重程度、source_refs、判定依据。
- expected_scenarios：scenario_id、positive/negative/boundary 等分类、对应 risk_ids、准备条件、操作和关键断言。正常、异常、边界分别明确，不能只列关键词。
- rubric_version：人工审核判定标准版本。

数据集内的所有 ID 唯一，所有引用必须存在；需求/API/风险/场景变化都改变摘要，正式发布时提升 dataset_version。

`DatasetApproval` 单独保存 dataset_sha256、reviewer、reviewed_at、decision=approved。只由使用者检查数据集后显式执行批准命令创建。模型输出与文件中的自述“已审核”字段不能替代批准记录。

开发时可以提供 draft 模板、规则引用及明确标为 synthetic 的单元测试数据，但不代替用户批准真实黄金风险点。正式采集和正式评分要求有效的批准记录。草稿只能做结构预览，不能显示正式风险覆盖分。

### ExperimentConfig 与 RunManifest

`ExperimentConfig`：experiment_id、provider、requested_model、declared_model_version、dataset_sha256、选定 sample_ids、repeats、生成数量与分批大小、输出模式、token 上限、超时、重试策略。

温度/seed 等只有实际发送时才记录为已配置值；未发送就标为 provider_default，不伪造确定参数。模型服务没有返回真实版本时，reported_model_version 保持 unknown，保留用户声明版本及模型别名。

`RunManifest` 包含：

- run_id、UTC 时间、评测协议版本、数据集及批准记录摘要。
- 实验配置快照、生成器 Prompt 版本、代码 revision + dirty 状态、相关源码摘要、Python/依赖版本。
- 每个计划运行的 `(sample_id, repetition)`；同一重复编号只能有一个样本结果。
- 每个样本的状态与产物路径、sha256；失败和中断也占一个计划槽位，不能静默删除。

Prompt 版本由实际生成器记录；同时保存每次请求的完整 messages 和 response schema 及摘要。比较历史版本时读取对应运行快照，不能仅改一个版本标签就声称测试了新 Prompt。

### SampleRun、AttemptRecord 与候选用例

`SampleRun`：sample_id、repetition、生成 run_id、input_sha256、complete/failed/interrupted、错误阶段和类型、attempts、候选索引、raw/validated 快照。

`AttemptRecord`：attempt_id、请求参数、精确 messages、response schema、请求/结束时间、原始 content、raw_available、token_usage、错误类别、finish_reason（服务可提供时）、响应模型版本信息（服务可提供时）。

通过 RecordingClient 包装现有 `generate_structured()`，捕获成功及异常。它只记录生成所需材料，不向被评测模型发送黄金风险、预期场景或人工审核结论。若现有 SDK 在解析失败时没有提供原文，显式记录 unavailable，不能重构或伪造原始响应。

每条候选使用 `(sample_id, repetition, attempt_id, array_index)` 的内部键和原始内容摘要，不依赖模型 case_id 唯一。原始 case_id 保留用于展示和执行映射。

候选总数 N 的口径：对非截断、可完整解析 JSON 且含 test_cases 数组的实际批次，逐元素入账，包括错误类型、字段缺失和重复 ID 元素；无合法数组时不能猜测生成数量。截断后被放弃的父批次仅进入尝试统计，不把它再次加入最终候选集合。失败前已得到的批次仍保留。不得为了提高分数先删除无效/重复项。

每次尝试记录 included_in_candidate_cohort 和纳入/排除原因；复算时重新校验该决定，不信任可编辑标记。被排除的原文仍完整保留。

### HumanReview

审核文件绑定 run_sha256、dataset_sha256、rubric_version 和每条候选的 content_sha256。内容变化时旧审核失效。

- validity：valid / invalid / pending；有效/无效结论必须有 reviewer、时间及理由。
- risk_matches：候选键、risk_id、covered / not_covered / pending、证据字段位置、说明。
- risk_audits：risk_id、completed / pending、审核理由；completed 绑定整个候选集合摘要。只有人工完成该风险的检查且没有有效覆盖时，才标记为确定遗漏。
- semantic_duplicates：候选键集合、代表用例键、审核理由；仅同一样本同一次重复运行内合并。

有效性标准：前置条件可成立、操作与目标一致、预期有需求/API 依据、关键步骤可验证，不含关键业务臆造。结构或明确契约错误的候选不能被人工有效结论覆盖，冲突会报审核错误。

覆盖结论必须给出能定位到候选 action/expected_result 等字段的证据，逐项核对黄金判定依据。来源 ID 相同、关键词相同、标题相似只允许产生 pending 候选关联，不能自动记 covered。

未填写的人工审核默认为 pending。审核工具生成空白审核模板，不自动填写 valid、covered 或批准人。

### EvaluationReport

包括 evaluator_version、输入摘要、评测时间、各指标分子/分母/value、统计口径、审核完成度、逐样本和逐候选明细、失败类别、风险状态、重复分组、执行转换原因及 Prompt 改进建议。

value 使用 0～1；Markdown 展示为百分比。分母为 0 时 value=null，并显示“不适用/尚无可审核数据”，不显示 0% 或 100%。

## 4. 五项核心指标与分母

记 S 为计划的样本×重复次数，A 为实际逻辑调用尝试数，N 为上述可枚举候选元素数，H 为人工 valid+invalid 数量。

| 指标 | 分子 / 分母 | 来源与解释 |
| --- | --- | --- |
| Schema Validity Rate（主指标） | 最终完整产出且符合 TestSuite 严格结构的样本槽位 / S | 自动判断；超时、解析失败、结构失败、空套件和未完成样本不能从分母消失；业务校验失败不等同 Schema 失败 |
| Attempt Schema Validity（诊断） | 符合本次响应 Schema 且未截断的逻辑尝试 / A | 自动判断；包括超时/空回复/截断等失败；不能称为 HTTP 成功率 |
| Valid Case Rate | 人工 valid 数 / H | 人工结论；同时强制展示 H/N 审核完成度，以及 valid/N 的“已确认有效产出比例”，后者是当前已确认的下界 |
| Risk Coverage / Recall | 有有效用例且有人工证据确认 covered 的黄金风险点数 / 该样本的黄金风险点数 | 人工语义结论；同一个风险无论有多少覆盖用例只计一次；跨样本/重复时对风险槽位分别计数 |
| Duplicate Rate | 每个重复等价组中除一个代表项以外的候选数之和 / N | 确定性内容重复与人工语义重复分别展示，合并后去重计数；改 case_id/title 不逃避内容去重；跨不同实验/重复运行不互相去重 |
| Executable Rate | 经现有校验、审核、CaseAdapter 确认 ready 的候选数 / N | 自动重验实际源文件、映射、审核及契约；不读取一个自述 ready 布尔值作为证据；无需发送 HTTP |

有效性暂未审核时，Valid Case Rate=null。风险有明确未覆盖结论时列为 missed；仍有可能关联的 pending 用例时列为 pending_review；生成失败且没有候选时列为 missed_generation_failed。报告区分“确定遗漏”和“尚未确认覆盖”，不把待审核语义断言为确定遗漏。

对 N=0 的失败样本，病例层比例不适用，但 S、风险分母和失败案例仍计入。空输出不会因为不存在用例而得到好分。

自动重复检测采用可解释的规范化内容指纹（忽略展示 ID 和标题、规范化空白），自动只声称“规则检测到的重复”。近义改写需要人工语义分组；报告注明该指标不保证发现所有语义重复。人工分组合并为等价类，拒绝跨样本引用及无效代表项，避免重复计数。

可执行性报告必须展示 missing_mapping、review_required、unverifiable_expectations、contract_invalid、source_invalid 等原因，并同时显示提供映射数/N。Adapter 若在整个源套件层失败，原因传播到受影响候选，不能缩小分母。Executable Rate 反映当前审核映射流程的可执行程度，也受人工映射完成度影响，不能单独用来排名模型的业务能力。

Pytest 执行通过率不进入上述公式。业务正确性审核和执行映射审核分开记录；有执行审核不能自动推断用例业务有效。

聚合提供 micro（分子/分母求和）和 per-sample 明细；风险另提供按样本平均的 macro。每次重复运行独立计算风险覆盖，再聚合，不能把多次输出并集当成一次运行覆盖率。

手算验收例：N=10，人工 valid=3、invalid=1、pending=6，黄金风险=5、确认覆盖=2、重复冗余项=2、ready=2。结果为 Valid Case Rate=3/4=75%，审核完成度=4/10=40%，已确认有效产出=3/10=30%，Risk Recall=2/5=40%，Duplicate=2/10=20%，Executable=2/10=20%。

## 5. 离线评测、重新计算与模型比较

1. 使用者维护黄金数据、检查结构，审核确认后产生独立批准记录。
2. 冻结实验输入与配置，为每个样本建立槽位。运行现有生成器，完整记录原文和请求。一个样本失败仍继续采集后续样本。
3. 通过纯本地解析/校验收集候选及自动判断，输出待审核文件。解析失败保留原文和字段路径，不以修复后的内容替代原始内容。
4. 人工填写业务有效性、风险关联和语义重复结论；需要可执行性时提供现有 mapping/review 文件。
5. `evaluate` 从快照读取、检查全部摘要，重新计算自动结果，结合人工审核生成 JSON 和 Markdown。
6. 补充审核后再次 evaluate 产生新 report_id，保留旧报告和审核版本。原始快照不覆盖。
7. `compare` 在同样的测试集、sample/repeat 集合、数量预算、分批与输出策略、评测协议/审核标准下对比模型/Prompt 版本。差异列明确显示模型、Prompt 内容摘要、调用参数和人工审核进度。

比较必须拒绝同名不同内容的数据集、缺失样本、重复样本键、不同评测协议/审核标准或不兼容预算。每个报告绑定实际输入，不能通过手工修改报告里的百分比参与比较；compare 读取运行及审核快照并重新计算。

审核未完成时仍可生成并排的 provisional 报告，展示待审核量；不宣称谁更优。不生成混合五项的单一“正确率”。模型别名更新等不可固定因素明确展示；保证同一份快照能复算，不保证重新请求供应商获得逐字相同输出。

历史 raw 文件没有完整参数/请求快照，不能直接作为同条件正式实验。第一版使用新采集器建立完整基准；保留原有产物，不伪造历史缺失参数。历史产物导入工具不在本次范围。

## 6. CLI 与报告内容

拟提供以下命令（本文件是设计，命令在实现完成后才可使用）：

```text
python -m ai_testeval dataset-check --dataset <draft.json>
python -m ai_testeval dataset-approve --dataset <golden.json> --reviewer <name> --output <approval.json>
python -m ai_testeval run --dataset <golden.json> --approval <approval.json> --config <experiment.json> --output-dir artifacts/ai_testeval
python -m ai_testeval review-template --run <run.json> --output <review.json>
python -m ai_testeval evaluate --run <run.json> --review <review.json> --output-dir artifacts/ai_testeval/reports
python -m ai_testeval compare --runs <run-a.json> <run-b.json> --reviews <review-a.json> <review-b.json> --output-dir artifacts/ai_testeval/comparisons
```

`evaluate` 可不提供人工审核或执行映射，报告必须明确 pending/缺映射。可选 `--execution-evidence <evidence.json>` 清单按 sample/repetition 对应 source、mapping、review、OpenAPI 快照；evaluate 将其冻结到评测输入。compare 用同样的已归档审核/执行证据复算两份运行。读取评测、查看帮助、生成审核模板均不要求 API Key。

报告内容：五项核心指标及附加诊断、分子分母、自动/人工来源、失败样本/候选及字段位置、确定遗漏/待审核风险、重复等价组及证据、无效用例理由、可执行性失败原因、token 与耗时、版本信息及输入摘要。

Prompt 改进建议采用确定性规则：字段类型错误→强调 Schema/简短字段示例；截断→控制每批规模；来源不明→要求引用明确规则；经人工确认的边界遗漏→建议补充边界覆盖指引；重复→强化不同前置条件与断言目标；信息不足→完善需求。只在报告提出建议，不自动改 Prompt 或黄金标准，不将黄金答案偷偷加入下一次基准请求。

模型 Key、认证头和带凭据 URL 不进入快照。需求和输出可能包含敏感内容，产物使用现有受限文件权限与原子写入方式，并加入 gitignore。错误日志显示类别/位置，详细原文仅保存在本地快照。

## 7. 拟修改文件与模块职责

| 文件 | 职责 |
| --- | --- |
| 新增 `ai_testeval/models.py` | 数据集、批准、实验、采集、审核及报告的严格模型 |
| 新增 `ai_testeval/dataset.py` | 黄金数据引用校验、内容摘要、人工批准绑定 |
| 新增 `ai_testeval/recording.py` | 包装模型调用，记录真实输入、结果和失败 |
| 新增 `ai_testeval/runner.py` | 按固定样本/重复顺序运行现有生成器，保证失败槽位留档 |
| 新增 `ai_testeval/evaluator.py` | 原文重解析、候选索引、指标分子分母及证据汇总 |
| 新增 `ai_testeval/reviews.py` | 审核模板、内容绑定、人工结论及证据引用检查 |
| 新增 `ai_testeval/executability.py` | 复用 CaseAdapter，重验执行证据，不发 HTTP |
| 新增 `ai_testeval/reports.py` | JSON/Markdown 和可比性检查、模型比较、改进建议 |
| 新增 `ai_testeval/io.py` | 产物快照、sha256 校验、原子存储 |
| 新增 `ai_testeval/cli.py`、`__init__.py`、`__main__.py` | CLI 与离线入口 |
| 视需要小改 `ai_testgen/llm_client.py` | 通过可选字段暴露服务返回模型版本和 finish_reason；保持旧调用兼容 |
| 视需要小改 `ai_testgen/validator.py` | 公开现有重复指纹函数，避免两套重复规则漂移 |
| 新增 `examples/ai_testeval/` | draft 数据、实验配置、待填写审核示例；无冒充用户批准的记录 |
| 新增 `tests/test_ai_testeval_*.py` | 各模块离线单元测试与 Mock 端到端验证 |
| 修改 `.gitignore`、`README.md`，新增 `docs/ai-test-evaluation.md` | 忽略评测产物、友好的操作步骤、指标解释和限制 |

不重写现有 README；只追加新模块说明并保留已有用户注释。实施在当前工作目录进行，不覆盖已有修改，不提交或推送未获确认的内容。

## 8. 分阶段实现与验收

1. 数据集与审核契约：测试重复 ID、未知引用、缺失关键风险/场景、未批准/已修改数据集被拒绝；单元测试的批准人为明确测试身份。
2. 采集与存储：Mock 成功、纯超时、空回复、非法 JSON、部分字段错误、截断拆批；每个计划样本都有记录；精确 Prompt/参数及原文可恢复，Key 不出现，黄金风险不发送给模型。
3. 评测与人工审核：用上面的手算例验证分子分母；覆盖 null 分母、部分审核、语义 pending、有效性与自动检查冲突、错误 ID/摘要、重复分组交叉、不同 repeat 不合并。
4. 执行性：实际调用已有 Adapter 的校验路径，覆盖 ready、无映射、未审核、契约错误、套件级失败和篡改；所有用例 HTTP 调用数为 0。
5. 比较与 CLI：两份不同模型/Prompt 的 Mock 快照可对比；不同测试集内容、预算、缺样本不能冒充同条件；未完成审核显式 provisional；报告覆盖所有要求的栏目。
6. 复算与兼容：同一快照离线重复计算，除 report_id/时间外数值与明细一致；现有生成与执行模块回归不受影响；从 CLI 完成离线 Mock 示例，不依赖真实 API 或费用。

## 9. 优惠券黄金风险候选清单：请用户审核

以下只整理已经写入 `examples/ai_testgen/coupon-redemption.md` 和 `coupon-openapi.json` 的规则，用作一个完整核销需求样本的待审核清单。评测器不会让待评测模型补写标准；用户可修改、增删并最终确认这些风险。

| 候选 ID | 类型 | 待确认的关键风险与判定依据 |
| --- | --- | --- |
| R-01 | 正常 | 已领取且未过期的满100减20券，订单12000分：抵扣2000分、实付10000分，状态 redeemed，查询与核销响应一致 |
| R-02 | 边界 | 订单恰好10000分：允许核销，抵扣2000分、实付8000分 |
| R-03 | 边界/异常 | 订单9999分：409，detail=order amount is below coupon minimum，券仍为 claimed |
| R-04 | 异常 | 同一订单第一张券成功后，第二张券核销409，detail=order already has a redeemed coupon；第一张核销记录不变，第二张 claimed |
| R-05 | 异常 | 同一用户重复领取同一 coupon_id：409，detail=user already claimed coupon |
| R-06 | 异常 | 同一已核销用户券再次核销：409，detail=coupon is already redeemed，不产生第二次成功核销 |
| R-07 | 边界 | now < expires_at 有效；now = expires_at 及之后核销409，detail=coupon has expired。到期由创建方设置为创建后7天，不随领取重新计时 |
| R-08 | 异常/权限 | 用户B核销用户A领取的券：404，detail=claimed coupon not found；A的券不应被核销 |

R-07 的精确时间边界是业务有效性要求，是否可在当前执行映射中冻结时钟另行判断；它不能因为缺乏当前自动化能力而从黄金风险中删除。风险确认后再落为正式版本化数据，并由用户创建批准记录。
