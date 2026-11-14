# 首次评级建议只冻结库存、批量评分和统计蓝图需求，不执行装备分配或游戏写入。
from src.observability.stage_timings import timed_call
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import CancelledError
import shutil
import time
from copy import deepcopy
from collections import Counter, defaultdict

from src.domain.equipment.warehouse.warehouse_quality import summarize_warehouse_quality
from src.domain.equipment.warehouse.intelligent_rules import policy_from_rules, merge_generated
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact, shape_demands, future_profiles
from src.domain.equipment.warehouse.warehouse_grade_advice import advise_drive_grade
from src.domain.equipment.properties.stat_catalog import StatCatalog
from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import effective_matching_suits, set_matching_suits
from src.services.characters.workshop_weight_template_service import configured_workshop_weight_template_file
from src.services.inventory.recommendation.warehouse_recommendation_preview import file_identity, recommendation_account_identity
from src.services.inventory.recommendation.warehouse_rule_generation import generate_warehouse_rules
from src.services.inventory.recommendation.warehouse_calculation_inputs import (
    freeze_warehouse_calculation_inputs, warehouse_orchestrator,
)


def assess_quality(inputs, static_database, config_dir, matching, cancel_check, *, preserve_rules=(),
                   advise_grade=False, timings=None):
    started = time.perf_counter()
    def checkpoint():
        if cancel_check():
            raise CancelledError('首次评级评估已取消')
    checkpoint()
    data = timed_call(timings, "quality.inventory", inputs.values)
    snapshot = inputs.snapshot_id
    originals = data['originals'] if advise_grade else []
    locked_plans = data['locked_owners'] if advise_grade else []
    items = [Drive(**row) if row['item_type']=='drive' else Tape(**row) for row in data['projected']]
    stats = StatCatalog.from_config_dir(config_dir)
    orchestrator = warehouse_orchestrator(inputs, static_database, config_dir, cancel_check, timings=timings)
    matching = deepcopy(matching)
    role = next((name for name,data in orchestrator.roles_db.items() if data['character_id']==1036),None)
    if role and 'cross_suit' in policy_from_rules(preserve_rules) and any(
            str(data['suit_id'])=='Suit6' for data in orchestrator.sets_db.values()):
        default = orchestrator.roles_db[role]['default_set']
        default_id = str(orchestrator.sets_db[default]['suit_id'])
        set_matching_suits(matching,1036,default_id,(*effective_matching_suits(matching,1036,default_id),'Suit6'))
    context = timed_call(timings, "quality.matching", build_equipment_match_context,orchestrator, matching, cancel_check=cancel_check, batch_defaults=True)
    if context.errors or context.missing_character_ids:
        raise ValueError('角色评分资料不完整，暂不能自动推荐评级；请补齐资料或手动添加评级保护规则。')
    scorer = ScoringEngine(config_dir, static_database_path=static_database,
        roles_db=orchestrator.roles_db, stat_catalog=stats)
    profiles = ([{'key':r,'weights':d.get('weights') or {},'main_weights':d.get('main_weights'),'max_weight':None}
                 for r,d in orchestrator.roles_db.items()]+future_profiles()) if advise_grade else None
    timed_call(timings, "quality.batch_scores", scorer.prepare_scores, items, profiles=profiles, assign_scores=True, checkpoint=checkpoint, cache_items=False)
    names = sorted({stats.normalize_tape_main_stat(i.main_stats) for i in items if isinstance(i,Tape)})
    matrix = scorer.prepare_weight_queries([d.get('main_weights') or d.get('weights') or {} for d in orchestrator.roles_db.values()],names)
    allowed = {role:{name for name,value in zip(names,values) if value>0}
               for role,values in zip(orchestrator.roles_db,matrix)}
    facts = []
    raw_by_uid={f"nte-{'module' if r['kind']=='module' else 'core'}-{r['uid_slot']}-{r['uid_serial']}":r for r in originals}
    for i in items:
        checkpoint()
        main = stats.normalize_tape_main_stat(i.main_stats) if isinstance(i,Tape) else ''
        roles = {m.role for m in context.for_item(i)}
        if isinstance(i,Tape):
            roles = {r for r in roles if main in allowed[r]}
        raw=raw_by_uid.get(i.uid,{})
        facts.append(WarehouseFact(i.uid,i.item_type,str(getattr(i,'suit_id','') or ''),i.shape_id,
            main,i.area,i.quality,tuple(i.sub_stats.items()),tuple(i.role_scores.items()),tuple(sorted(roles)),
            bool(raw.get('equipped')),bool(raw.get('locked')),bool(raw.get('discarded'))))
    blueprints = timed_call(timings, "quality.blueprints", orchestrator.solve_blueprints, list(orchestrator.roles_db),cancel_check=cancel_check)
    blueprints = {r:dedupe_blueprints_by_piece_signature(v) for r,v in blueprints.items()}
    statistics_started = time.perf_counter()
    demands=shape_demands(blueprints)
    quality = timed_call(timings, "quality.statistics", summarize_warehouse_quality, facts, demands)
    quality['statistics_seconds'] = time.perf_counter()-statistics_started
    quality['recommended_drive_grade'] = (scorer.get_grade_tag(quality['drive_rate']*10,1)
                                         if quality['drive_rate'] is not None else 'SSS')
    if advise_grade and quality['drive_rate'] is not None:
        multiplicity=defaultdict(int)
        for groups in blueprints.values():
            for group in groups:
                for shape,count in Counter((*group['set_pieces'],*group['extra_pieces'])).items():
                    multiplicity[shape]=max(multiplicity[shape],count)
        protected={f"nte-{'module' if r['kind']=='module' else 'core'}-{r['uid_slot']}-{r['uid_serial']}" for r in locked_plans}
        protected.update(uid for uid,r in raw_by_uid.items() if any(r.get(k) is None for k in ('locked','discarded','equipped')))
        advice=advise_drive_grade(facts,items,preserve_rules,demands,multiplicity,protected_uids=protected,
            sample_grade=quality['recommended_drive_grade'],checkpoint=checkpoint)
        quality['grade_advice']=advice
        quality['recommended_drive_grade']=advice['grade']
    quality['elapsed_seconds'] = time.perf_counter()-started
    quality['snapshot_id'] = snapshot
    checkpoint()
    return quality


