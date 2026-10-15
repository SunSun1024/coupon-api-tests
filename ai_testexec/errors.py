class GateError(ValueError):
    """执行前门槛失败；code 用于报告分类，而非把所有错误当成 HTTP 失败。"""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
