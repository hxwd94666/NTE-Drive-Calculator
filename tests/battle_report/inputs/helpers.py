# 构建不依赖暂挂测试模块的合成首击尝试身份。
def attempt(sequence="1", revision="1"):
    return {"attemptId": sequence, "firstChanges": {d: {"revision": revision, "dirty": False}
            for d in ("character", "inventory", "team", "environment")}, "changes": []}
