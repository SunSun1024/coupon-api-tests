"""维护者控制的目标策略；模型数据只能选择环境名称，不能提供目标主机。"""

import re
from urllib.parse import urlsplit

from ai_testexec.errors import GateError

BASE_URL = "http://127.0.0.1:8000"


def validate_target(base_url, environment="local-coupon"):
    # 首期只支持与本地 SQLite 清理 fixture 匹配的服务，不允许 DNS/任意端口。
    if environment != "local-coupon" or base_url.rstrip("/") != BASE_URL:
        raise GateError("target_forbidden", "只允许 local-coupon 的 http://127.0.0.1:8000")
    parsed = urlsplit(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise GateError("target_forbidden", "测试地址不能带认证、路径、查询或fragment")


def path_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) or ".." in value:
        raise GateError("contract_invalid", "路径参数必须是安全的单个标识符")
    return value


def build_path(template, parameters):
    names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template)
    if set(names) != parameters.keys():
        raise GateError("contract_invalid", "路径参数与模板不一致")
    result = template
    for name in names:
        result = result.replace("{" + name + "}", path_value(parameters[name]))
    if not result.startswith("/") or result.startswith("//") or any(c in result for c in "?#%\\"):
        raise GateError("target_forbidden", "请求路径非法")
    return result


def validate_concrete_path(template, path):
    pieces, position = [], 0
    for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
        pieces.append(re.escape(template[position:match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        position = match.end()
    pieces.append(re.escape(template[position:]))
    matched = re.fullmatch("".join(pieces), path)
    if not matched or build_path(template, matched.groupdict()) != path:
        raise GateError("target_forbidden", "实际请求路径不符合登记模板")