def generate_first_rules(*, database, static_database, config_dir, config, matching, check_context, cancel_check):
    database, static_database, config_dir = map(Path,(database,static_database,config_dir))
    files = (static_database, static_database.with_name('manifest.json'), config_dir/'stats.json')
    template = configured_workshop_weight_template_file()
    if template is not None and template.is_file():
        files += (template,)
    identity, version = recommendation_account_identity(database), file_identity(files)
    check_context()
    with TemporaryDirectory(prefix='nte-warehouse-grade-') as temporary:
        root = Path(temporary)
        (root/'data').mkdir();(root/'config').mkdir()
        for source in files[:2]:
            shutil.copy2(source,root/'data'/source.name)
        shutil.copy2(files[2],root/'config/stats.json')
        frozen = freeze_warehouse_calculation_inputs(database, root/'data'/static_database.name,
            root/'config', cancel_check=cancel_check)
        if frozen.account_identity != identity:
            raise ValueError('冻结期间账号输入变化，请重新生成规则')
        initial=generate_warehouse_rules(root/'data'/static_database.name,config['preserve_rules'])
        quality=assess_quality(frozen,root/'data'/static_database.name,root/'config',matching,cancel_check,
                               preserve_rules=merge_generated(config['preserve_rules'],initial['rules']),advise_grade=True)
        result=generate_warehouse_rules(root/'data'/static_database.name,config['preserve_rules'],
                                       grade_choice=quality['recommended_drive_grade'])
    check_context()
    if cancel_check():
        raise CancelledError('首次评级评估已取消')
    if recommendation_account_identity(database)!=identity or file_identity(files)!=version:
        raise ValueError('评估期间账号或资料变化，请重新生成规则')
    result['options']['initial_grade_advice']={k:quality[k] for k in
        ('recommended_drive_grade','drive_rate','drive_count','elapsed_seconds','snapshot_id')}
    result['options']['initial_grade_advice']['budget']=quality.get('grade_advice')
    return result
