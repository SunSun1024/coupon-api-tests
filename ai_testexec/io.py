"""规范化摘要和原子 JSON 写入；摘要绑定内容，不是用户身份认证。"""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel


def json_data(value):
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def digest(value):
    encoded = json.dumps(json_data(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse(model, value):
    # JSON 边界允许日期字符串转为 datetime；其余严格类型由 Pydantic 控制。
    if isinstance(value, model):
        value = value.model_dump(mode="json")
    return model.model_validate_json(json.dumps(value, ensure_ascii=False, allow_nan=False))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            json.dump(json_data(value), file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def strict_equal(left, right):
    # Python 的 True == 1，JSON 字段断言需要保留 bool 与 number 的区别。
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(strict_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right
