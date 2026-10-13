# 在冻结装配任务内执行有界指令重试、释放读取锁并等待完整快照恢复。
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError
from contextlib import contextmanager, ExitStack
from threading import Event
from time import monotonic, sleep
from typing import Any

from src.integrations.nte_core_protocol import NteCoreRpcError, is_mods_plugin_busy_error
from src.integrations.operation_guard import require_operation
from src.observability.context import OperationContext
from src.observability.operation import log_event


SHORT_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)
ROLE_RECOVERY_SECONDS = 15.0
TASK_RECOVERY_SECONDS = 30.0
SNAPSHOT_RECOVERY_SECONDS = 5.0
MAX_SYNC_RECOVERIES = 2


def is_pre_dispatch_rejection(error: BaseException) -> bool:
    return is_mods_plugin_busy_error(error) or (
        isinstance(error, NteCoreRpcError)
        and error.code == -32001
        and error.message == "source_changed"
        and error.domain_code is None
    )


class RecoveryStopped(RuntimeError):
    def __init__(self, reason: str, *, step: str = "", module_index: int = 0):
        self.reason = reason
        self.step = step
        self.module_index = module_index
        super().__init__(f"装配自动补救已停止：{reason}")


class EquipmentApplyRecovery:
    def __init__(
        self,
        sync_service,
        user_dao,
        *,
        snapshot_id: int,
        inventory_uids: frozenset[tuple[int, int]],
        operation_guard=None,
        cancel_event: Event | None = None,
        check_current: Callable[[], None] | None = None,
        progress: Callable[[str], None] | None = None,
        operation_context: OperationContext | None = None,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], Any] | None = None,
    ):
        self.sync = sync_service
        self.dao = user_dao
        self.inventory_uids = inventory_uids
        self.observed_snapshot_id = snapshot_id
        self.observed_items: list[dict] | None = None
        self.operation_guard = operation_guard
        self.cancel_event = cancel_event or Event()
        self.check_current = check_current or (lambda: None)
        self.progress = progress or (lambda _message: None)
        self.context = operation_context or OperationContext.create("equipment_apply")
        self.clock = clock
        self.sleeper = sleeper or self.cancel_event.wait
        self.task_spent = 0.0
        self.role_spent = 0.0
        self.sync_recoveries = 0
        self.total_sync_recoveries = 0
        self.short_retry_count = 0
        self.last_dispatch_observation_cursor = None
        self.plan_id = 0
        self.character_id = 0
        self.character_uid: dict[str, int] = {}
        self.identity = None
        self._identity_frozen = False
        self._stack: ExitStack | None = None
        self._role_budgets: dict[tuple, tuple[float, int]] = {}
        self._role_key: tuple | None = None

    def check(self):
        self.check_current()
        require_operation(self.operation_guard, "native_equipment")
        if self.cancel_event.is_set():
            raise CancelledError("装配任务已取消，已下发操作保持原状")

    def begin_role(self, plan_id: int, character_id: int, character_uid: dict[str, int]):
        self._role_key = (plan_id, character_id, character_uid["slot"], character_uid["serial"])
        self.role_spent, self.sync_recoveries = self._role_budgets.get(self._role_key, (0.0, 0))
        self.plan_id = plan_id
        self.character_id = character_id
        self.character_uid = dict(character_uid)
        self.observed_items = None

    @property
    def batch_active(self):
        return self._stack is not None

    def check_source(self):
        if self.identity is None:
            self.check()
        else:
            self._check_identity(timeout=2.0, charge=False)

    def _identity(self, *, timeout: float = 2.0):
        reader = getattr(self.sync, "equipment_identity", None)
        return reader(timeout=timeout) if callable(reader) else None

    def _check_identity(self, *, timeout: float | None = None, charge: bool = True):
        self.check()
        budget = self._remaining() if timeout is None else timeout
        if budget <= 0:
            raise RecoveryStopped("recovery_budget_exhausted")
        started = self.clock()
        try:
            current = self._identity(timeout=min(2.0, budget))
        finally:
            if charge:
                self._charge(self.clock() - started)
        if current is None:
            raise RecoveryStopped("source_identity_unavailable")
        if current != self.identity:
            raise RecoveryStopped("source_identity_changed")
        self.check()

    def _open(self, *, timeout: float = 15.0):
        self.check()
        stack = ExitStack()
        try:
            opener = getattr(self.sync, "equipment_recovery_batch", None)
            stack.enter_context(
                opener(check_cancelled=self.check, timeout=timeout) if callable(opener) else self.sync.equipment_batch()
            )
            current = self._identity(timeout=min(2.0, timeout))
            if self._identity_frozen and current != self.identity:
                raise RecoveryStopped("source_identity_changed")
            self.identity = current
            self._identity_frozen = True
            self.check()
        except BaseException:
            stack.close()
            raise
        self._stack = stack

    def _close(self):
        if self._stack is not None:
            stack, self._stack = self._stack, None
            stack.close()

    @contextmanager
    def batch(self):
        self._open()
        try:
            yield self
        finally:
            self._close()

    def _remaining(self):
        return min(ROLE_RECOVERY_SECONDS - self.role_spent, TASK_RECOVERY_SECONDS - self.task_spent)

    def _charge(self, elapsed: float):
        elapsed = max(0.0, elapsed)
        self.role_spent += elapsed
        self.task_spent += elapsed
        self._role_budgets[self._role_key] = (self.role_spent, self.sync_recoveries)

    def _wait(self, delay: float, *, charge: bool):
        self.check()
        if charge and delay > self._remaining():
            raise RecoveryStopped("recovery_budget_exhausted")
        started = self.clock()
        remaining = delay
        while remaining > 1e-9:
            self.sleeper(min(remaining, 0.05))
            self.check()
            remaining = max(0.0, delay - (self.clock() - started))
        if charge:
            self._charge(self.clock() - started)

    def _event(self, event: str, **fields):
        fields = {key: value for key, value in fields.items() if value is not None}
        log_event(
            "INFO",
            "equipment_apply." + event,
            "装配自动补救状态",
            self.context,
            plan_id=self.plan_id,
            character_id=self.character_id,
            snapshot_id=self.observed_snapshot_id,
            recovery_duration_ms=round(self.task_spent * 1000, 3),
            **fields,
        )

    def _recover(self, *, step: str, module_index: int):
        self.check()
        if self.sync_recoveries >= MAX_SYNC_RECOVERIES or self._remaining() <= 0:
            raise RecoveryStopped("recovery_budget_exhausted", step=step, module_index=module_index)
        # 同步恢复前释放外层装配读取锁，避免持锁等待完整观测。
        self._check_identity()
        cursor_reader = getattr(self.sync, "inventory_observation_cursor", None)
        observation_waiter = getattr(self.sync, "wait_for_inventory_observation", None)
        observation_cursor = cursor_reader() if callable(cursor_reader) and callable(observation_waiter) else None
        self._close()
        self.sync_recoveries += 1
        self.total_sync_recoveries += 1
        self._role_budgets[self._role_key] = (self.role_spent, self.sync_recoveries)
        self.progress("正在等待背包同步，随后继续当前角色…")
        self._event("recovery_waiting", step=step, module_index=module_index, recovery_round=self.sync_recoveries)
        started = self.clock()
        deadline = started + min(SNAPSHOT_RECOVERY_SECONDS, self._remaining())
        try:
            while deadline - self.clock() > 1e-9:
                self._check_identity(timeout=max(0.0, deadline - self.clock()), charge=False)
                try:
                    wait_seconds = min(0.25, max(0.0, deadline - self.clock()))
                    state = (
                        observation_waiter(after_cursor=observation_cursor, timeout=wait_seconds)
                        if observation_cursor is not None
                        else self.sync.wait_for_snapshot(
                            after_snapshot_id=self.observed_snapshot_id, timeout=wait_seconds
                        )
                    )
                except TimeoutError:
                    self._wait(min(0.05, max(0.0, deadline - self.clock())), charge=False)
                    continue
                self.check()
                snapshot_id = state.last_snapshot_id
                if (
                    not isinstance(snapshot_id, int)
                    or snapshot_id < self.observed_snapshot_id
                    or (observation_cursor is None and snapshot_id == self.observed_snapshot_id)
                    or (observation_cursor is not None and state.cursor <= observation_cursor)
                ):
                    raise RecoveryStopped("snapshot_not_fresh")
                summary = self.dao.inventory_snapshot_summary(snapshot_id)
                if not summary or summary.get("source") != "nte_core" or not summary.get("complete"):
                    raise RecoveryStopped("snapshot_incomplete")
                rows = list(state.items) if hasattr(state, "items") else self.dao.list_inventory_items(snapshot_id)
                uids = frozenset((int(row["uid_slot"]), int(row["uid_serial"])) for row in rows)
                if uids != self.inventory_uids:
                    raise RecoveryStopped("inventory_changed")
                if hasattr(state, "characters"):
                    actor_confirmed = any(
                        row.get("character_id") == self.character_id and row.get("uid") == self.character_uid
                        for row in state.characters
                    )
                else:
                    actor_confirmed = any(
                        row.get("source") == "snapshot"
                        and row.get("last_seen_snapshot_id") == snapshot_id
                        and (row.get("uid_slot"), row.get("uid_serial"))
                        == (self.character_uid["slot"], self.character_uid["serial"])
                        for row in self.dao.list_character_instance_mappings(self.character_id)
                    )
                if not actor_confirmed:
                    raise RecoveryStopped("character_identity_unconfirmed")
                self._check_identity(timeout=max(0.0, deadline - self.clock()), charge=False)
                self.observed_snapshot_id = snapshot_id
                self.observed_items = rows
                if self.clock() >= deadline:
                    raise RecoveryStopped("snapshot_recovery_timeout")
                self._open(timeout=min(2.0, deadline - self.clock()))
                self._event("recovery_resumed", step=step, module_index=module_index)
                return
            raise RecoveryStopped("snapshot_recovery_timeout", step=step, module_index=module_index)
        finally:
            self._charge(self.clock() - started)

    def dispatch(
        self,
        command: Callable[[], Any],
        *,
        step: str,
        module_index: int = 0,
        settle_seconds: float = 0.0,
        confirmed: Callable[[list[dict]], bool] | None = None,
    ):
        try:
            return self._dispatch(
                command, step=step, module_index=module_index, settle_seconds=settle_seconds, confirmed=confirmed
            )
        except RecoveryStopped as error:
            error.step = step
            error.module_index = module_index
            raise

    def _dispatch(
        self,
        command: Callable[[], Any],
        *,
        step: str,
        module_index: int,
        settle_seconds: float,
        confirmed: Callable[[list[dict]], bool] | None,
    ):
        while True:
            for attempt in range(len(SHORT_DELAYS) + 1):
                self.check()
                try:
                    cursor_reader = getattr(self.sync, "inventory_observation_cursor", None)
                    if callable(cursor_reader):
                        self.last_dispatch_observation_cursor = cursor_reader()
                    result = command()
                except Exception as error:
                    if not is_pre_dispatch_rejection(error):
                        self._event(
                            "command_failed",
                            step=step,
                            module_index=module_index,
                            failure_kind=type(error).__name__,
                            error_code=getattr(error, "code", None),
                            domain_code=getattr(error, "domain_code", None),
                        )
                        raise
                    self._check_identity()
                    if attempt == len(SHORT_DELAYS):
                        break
                    self.short_retry_count += 1
                    self.progress(f"组件状态更新，正在自动重试当前装配指令（{attempt + 1}/5）…")
                    self._event(
                        "command_retry",
                        step=step,
                        module_index=module_index,
                        retry_number=attempt + 1,
                        error_code=getattr(error, "code", None),
                        source_identity_same=True,
                        dispatched=False,
                    )
                    self._wait(SHORT_DELAYS[attempt], charge=True)
                    continue
                # 已接受请求永不重放；缓冲期间取消只阻止后续操作，不撤销既有副作用。
                self._event("command_accepted", step=step, module_index=module_index)
                if settle_seconds:
                    self._wait(settle_seconds, charge=False)
                return result
            self._recover(step=step, module_index=module_index)
            if confirmed is not None and confirmed(self.observed_items or []):
                self.check()
                return {"status": "confirmed_snapshot", "snapshot_id": self.observed_snapshot_id}
            if self._remaining() <= 0:
                raise RecoveryStopped("recovery_budget_exhausted", step=step, module_index=module_index)


def retry_command(command, *, check, step: str, settle_seconds: float = 0.0, sleeper=sleep):
    """单角色仅共享未派发拒绝的分类和短重试，不凭空创建任务级恢复范围。"""
    for attempt in range(len(SHORT_DELAYS) + 1):
        check()
        try:
            result = command()
        except Exception as error:
            if not is_pre_dispatch_rejection(error) or attempt == len(SHORT_DELAYS):
                raise
            sleeper(SHORT_DELAYS[attempt])
            continue
        if settle_seconds:
            sleeper(settle_seconds)
        return result
    raise RecoveryStopped("recovery_budget_exhausted", step=step)
