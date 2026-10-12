# Scheduled Regression and Browser-Hosted Allure Report

## Context

The existing GitHub Actions workflow runs the regression suite on pushes and pull requests. It generates a browsable Allure HTML report and uploads that directory as a downloadable workflow artifact. The artifact is a ZIP download, so its contents cannot be explored directly in the Actions page.

The repository is public and the report contains generated test data. Publishing the report through GitHub Pages will make it publicly accessible.

## Goals

- Run the regression suite once each day at 09:00 China Standard Time.
- Keep the existing push and pull request regression checks.
- Let users open the latest Allure report directly in a browser.
- Keep the downloadable Actions artifact for each run.
- Publish reports only from the default branch, never from pull requests.
- Preserve a useful report when tests fail after producing Allure results.

## Design

Add a scheduled trigger at `01:00 UTC` (`09:00` in Asia/Shanghai), plus `workflow_dispatch` for manual runs. GitHub Actions scheduled workflows use UTC and may start later during periods of high load.

Keep testing on `push` and `pull_request`. Generate the Allure HTML directory after the test command, including when tests fail, and upload it as the current per-run artifact. For runs on the default branch triggered by push, schedule, or manual dispatch, publish that same HTML directory to GitHub Pages using the official Pages artifact and deploy actions. Pull request runs must not publish. Each successful publication replaces the Pages site with the latest report; retaining historical trend data is outside this change.

Grant Pages deployment permissions only to the publishing job. The published site will be public because this repository is public. The repository owner must select **Settings → Pages → Build and deployment → GitHub Actions** once if Pages has not already been configured. The deployment URL will appear in the workflow's `github-pages` environment and deployment step.

## Failure behavior

If tests fail after Allure results exist, still generate, archive, and publish the failure report so the user can inspect failed cases and request/response attachments. If the API cannot start and no Allure results are produced, skip report publishing and leave the last published report available. A cancelled run must not start a deployment.

## Documentation

Update the README and testing framework guide to explain the daily schedule, manual trigger, public report URL, Pages setup, and artifact download. Clarify that the existing artifact already contains HTML but must be downloaded and extracted, while Pages opens the report directly.

## Validation

- Validate the edited workflow YAML and run the relevant local test suite.
- Check the workflow conditions so pull requests cannot publish and report deployment can proceed after test failures only when an HTML index exists.
- After the workflow is pushed and Pages is enabled, verify a fresh scheduled or manual run, its regression result, the downloadable report artifact, and the live Pages URL.
