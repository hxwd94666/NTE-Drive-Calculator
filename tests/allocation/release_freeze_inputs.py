# 复用真实公共基线的最小合成输入，仅从当前正式schema构造临时SQLite A/B。
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from src.features.weighted_allocation.save_owner import WeightedOwnerIdentity, WeightedSaveOwner
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.static_release_helpers import ROOT

SCHEMA_PATHS = tuple(sorted((ROOT / "src/storage/sqlite/schema").glob("[0-9][0-9][0-9]_game_static*.sql")))
CHARACTER_ID = 2001
PROPERTIES = (("CritBase", "暴击率%"), ("CritDamageBase", "暴击伤害%"),
              ("DamageUpGeneralBase", "伤害增加%"), ("AtkUp", "攻击力%"))

def build_fixture(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "building.tmp"
    config = directory / "config"
    config.mkdir()
    stats = {"gold_base_values": {name: 1.0 for _, name in PROPERTIES},
             "tape_stat_values": {name: 10.0 for _, name in PROPERTIES},
             "tape_main_stats_pool": ["攻击力%"], "tape_main_stat_values": {"攻击力%": 37.5}}
    (config / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    connection = sqlite3.connect(path)
    for schema in SCHEMA_PATHS:
        connection.executescript(schema.read_text(encoding="utf-8"))
    stamp = "2026-01-01T00:00:00Z"
    connection.executemany("INSERT INTO schema_migration VALUES (?, ?)",
                           [(int(schema.name.split("_", 1)[0]), stamp) for schema in SCHEMA_PATHS])
    connection.execute("INSERT INTO dataset VALUES ('builder-fixture', 51, ?)", (stamp,))
    connection.execute("INSERT INTO dataset_scope VALUES ('builder-fixture', 'game')")
    connection.execute("INSERT INTO source_file VALUES (1, 'fixture.json', ?, 1)", ("0" * 64,))
    connection.execute("INSERT INTO source_row VALUES (1, 1, 'fixture', NULL, ?)", ("0" * 64,))
    connection.execute("INSERT INTO character(character_id,name_zh,group_type,source_row_id) VALUES (?, '合成角色', 'fixture', 1)", (CHARACTER_ID,))
    connection.execute("INSERT INTO character_annotation VALUES (?, 'fixture-role', ?, 'available_character', 'fixture')", (CHARACTER_ID, CHARACTER_ID))
    connection.execute("INSERT INTO character_panel_growth VALUES (?,80,6,'max_level',1000,100,50,1,1,1)", (CHARACTER_ID,))
    for property_id, name in PROPERTIES:
        connection.execute("INSERT INTO equipment_attribute(attribute_id,display_name_zh,filter_name_zh,show_percent,show_outside,show_inside,source_row_id) VALUES (?,?,?,1,1,1,1)", (property_id, name, name))
    connection.execute("INSERT INTO character_weight_recommendation(character_id,source_kind,source_updated_at_utc) VALUES (?, 'workshop_cache', ?)", (CHARACTER_ID, stamp))
    connection.executemany("INSERT INTO character_weight_recommendation_property VALUES (?, ?, 1, 1, ?)",
                           [(CHARACTER_ID, property_id, ordinal) for ordinal, (property_id, _) in enumerate(PROPERTIES)])
    connection.execute("INSERT INTO character_graduation_template(character_id,source_kind,core_main_property_id,drive_area,extra_shape_count,benchmark_damage,profile_json,equipment_json,generated_at_utc) VALUES (?, 'official_default','AtkUp',20,0,1,'{}','[]',?)", (CHARACTER_ID, stamp))
    connection.execute("CREATE TABLE build_owner_sentinel(value TEXT)")
    connection.execute("INSERT INTO build_owner_sentinel VALUES ('owner-original')")
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return connection, path, config

def write_manifest(path):
    import hashlib
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        dataset, importer, built = db.execute('SELECT dataset_id,importer_version,built_at_utc FROM dataset').fetchone()
        schema = db.execute('SELECT MAX(version) FROM schema_migration').fetchone()[0]
    path.with_name('manifest.json').write_text(json.dumps({
        'catalog_scope': 'game', 'database': {'filename': path.name, 'dataset_id': dataset,
        'schema_version': schema, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'size_bytes': path.stat().st_size, 'source_payloads_omitted': True},
        'build_tool': {'importer_version': importer}}), encoding='utf-8')

def build_pair(directory):
    paths = []
    for label, value in (('A', .1), ('B', .3)):
        database, path, config = build_fixture(directory / label)
        database.execute("INSERT INTO equipment_shape VALUES ('EquipmentGeometry_H_3',3,0,0,1)")
        database.executemany("INSERT INTO equipment_shape_cell VALUES ('EquipmentGeometry_H_3',?,?,?)", [(i,0,i) for i in range(3)])
        database.execute("INSERT INTO equipment_shape VALUES ('EquipmentGeometry_H_2',2,0,0,1)")
        database.executemany("INSERT INTO equipment_shape_cell VALUES ('EquipmentGeometry_H_2',?,?,?)", [(i,0,i) for i in range(2)])
        database.execute("INSERT INTO equipment_suit(suit_id,name_zh,source_row_id) VALUES ('fixture-suit','合成套装',1)")
        database.execute("INSERT INTO equipment_suit_required_shape VALUES ('fixture-suit',0,'EquipmentGeometry_H_3')")
        for item_id, kind in (('drive_h3','module'),('drive_h2','module'),('core_atk','core')):
            database.execute('''INSERT INTO equipment_item(item_id,kind,quality,name_zh,geometry_id,grid_count,
                max_level,random_base_attribute_count,random_sub_attribute_count,random_sub_attribute_max_count,
                is_guide_item,source_row_id) VALUES (?,?,'orange',?,?,?,20,1,1,5,0,1)''',
                (item_id,kind,item_id,('EquipmentGeometry_H_2' if item_id=='drive_h2' else 'EquipmentGeometry_H_3') if kind=='module' else None, (2 if item_id=='drive_h2' else 3) if kind=='module' else None))
        for category in ('2','3','Core'):
            curve=f'AtkUp_{category}_ITEM_QUALITY_ORANGE'
            database.execute('INSERT INTO equipment_base_attribute_curve(curve_id,default_value,source_row_id) VALUES (?,0,1)',(curve,))
            database.executemany('INSERT INTO equipment_base_attribute_point VALUES (?,?,?,?)', [(curve,0,1,value/2),(curve,1,20,value)])
        for cid in (2001,2002):
            if cid==2002:
                database.execute("INSERT INTO character(character_id,name_zh,group_type,source_row_id) VALUES (2002,'合成角色','fixture',1)")
                database.execute("INSERT INTO character_annotation VALUES (2002,'fixture-role-2',2002,'available_character','fixture')")
                database.execute("INSERT INTO character_panel_growth VALUES (2002,80,6,'max_level',1000,100,50,1,1,1)")
                database.execute("INSERT INTO character_weight_recommendation(character_id,source_kind,source_updated_at_utc) VALUES (2002,'workshop_cache','fixture')")
                database.executemany('INSERT INTO character_weight_recommendation_property VALUES (?,?,1,1,?)',[(cid,pid,i) for i,(pid,_) in enumerate(PROPERTIES)])
            database.execute('UPDATE character SET name_zh=? WHERE character_id=?',(f'Byte-{label}-{cid}',cid))
            database.execute("INSERT INTO equipment_plan VALUES (?,'core_atk',20,20,1,NULL,NULL,1)",(cid,))
            database.executemany('INSERT INTO equipment_plan_cell VALUES (?,?,?,NULL)',[(cid,row,col) for row in range(1,5) for col in range(1,6)])
        database.execute('UPDATE character_panel_growth SET atk_base=?',(100 if label=='A' else 300,))
        database.execute("UPDATE character_weight_recommendation_property SET weight=? WHERE property_id='AtkUp'",(1 if label=='A' else 3,))
        database.commit()
        assert database.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert database.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        database.close()
        target=path.with_name('game_static.sqlite3'); path.rename(target); write_manifest(target)
        paths.append(target)
    assert paths[0].stat().st_size==paths[1].stat().st_size
    return tuple(paths), directory/'A/config/stats.json'


class PublicReleaseInputs:
    def __init__(self, directory):
            (self.static, self.other), self.rules = build_pair(directory/'packages')
            self.database = directory/'user.sqlite3'
            items = []
            for cid in (2001,2002):
                parts=[('module',21,cid*100+i,size) for size in (3,2) for i in range((size-2)*4,(size-2)*4+4)]
                parts.append(('core',22,cid,None))
                for kind, slot, serial, size in parts:
                    items.append({'uid':{'slot':slot,'serial':serial}, 'kind':kind,
                        'item_id':f'drive_h{size}' if kind=='module' else 'core_atk',
                        'geometry':f'EquipmentGeometry_H_{size}' if kind=='module' else None,
                        'grid':size,'quality':'orange','level':1,'max_level':20,
                        'locked':False,'discarded':False,'equipped':False,
                        'main_stats':[{'property_id':'AtkUp','value':.05,'percent':True}],
                        'sub_stats':[{'property_id':'AtkUp','value':.02,'percent':True}]})
            self.raw={'complete':True,'generation':91,'sequence':2,'item_count':len(items),'items':items}
            with UserDataDao(self.database,account_id='weighted-byte-fixture') as user:
                self.snapshot=user.import_inventory_snapshot(self.raw,source='nte_core')
                self.profile=user.create_optimization_profile('fixture',allocation_strategy='role_priority',
                    characters=[{'character_id':cid,'ordinal':i,'priority_group':i,'ignore_grade_limit':True}
                                for i,cid in enumerate((2001,2002))])
                self.slots={}
                for cid in (2001,2002):
                    slot=user.create_loadout_slot(cid,str(cid),slot_key='primary'); self.slots[cid]=slot
                    user.save_plan_to_slot(slot,name='old',source_snapshot_id=self.snapshot,score=12,
                        assignments=[{'kind':'core','uid_slot':22,'uid_serial':cid}],
                        payload={'schema':'allocation-official-snapshot-v1','source_role_name':str(cid),
                                 'assignment_scores':{f'nte-core-22-{cid}':12}})
            self.current=[WeightedOwnerIdentity('weighted-byte-fixture',7,self.database.resolve())]
            self.owner=WeightedSaveOwner(lambda:self.current[0])
