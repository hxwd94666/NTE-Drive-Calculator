# 用合成账号与真实SQLite、AppContext、Qt任务owner和两个正式writer构造槽位契约。
from copy import deepcopy
from time import monotonic
from functools import lru_cache

from PySide6.QtWidgets import QApplication
from src.app.context import AccountContext, AppContext, ApplicationPaths, CallbackAccountLifecycle
from src.features.weighted_allocation import runner
from src.features.weighted_allocation.save_owner import WeightedOwnerIdentity, WeightedSaveOwner
from src.services.allocation.context_builder import ALLOCATION_CONTEXT_SOLVER_VERSION
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao

from tests.allocation.static_release_helpers import ROOT


@lru_cache(maxsize=8)
def virtual_board(cid):
    # 从同一正式底盘/形状取得完整布局；保存fixture不伪造可编辑的残缺布局。
    with StaticGameDataDao(ROOT / 'data/game_static.sqlite3') as dao:
        cells = frozenset((cell['row'], cell['column']) for cell in dao.get_equipment_plan(cid)['cells'])
        shapes = dao.list_shapes()
    placements = []
    for shape in shapes:
        offsets = tuple((cell['x'], cell['y']) for cell in shape['cells'])
        for row in range(1, 6):
            for column in range(1, 6):
                occupied = frozenset((row + x, column + y) for x, y in offsets)
                if occupied and occupied <= cells:
                    placements.append((shape['shape_id'], row, column, occupied))
    @lru_cache(maxsize=None)
    def cover(remaining):
        if not remaining:
            return ()
        first = min(remaining)
        for shape, row, column, occupied in placements:
            if first in occupied and occupied <= remaining:
                rest = cover(remaining - occupied)
                if rest is not None:
                    return ((shape, row, column, len(occupied)), *rest)
        return None
    result = cover(cells)
    assert result is not None, '正式底盘没有可用于fixture的完整覆盖'
    return result


def app():
    return QApplication.instance() or QApplication([])


def pump(application, condition, timeout=30):
    deadline = monotonic() + timeout
    while not condition():
        assert monotonic() < deadline, '槽位契约Qt任务超过截止时间'
        application.processEvents()
        __import__('time').sleep(.001)
    application.processEvents()


