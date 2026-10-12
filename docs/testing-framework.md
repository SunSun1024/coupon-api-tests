# 优惠券接口自动化框架

## 当前结构

- `tests/support/coupon_api_client.py` 集中管理请求地址、超时时间和优惠券接口调用。测试仍然直接检查响应状态码和业务字段。
- `api_client` fixture 把 API 地址、Requests 会话和数据清理登记器组合起来。每个测试都会得到自己的客户端和清理登记器。
- `base_url` 和 `api_ready` 使用 session 作用域；第一次创建 API 客户端前会检查服务的 `/docs`。服务未启动时，Pytest 会提前停止并显示启动命令，避免每个接口用例重复报告连接错误。
- 创建优惠券、领取成功时，客户端登记服务端返回的 ID。测试结束后，清理 fixture 先删除本测试创建的领取记录，再删除优惠券；没有登记的旧数据会保留。
- `COUPON_DB_PATH` 用来让 API 服务和测试清理逻辑指向同一个 SQLite 文件。GitHub Actions 使用 runner 临时目录里的独立数据库，避免碰到开发者本地数据。

## 本地生成 Allure 报告

先在一个终端启动 API：

```bash
source .venv/bin/activate
python -m uvicorn coupon_api.main:app --reload
```

在另一个终端安装 Python 依赖并运行测试：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q -m regression tests --alluredir=allure-results --clean-alluredir
```

Allure HTML 报告需要单独的 Allure CLI。安装 Node.js 后运行：

```bash
npm install --global allure
allure generate allure-results -o allure-report
allure open allure-report
```

`allure-results` 是测试运行生成的原始结果，`allure-report` 是可浏览的 HTML 报告。两者都已加入 `.gitignore`。只想看已有的 HTML 报告时，也可以直接在浏览器打开 `allure-report/index.html`。

使用 `api_client` 的测试如果在执行阶段失败，Allure 的用例详情会显示按请求顺序命名的 JSON 附件，例如 `API 1: POST /coupons`。附件记录请求方法、路径和 JSON 请求体，以及响应状态码和正文；发生连接异常时记录异常类型和消息。成功用例、setup 阶段失败和 teardown 阶段失败都不会附加这些 HTTP 详情。为避免泄露凭据，请求头和 Cookie 不会被记录。

过期边界测试通过接口创建优惠券，再将数据库中的过期时间改为过去，从而模拟时间经过；领取与核销动作仍通过 HTTP API 执行。最后一张库存的并发测试使用独立临时 SQLite 数据库，并在真实路由函数访问库存更新语句前同步多个线程，稳定复现并发竞争；它验证的是路由业务逻辑和数据库库存保护，不经过 HTTP 传输层。

## GitHub Actions

`.github/workflows/coupon-api-tests.yml` 会在 push 和 pull request 时启动本地 FastAPI 服务，用独立 SQLite 数据库运行 `regression` 用例，生成 Allure 报告，并将报告作为名为 `coupon-api-allure-report` 的 workflow artifact 上传。打开对应的 Actions 运行记录，在 Artifacts 区域下载并解压即可查看 `index.html`。

## 调试用例

```bash
python -m pytest -q tests/test_coupon_flow.py::test_failed_redemption_keeps_coupon_claimed -vv
python -m pytest --collect-only -q -m regression tests
```
