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

`.github/workflows/coupon-api-tests.yml` 会在 push 和 pull request 时启动本地 FastAPI 服务运行 `regression` 用例，并每天北京时间 09:00 运行一次；GitHub Actions 的 cron 使用 UTC（`0 1 * * *`），高负载时可能延迟。也可以在 **Actions → Coupon API tests → Run workflow** 手动运行。

每次有 Allure 结果时，workflow 都会生成 HTML 并上传 `coupon-api-allure-report` artifact。测试失败但已产生 Allure 结果时，失败详情仍会生成并发布；没有结果时会保留上一份网页报告。只有 `main` 分支上的 push、定时或手动运行会部署 GitHub Pages，pull request 不会覆盖它。Pages 部署会替换上一次的报告，不保留历史趋势。

仓库公开，所以 Pages 报告公开可访问。首次发布前，在 **Settings → Pages → Build and deployment** 选择 **GitHub Actions**。最新报告地址为 [https://sunsun1024.github.io/coupon-api-tests/](https://sunsun1024.github.io/coupon-api-tests/)，Actions 运行摘要中也会显示直达链接。artifact 仍可从对应运行记录的 Artifacts 区域下载；解压后，直接打开 `index.html` 即可。

## 调试用例

```bash
python -m pytest -q tests/test_coupon_flow.py::test_failed_redemption_keeps_coupon_claimed -vv
python -m pytest --collect-only -q -m regression tests
```
