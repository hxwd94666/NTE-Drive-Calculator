# 等待完整库存的新观测，允许同内容快照复用身份而不伪造新快照。
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from time import monotonic


@dataclass(frozen=True)
class InventoryObservation:
    last_snapshot_id: int
    cursor: int
    items: tuple[dict, ...] = ()
    characters: tuple[dict, ...] = ()


def record_inventory_observation(service, payload):
    # 这里只复制已通过完整性、稳定性及原生修订校验的原始观察，不读取指令接受后的 UI 投影。
    # 同内容只推进观测游标；每次返回独立副本，不伪造数据库新快照。
    if payload.get("complete") is not True:
        return
    rows = tuple(
        {**deepcopy(item), "uid_slot": item["uid"]["slot"], "uid_serial": item["uid"]["serial"]}
        for item in payload["items"]
    )
    with service._state_condition:
        snapshot_id = service._state.last_snapshot_id
        if snapshot_id is not None:
            service._inventory_observation_cursor += 1
            service._inventory_observation = InventoryObservation(
                snapshot_id,
                service._inventory_observation_cursor,
                rows,
                tuple(deepcopy(payload.get("characters", ()))),
            )
            service._state_condition.notify_all()


def wait_for_inventory_observation(service, *, after_cursor: int, timeout: float):
    deadline = monotonic() + timeout
    with service._state_condition:
        while True:
            state = service._state
            observation = service._inventory_observation
            if observation is not None and observation.cursor > after_cursor:
                return deepcopy(observation)
            if state.stop_reason == "connection_lost" or (state.phase == "error" and not state.running):
                raise RuntimeError("原生库存连接已停止，自动补救已结束")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("等待新的完整库存观测超时")
            service._state_condition.wait(remaining)


def wait_for_snapshot(service, *, after_snapshot_id: int | None, timeout: float):
    deadline = monotonic() + timeout
    with service._state_condition:
        while True:
            state = service._state
            snapshot_id = state.last_snapshot_id
            if snapshot_id is not None and (after_snapshot_id is None or snapshot_id > after_snapshot_id):
                return state
            if state.phase == "error" and not state.running:
                raise RuntimeError(state.error or state.message)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("等待新的稳定背包快照超时")
            service._state_condition.wait(remaining)
