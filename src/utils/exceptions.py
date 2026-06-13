"""Project-specific exception types."""

class NTEBaseError(Exception):
    pass


class OCRParseError(NTEBaseError):
    pass


class InventoryEmptyError(NTEBaseError):
    pass


class ConfigMissingError(NTEBaseError):
    pass


class AllocationFailedError(NTEBaseError):
    pass
