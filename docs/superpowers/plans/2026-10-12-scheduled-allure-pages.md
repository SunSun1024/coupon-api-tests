# Scheduled Regression and Browser-Hosted Allure Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run coupon regression tests daily at 09:00 Asia/Shanghai and publish the latest Allure HTML report to a browser-accessible GitHub Pages URL.

**Architecture:** Extend the existing workflow with `schedule` and `workflow_dispatch`. Keep test and report generation in the regression job, preserve its ZIP artifact, and use a separate, narrowly permissioned Pages job to download and publish the report only from `main`; update both project guides with the schedule and report links.

**Tech Stack:** GitHub Actions, Allure CLI, GitHub Pages, Markdown, pytest.

## Global Constraints

- Scheduled trigger: `0 1 * * *` UTC, corresponding to 09:00 Asia/Shanghai.
- Keep `push` and `pull_request` regression runs.
- Pull requests and non-default branches must not publish to GitHub Pages.
- Keep the per-run `coupon-api-allure-report` artifact.
- Publish only when `allure-report/index.html` exists; preserve the previously published site when no report is generated.
- Grant `pages: write` and `id-token: write` only to the Pages publishing job.
- The Pages site and report are public because the repository is public.

---

## File Structure

- `.github/workflows/coupon-api-tests.yml`: schedule, manual trigger, report-generation output, artifact handoff, conditional Pages deployment, and workflow summary link.
- `README.md`: user-facing schedule, manual run steps, public URL, and one-time Pages setting.
- `docs/testing-framework.md`: detailed explanation of artifact versus browser-hosted HTML and deployment behavior.

### Task 1: Add daily execution and conditional Pages publishing

**Files:**
- Modify: `.github/workflows/coupon-api-tests.yml`

**Interfaces:**
- The `regression` job exposes `report_generated` as a boolean-like string output (`true` or `false`).
- The `publish-report` job consumes the `coupon-api-allure-report` artifact and publishes it to the `github-pages` environment.

Replace the workflow file with this complete definition, retaining the existing API startup and test invocation:

```yaml
name: Coupon API tests

on:
  push:
  pull_request:
  schedule:
    - cron: "0 1 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  regression:
    runs-on: ubuntu-latest
    outputs:
      report_generated: ${{ steps.generate_allure_report.outputs.report_generated }}
    steps:
      - name: Check out repository
        uses: actions/checkout@v7

      - name: Set up Python
        uses: actions/setup-python@v7
        with:
          python-version: "3.14"
          cache: pip

      - name: Set up Node.js for Allure CLI
        uses: actions/setup-node@v7
        with:
          node-version: "22"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt
          npm install --global allure

      - name: Start API and run regression tests
        env:
          COUPON_DB_PATH: ${{ runner.temp }}/coupon-api-tests.db
        run: |
          python -m uvicorn coupon_api.main:app --host 127.0.0.1 --port 8000 > "$RUNNER_TEMP/coupon-api.log" 2>&1 &
          server_pid=$!
          trap 'kill "$server_pid" 2>/dev/null || true' EXIT
          python - <<'PY'
          import time
          import requests

          for _ in range(40):
              try:
                  response = requests.get("http://127.0.0.1:8000/docs", timeout=1)
                  if response.status_code == 200:
                      break
              except requests.RequestException:
                  time.sleep(0.5)
          else:
              raise SystemExit("API did not become ready; inspect coupon-api.log")
          PY
          python -m pytest -q -m regression tests \
            --alluredir=allure-results --clean-alluredir

      - name: Generate Allure HTML report
        id: generate_allure_report
        if: always() && !cancelled()
        run: |
          if [ ! -d allure-results ]; then
            echo "report_generated=false" >> "$GITHUB_OUTPUT"
            echo "No Allure results were produced."
            exit 0
          fi

          allure generate allure-results -o allure-report
          echo "report_generated=true" >> "$GITHUB_OUTPUT"

      - name: Upload Allure report
        if: always() && steps.generate_allure_report.outputs.report_generated == 'true'
        uses: actions/upload-artifact@v7
        with:
          name: coupon-api-allure-report
          path: allure-report/
          if-no-files-found: error

  publish-report:
    needs: regression
    if: >-
      always() &&
      !cancelled() &&
      needs.regression.result != 'cancelled' &&
      needs.regression.outputs.report_generated == 'true' &&
      github.event_name != 'pull_request' &&
      github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Download Allure HTML report
        uses: actions/download-artifact@v8
        with:
          name: coupon-api-allure-report
          path: allure-report

      - name: Configure GitHub Pages
        uses: actions/configure-pages@v5

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v4
        with:
          path: allure-report

      - name: Deploy Allure report
        id: deployment
        uses: actions/deploy-pages@v4

      - name: Add report link to workflow summary
        env:
          REPORT_URL: ${{ steps.deployment.outputs.page_url }}
        run: |
          echo "## Allure report" >> "$GITHUB_STEP_SUMMARY"
          echo "[Open the latest Allure report]($REPORT_URL)" >> "$GITHUB_STEP_SUMMARY"
```