class SlotScenario:
    def __init__(self, root, *, roles=(1003,), account_id='fixture-a'):
        self.root = root
        self.roles = roles
        self.account_id = account_id
        self.database = root / 'user.sqlite3'
        self.static = ROOT / 'data/game_static.sqlite3'
        with StaticGameDataDao(self.static) as static:
            equipment = static.list_equipment_items()
            template = next(row for row in equipment
                            if row['kind'] == 'core' and row.get('suit_id') == 'Suit1'
                            and str(row.get('quality')).casefold() == 'orange')
            summary = static.summary()
        self.dataset = {'schema_version': summary['schema_version'], **{
            key: summary['dataset'][key] for key in ('dataset_id', 'importer_version', 'built_at_utc')}}
        items = []
        for serial, value in ((11, .02), (12, .04), (13, .06)):
            items.append({
                'uid': {'serial': serial, 'slot': 22}, 'kind': 'core',
                'item_id': template['item_id'], 'suit_id': 'Suit1',
                'geometry': None, 'grid': None, 'quality': 'orange', 'level': 20, 'max_level': 20,
                'locked': False, 'discarded': False, 'equipped': False,
                'equipped_character_uid': None, 'equipped_character_id': None,
                'equipped_placement': None, 'names': {'zh_cn': '合成卡带'},
                'suit_names': {'zh_cn': '合成空幕'},
                'main_stats': [{'property_id': 'AtkUp', 'value': .3, 'percent': True}],
                'sub_stats': [{'property_id': 'AtkUp', 'value': value, 'percent': True}],
            })
        # saved plan中的虚拟布局不是求解候选；同源真实库存须独立具备正式完整驱动覆盖。
        for cid in roles:
            for ordinal, (shape, _row, _column, count) in enumerate(virtual_board(cid)):
                drive = next(row for row in equipment if row['kind'] == 'module'
                             and row['geometry_id'] == shape
                             and str(row['quality']).casefold() == 'orange')
                items.append({**deepcopy(items[0]), 'uid': {'serial': cid * 100 + ordinal, 'slot': 21},
                    'kind': 'module', 'item_id': drive['item_id'], 'suit_id': None,
                    'geometry': shape, 'grid': count,
                    'names': {'zh_cn': '合成正式驱动'}, 'suit_names': {},
                    'main_stats': [{'property_id': 'AtkUp', 'value': .05, 'percent': True}],
                    'sub_stats': [{'property_id': 'AtkUp', 'value': .02, 'percent': True}]})
        # 正式1003的加成类型为Type-3；仅有完整二格铺板仍不满足既有选优前置。
        for drive in equipment:
            if (drive['kind'] != 'module' or drive['grid_count'] != 3
                    or str(drive['quality']).casefold() != 'orange'):
                continue
            for _copy in range(7):
                items.append({**deepcopy(items[-1]), 'uid': {'serial': 900000 + len(items), 'slot': 21},
                    'item_id': drive['item_id'], 'geometry': drive['geometry_id'], 'grid': 3})
        self.raw = {'jsonrpc': '2.0', 'method': 'event.inventory.snapshot', 'params': {
            'complete': True, 'generation': 91, 'sequence': 2,
            'observed_at_unix_ms': 1_784_308_856_895, 'item_count': len(items), 'items': items,
        }}
        with UserDataDao(self.database, account_id=account_id) as dao:
            self.snapshot_id = dao.import_inventory_snapshot(self.raw)
            self.profile = dao.create_optimization_profile('__weighted_allocation_role_priority__',
                allocation_strategy='role_priority', characters=[{
                    'character_id': cid, 'ordinal': ordinal, 'priority_group': ordinal,
                    'property_weights': {'AtkUp': 1.0}, 'ignore_grade_limit': True,
                } for ordinal, cid in enumerate(roles)])
        self.profile_id = self.profile['profile_id']
        self.version = self.profile['version']['version_number']
        paths = ApplicationPaths.from_roots(root=ROOT, app_dir=ROOT, data_root=root / 'local',
            bundled_config_dir=ROOT / 'config', asset_dir=ROOT / 'data',
            app_icon_path=root / 'none.ico', static_database_path=self.static)
        account = AccountContext(account_id, '合成账号', root, self.database,
            root / 'config', root / 'screenshots', root / 'logs')
        self.context = AppContext(paths, account)
        identity_path = None

        def current_identity():
            nonlocal identity_path
            current_account = self.context.account
            database = current_account.user_database_path
            cached = identity_path
            # 消费者只使用自有临时库，不重定向符号链接；仅缓存当前上下文及Path的规范路径。
            # 账号对象或路径变化即替换唯一缓存，账号ID与代次每次读取，ABA切换不会复活旧任务。
            if cached is None or cached[0] is not current_account or cached[1] is not database:
                cached = (current_account, database, database.resolve())
                identity_path = cached
            return WeightedOwnerIdentity(
                current_account.active_account_id, self.context.generation, cached[2])

        self.owner = WeightedSaveOwner(current_identity)
        self.context.register_account_lifecycle(CallbackAccountLifecycle(
            is_running=self.owner.is_running, stop=self.owner.stop,
            rebuild=lambda _account: self.owner.rebuild(), start=lambda: None))

    def record(self, cid=1003, serial=11, score=40., *, source=None, missing=None, virtual=False, core_only=False):
        assignment = {'uid_slot': 0 if virtual else 22, 'uid_serial': serial,
                      'kind': 'core', 'rotation': 0, 'virtual': virtual}
        if virtual:
            assignment['virtual_equipment'] = {
                'item_id': 'fixture-virtual-core', 'kind': 'core', 'suit_id': 'Suit1',
                'quality': 'orange', 'geometry': None, 'grid_count': None}
        assignments = [assignment]
        for ordinal, (shape, row, column, count) in enumerate(() if core_only else virtual_board(cid)):
            assignments.append({'uid_slot': 0, 'uid_serial': cid * 100 + ordinal,
                'kind': 'module', 'target_row': row, 'target_column': column, 'rotation': 0,
                'geometry': shape, 'grid_count': count, 'virtual': True,
                'virtual_equipment': {'item_id': f'fixture-virtual-{shape}', 'kind': 'module',
                    'geometry': shape, 'grid_count': count, 'quality': 'orange', 'suit_id': None}})
        payload = {'schema': 'allocation-official-snapshot-v1', 'source': 'weighted_allocation',
            'source_role_name': f'合成角色{cid}', 'profile_id': self.profile_id,
            'profile_version': self.version, 'solver_version': ALLOCATION_CONTEXT_SOLVER_VERSION,
            'static_dataset': dict(self.dataset),
            'assignment_scores': {f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}":
                                 score if item is assignment else 0. for item in assignments}}
        if missing == 'scores':
            payload.pop('assignment_scores')
        return {'name': '合成保存方案', 'character_id': cid,
                'source_snapshot_id': self.snapshot_id if source is None else source,
                'score': score, 'payload': payload, 'assignments': assignments}

    def primary(self, **values):
        with UserDataDao(self.database) as dao:
            ids = dao.replace_active_loadout_plans([self.record(**values)])
            plan = dao.get_loadout_plan(ids[0])
        return plan['slot_id'], ids[0]

    def explicit(self, slot=None, *, cid=1003, **values):
        with UserDataDao(self.database) as dao:
            if slot is None:
                slot = dao.create_loadout_slot(cid, '合成备用')
            ids = dao.save_plans_to_slots([{**self.record(cid=cid, **values), 'slot_id': slot}])
        return slot, ids[0]

    def read(self, mapping=None):
        args = (self.database, self.static, True)
        return (runner.read_weighted_allocation_persistence(*args) if mapping is None else
                runner.read_weighted_allocation_persistence(*args, slot_ids_by_character=mapping))

    def restore(self, mapping=None):
        persistence = self.read(mapping)
        preview = runner.restore_weighted_allocation_preview(persistence,
            ownership=self.owner.capture(self.owner.current_identity()))
        return persistence, preview

    def calculate(self):
        request = runner.WeightedAllocationRequest(self.database, self.snapshot_id,
            self.profile_id, self.version, 1, include_role_top_k=False,
            static_database_path=self.static, equipment_only=True,
            ownership=self.owner.capture(self.owner.current_identity()),
            scoring_rules_path=ROOT / 'config/stats.json')
        return runner.run_weighted_allocation(request)


    def rows(self):
        with UserDataDao(self.database) as dao:
            assert dao.integrity_check()['ok']
            return dao.list_loadout_plans(), dao.list_visible_loadout_slots()

    def newer_inventory(self):
        raw = deepcopy(self.raw)
        raw['params']['generation'] += 1
        for item in raw['params']['items']:
            item['sub_stats'][0]['value'] += .5
        with UserDataDao(self.database) as dao:
            return dao.import_inventory_snapshot(raw)

    def close(self, application):
        self.owner.request_stop()
        pump(application, self.owner.try_retire)
        self.owner.close()


def binding(preview, cid=1003):
    return next(row for row in preview.slot_bindings if row.character_id == cid)


def replacement(owner, preview, serial):
    old = next(a.uid for a in preview.result.unified.selected[0].assignments if a.kind == 'core')
    updated = runner.replace_weighted_allocation_assignment(preview, old_uid=old, new_uid=(22, serial))
    return save_confirm(owner, updated)




def save_confirm(owner, preview, *, slot_ids_by_character=None):
    """夹具直接调用真实提交、回执与current复核，不模拟已删除的UI状态机。"""
    from src.services.allocation.weighted_cursor import assert_weighted_save_receipt_current
    operation = owner.begin_save(preview)
    ids = runner.save_weighted_allocation_preview(preview, ownership=operation,
        slot_ids_by_character=slot_ids_by_character)
    confirmed = runner.confirm_weighted_allocation_save(preview, ids, ownership=operation,
        slot_ids_by_character=slot_ids_by_character)
    assert_weighted_save_receipt_current(confirmed, operation)
    return confirmed
