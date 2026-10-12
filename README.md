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
- 通过 GitHub Actions 在 push 和 pull request 时运行回归测试并保存 Allure 报告。

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

`.github/workflows/coupon-api-tests.yml` 在 push 和 pull request 时自动启动 FastAPI 服务，使用独立 SQLite 数据库运行回归测试，并生成 Allure 报告。打开 GitHub 仓库的 **Actions** 页面，选择对应运行记录，可以在 Artifacts 区域下载 `coupon-api-allure-report`。

## 补充文档

关于 fixture、数据清理和 CI 配置的更多说明，请阅读 [`docs/testing-framework.md`](docs/testing-framework.md)。
