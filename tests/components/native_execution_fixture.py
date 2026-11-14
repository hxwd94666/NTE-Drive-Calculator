# 提供执行输入与输出的合成证据，验证传输和展示不改写原始值。
def execution_evidence() -> dict:
    def array(items, *, declared=None, status=None):
        return {"declaredCount": len(items) if declared is None else declared,
                "capturedCount": len(items), "status": status or ("ok" if items else "empty"),
                "items": items}

    attribute = {"nameUtf16": array([72, 80]), "fieldPath": array([[42, 0]]),
                 "fieldOwner": [91, 12], "owner": None}
    return {
        "schemaVersion": 1, "executionId": "9007199254740993",
        "generation": "18446744073709551615",
        "identity": {"ge": [10, 2], "sourceAsc": [11, 3], "targetAsc": [12, 4]},
        "evaluationCoverage": "not_observed",
        "nameResolution": "identity_only",
        "before": {
            "observedUnixUs": "1788800000000010", "status": "partial",
            "specModifiers": array([{"bits": 1161863168, "value": 3082.0, "status": "ok"},
                                    {"bits": 2143289344, "value": None, "status": "invalid_value"}]),
            "sourceAttributes": array([{"attribute": attribute, "captureSource": 0, "snapshot": True}],
                                      declared=9, status="size_limit"),
            "targetAttributes": array([]), "sourceCaptureComplete": False,
            "targetCaptureComplete": True, "hasNonSnapshotAttributes": None,
            "tags": {key: {"tags": array([[100, 0]]), "parents": array([])} for key in
                     ("sourceActor", "sourceSpec", "sourceScoped", "targetActor", "targetSpec", "targetScoped")},
        },
        "after": {
            "observedUnixUs": "1788800000000090", "status": "ok", "outputFlags": 0,
            "outputModifiers": array([{"attribute": attribute, "operation": 3,
                                       "magnitude": {"bits": 1161863168, "value": 3082.0, "status": "ok"},
                                       "handle": -1, "valid": True}]),
        },
    }
