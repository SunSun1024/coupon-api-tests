"""支持通过 `python -m ai_testgen` 启动 CLI。"""

from ai_testgen.cli import main


if __name__ == "__main__":
    # 将 CLI 的退出码交给操作系统，方便脚本和 CI 根据成功/失败判断结果。
    raise SystemExit(main())
