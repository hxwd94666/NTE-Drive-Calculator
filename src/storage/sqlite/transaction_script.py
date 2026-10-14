# 在调用方持有的事务内执行发布迁移语句，不隐式提交或接管连接状态。
from __future__ import annotations

import sqlite3


def execute_transaction_script(
    connection: sqlite3.Connection, script: str, *, incomplete_error: Exception,
) -> None:
    """沿既有逐行完整语句规则执行；错误类型和事务收尾仍归对应 DAO。"""
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("迁移 SQL 需要调用方先开启事务")
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        raise incomplete_error
