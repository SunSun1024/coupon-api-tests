"""跨生成运行去重。记录转换过的程序，不阻止同一已审核用例重复回归。"""

import sqlite3
from pathlib import Path

from ai_testexec.errors import GateError


class CaseRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS cases (fingerprint TEXT PRIMARY KEY, generation_run_id TEXT NOT NULL, case_id TEXT NOT NULL)")

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return {row[0]: (row[1], row[2]) for row in db.execute("SELECT fingerprint, generation_run_id, case_id FROM cases")}

    def record(self, cases):
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            for case in cases:
                current = db.execute("SELECT generation_run_id, case_id FROM cases WHERE fingerprint = ?", (case.fingerprint,)).fetchone()
                identity = case.generation_run_id, case.source_case_id
                if current is not None and tuple(current) != identity:
                    raise GateError("duplicate_generated", "相同执行内容已由另一个生成用例登记")
                db.execute("INSERT OR IGNORE INTO cases VALUES (?, ?, ?)", (case.fingerprint, *identity))
