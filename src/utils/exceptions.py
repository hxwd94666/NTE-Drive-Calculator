# 定义项目通用异常类型。
"""Project-specific exception types."""

class NTEBaseError(Exception):
    pass


class OCRParseError(NTEBaseError):
    pass


class InventoryEmptyError(NTEBaseError):
    pass


class ConfigMissingError(NTEBaseError):
    pass
