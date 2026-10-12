# 优惠券接口自动化框架

## 当前结构

- `tests/support/coupon_api_client.py` 集中管理请求地址、超时时间和优惠券接口调用。测试仍然直接检查响应状态码和业务字段。
- `api_client` fixture 把 API 地址、Requests 会话和数据清理登记器组合起来。每个测试都会得到自己的客户端和清理登记器。
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

## GitHub Actions

`.github/workflows/coupon-api-tests.yml` 会在 push 和 pull request 时启动本地 FastAPI 服务，用独立 SQLite 数据库运行 `regression` 用例，生成 Allure 报告，并将报告作为名为 `coupon-api-allure-report` 的 workflow artifact 上传。打开对应的 Actions 运行记录，在 Artifacts 区域下载并解压即可查看 `index.html`。

## 调试用例

```bash
python -m pytest -q tests/test_coupon_flow.py::test_failed_redemption_keeps_coupon_claimed -vv
python -m pytest --collect-only -q -m regression tests
```
