# 在页面已显示的固定库存上后台统计仓库分，不生成规则、建议或游戏操作。
from concurrent.futures import CancelledError
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

from src.services.inventory.warehouse_quality_assessment import assess_quality
from src.services.inventory.warehouse_recommendation_preview import file_identity, post_config
from src.services.identification.matching_settings import MatchingSettingsService
from src.services.characters.workshop_weight_template_service import configured_workshop_weight_template_file
from src.storage.sqlite.warehouse_input_snapshot import freeze_warehouse_inputs
from src.storage.sqlite.warehouse_recommendation_inputs import recommendation_account_identity


def read_warehouse_quality(request, snapshot_id, cancel_check):
    def checkpoint():
        if cancel_check():
            raise CancelledError('仓库评分已取消')
    checkpoint()
    database = request.database_path
    static = request.scoring_database_path
    config_dir = request.config_dir
    files = (static,static.with_name('manifest.json'),config_dir/'stats.json')
    template = configured_workshop_weight_template_file()
    if template is not None and template.is_file():
        files += (template,)
    identity, version = recommendation_account_identity(database), file_identity(files)
    settings = post_config(database)
    matching = MatchingSettingsService(database).load()
    with TemporaryDirectory(prefix='nte-warehouse-score-') as temporary:
        root = Path(temporary)
        frozen = root/'account.sqlite3'
        freeze_warehouse_inputs(database, frozen, snapshot_id=snapshot_id, cancel_check=cancel_check)
        if recommendation_account_identity(frozen)!=identity or post_config(frozen)!=settings:
            raise ValueError('评分输入已变化，请刷新仓库重试')
        (root/'data').mkdir();(root/'config').mkdir()
        for path in files[:2]:
            shutil.copy2(path,root/'data'/path.name)
        shutil.copy2(files[2],root/'config/stats.json')
        checkpoint()
        quality=assess_quality(frozen,root/'data'/static.name,root/'config',matching,cancel_check,
            preserve_rules=settings['preserve_rules'],snapshot_id=snapshot_id)
    checkpoint()
    if (recommendation_account_identity(database)!=identity or file_identity(files)!=version
            or post_config(database)!=settings or MatchingSettingsService(database).load()!=matching):
        raise ValueError('评分期间资料或规则已变化，请刷新仓库重试')
    return quality