- [x] Add `schedule` with `cron: "0 1 * * *"` and `workflow_dispatch`; keep `push` and `pull_request`.
- [x] Run the existing pytest command unchanged. In an `always()` report-generation step, write `report_generated=true` to `$GITHUB_OUTPUT` only after `allure generate` succeeds; write `false` if `allure-results/` does not exist.
- [x] Keep uploading `allure-report/` as `coupon-api-allure-report` when the generated `index.html` exists.
- [x] Add `publish-report` with `needs: regression` and a condition requiring a non-cancelled run, a generated report, a non-PR event, and `github.ref == 'refs/heads/main'`.
- [x] Give `publish-report` only the `contents: read`, `pages: write`, and `id-token: write` permissions it needs. Download the report artifact, run `actions/configure-pages@v5`, upload it with `actions/upload-pages-artifact@v4`, then deploy with `actions/deploy-pages@v4`.
- [x] Set the job environment URL from `steps.deployment.outputs.page_url` and add the URL to `$GITHUB_STEP_SUMMARY` so each published run has an obvious report link.
- [x] Parse the workflow YAML and inspect conditions before running the API regression suite.

### Task 2: Explain the schedule and report access

**Files:**
- Modify: `README.md`
- Modify: `docs/testing-framework.md`

**Interfaces:**
- The README links to `https://sunsun1024.github.io/coupon-api-tests/` as the latest published report.
- The docs tell users how to manually trigger a workflow and configure Pages once.

Replace the README's current GitHub Actions paragraph with:

```markdown
## GitHub Actions

`.github/workflows/coupon-api-tests.yml` 在 push 和 pull request 时运行回归测试，并每天北京时间 09:00 定时巡检（GitHub Actions 使用 UTC，计划时间是 01:00 UTC；高负载时可能延迟）。在仓库的 **Actions → Coupon API tests → Run workflow** 中也可以手动触发。

最新 Allure 报告可以直接在浏览器打开：[查看 Coupon API Allure 报告](https://sunsun1024.github.io/coupon-api-tests/)。仓库是公开的，因此报告也公开可访问。首次使用前，请到 **Settings → Pages → Build and deployment**，将来源设为 **GitHub Actions**。

Actions 运行记录仍会保留 `coupon-api-allure-report` artifact。它已经是 HTML 报告，但需要下载并解压后打开 `index.html`；上面的 Pages 链接会直接打开最新报告。每次默认分支发布都会替换 Pages 上的旧报告。Pull request 会运行测试并保存 artifact，但不会发布网页报告。
```

Replace the GitHub Actions paragraph in `docs/testing-framework.md` with:

```markdown
## GitHub Actions

`.github/workflows/coupon-api-tests.yml` 会在 push 和 pull request 时运行 `regression` 用例，并每天北京时间 09:00 运行一次；GitHub Actions 的 cron 使用 UTC (`0 1 * * *`)，高负载时可能延迟。也可以在 **Actions → Coupon API tests → Run workflow** 手动运行。

每次有 Allure 结果时，workflow 都会生成 HTML 并上传 `coupon-api-allure-report` artifact。测试失败但已产生 Allure 结果时，失败详情仍会生成并发布；没有结果时会保留上一份网页报告。只有 `main` 分支上的 push、定时或手动运行会部署 GitHub Pages，pull request 不会覆盖它。Pages 部署会替换上一次的报告，不保留历史趋势。

仓库公开，所以 Pages 报告公开可访问。首次发布前，在 **Settings → Pages → Build and deployment** 选择 **GitHub Actions**。最新报告地址为 <https://sunsun1024.github.io/coupon-api-tests/>，Actions 运行摘要中也会显示直达链接。
```

- [x] In README's GitHub Actions section, document push/PR checks, daily 09:00 China time, the Actions → Coupon API tests → Run workflow manual trigger, and the direct public Allure URL.
- [x] Explain that repository Settings → Pages → Build and deployment must use GitHub Actions if Pages has not been enabled.
- [x] Explain that the ZIP artifact is still HTML but requires download/extraction, whereas Pages opens the Allure `index.html` directly; each deployment replaces the prior Pages report.
- [x] Add matching implementation detail to `docs/testing-framework.md`, including that PR runs never publish and failures with generated results still publish diagnostics.
- [x] Check Markdown links and `git diff --check`.

### Task 3: Validate and hand off the first live deployment

**Files:**
- Verify: `.github/workflows/coupon-api-tests.yml`, `README.md`, `docs/testing-framework.md`

**Interfaces:**
- A scheduled or manually triggered `main` run should show regression results, retain the `coupon-api-allure-report` artifact, and expose a direct Pages link.

- [x] Run a YAML syntax/structure check on the workflow.
- [x] Run `git diff --check` and verify the Pages publish condition excludes pull requests and all refs other than `refs/heads/main`.
- [ ] Run the regression suite against the local FastAPI service and confirm it passes. The current sandbox denies local socket bind/connect (`errno 1: Operation not permitted`); 13 network-free regression tests pass, and full HTTP regression remains to be verified in Actions after push.
- [x] Verify the final diff has no whitespace errors and confirm there are no uncommitted changes after the implementation commit.
- [ ] After the user pushes the workflow and enables Pages in repository settings, inspect the first live run for test results, artifact upload, deployment URL, and browser-accessible `index.html`.
