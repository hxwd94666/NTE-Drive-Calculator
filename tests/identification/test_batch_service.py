# 验证鉴定批量结果、冻结输入、取消以及路径和角色权重的一致性。
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.services.identification import equipment_service, tasks
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import normalize_matching_settings

NTE_TEST_TIER = "core"


def equipment():
    return Tape(uid="fixture-tape", quality="Gold", area=15,
                set_name="别名", main_stats="攻击力%", sub_stats={"暴击率%": 2.8})


class Orchestrator:
    roles_db = {"角色": {"character_id": 1001, "default_set": "套装", "weights": {"暴击率%": 1.0}}}
    sets_db = {"套装": {"suit_id": "A", "shapes": ["H_2"]}}

    @staticmethod
    def _resolve_set_name(value):
        return "套装"

    def solve_blueprints(self, roles, **kwargs):
        return {role: [{"set_pieces": ["H_2"]}] for role in roles}


def request(items):
    return tasks.IdentificationMatchRequest.freeze(
        config_dir=Path("config"), user_database_path=Path("account.sqlite3"),
        static_database_path=Path("data/game_static.sqlite3").resolve(), items=items,
    )


def test_batch_matches_existing_score_without_mutating_inputs():
    inputs = [equipment(), Drive(uid="drive", quality="Gold", area=2,
                                shape_id="H_2", set_name="未知套装",
                                main_stats={"攻击力": 10, "生命值": 100},
                                sub_stats={"暴击率%": 2.8})]
    before = [item.model_dump() for item in inputs]
    frozen = request(inputs)
    scoring = ScoringEngine("config", roles_db=Orchestrator.roles_db)
    matcher = equipment_service.EquipmentIdentificationService(
        Orchestrator(), build_equipment_match_context(Orchestrator()), scoring,
    )
    expected = [matcher.identify_item(item.model_copy(deep=True)) for item in inputs]
    with patch.object(equipment_service.EquipmentIdentificationService,
                      "from_paths", return_value=matcher) as factory, \
         patch.object(tasks.MatchingSettingsService, "load", return_value=normalize_matching_settings(None)):
        actual = tasks.match_identification_items(frozen)
    assert actual == expected
    assert [item.model_dump() for item in inputs] == before
    assert actual[0]["item"].set_name == "套装"
    assert frozen.items[0] is not inputs[0]
    assert factory.call_count == 1


def test_matcher_reuses_roles_and_selected_static_paths():
    orchestrator = Orchestrator()
    cancel = lambda: False
    selected = Path("data/game_static.sqlite3").resolve()
    with patch.object(equipment_service, "NTEPipelineOrchestrator", return_value=orchestrator) as ctor, \
         patch.object(equipment_service, "ScoringEngine") as scoring, \
         patch.object(equipment_service.MatchingSettingsService, "load", return_value=normalize_matching_settings(None)):
        equipment_service.EquipmentIdentificationService.from_paths(
            config_dir="config", user_database_path="user.sqlite3",
            static_database_path=selected, cancel_check=cancel,
        )
    assert ctor.call_args.kwargs["static_database_path"] == selected
    assert scoring.call_args.kwargs["static_database_path"] == selected
    assert scoring.call_args.kwargs["roles_db"] is orchestrator.roles_db


def test_settings_change_during_last_item_rejects_whole_batch(tmp_path):
    from src.services.identification.matching_settings import MatchingSettingsService
    from src.storage.sqlite.user_data_dao import UserDataDao

    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    settings = MatchingSettingsService(database)
    matcher = equipment_service.EquipmentIdentificationService(
        Orchestrator(), build_equipment_match_context(Orchestrator(), settings.load()),
        ScoringEngine("config", roles_db=Orchestrator.roles_db))
    frozen = tasks.IdentificationMatchRequest.freeze(
        config_dir=Path("config"), user_database_path=database,
        static_database_path=Path("data/game_static.sqlite3").resolve(), items=[equipment()])
    identify = matcher.identify_items
    def identify_then_change(items, **kwargs):
        result = identify(items, **kwargs)
        settings.save({"version": 1, "extra_suits": {"1001": ["B"]}}, expected=settings.load())
        return result
    with patch.object(equipment_service.EquipmentIdentificationService, "from_paths", return_value=matcher), \
         patch.object(matcher, "identify_items", side_effect=identify_then_change):
        with pytest.raises(CancelledError, match="角色匹配套装已变化"):
            tasks.match_identification_items(frozen)
    assert settings.load()["extra_suits"] == {"1001": ["B"]}


def test_cancel_stops_before_initialization_and_between_items():
    frozen = request([equipment(), equipment()])
    with patch.object(equipment_service.EquipmentIdentificationService, "from_paths") as factory:
        with pytest.raises(CancelledError):
            tasks.match_identification_items(frozen, cancel_check=lambda: True)
        factory.assert_not_called()
        cancelled = False
        def identify(items, *, checkpoint):
            nonlocal cancelled
            checkpoint()
            cancelled = True
            checkpoint()
            raise AssertionError("cancelled batch continued")
        matcher = SimpleNamespace(identify_items=Mock(side_effect=identify))
        factory.return_value = matcher
        with pytest.raises(CancelledError):
            tasks.match_identification_items(frozen, cancel_check=lambda: cancelled)
        assert matcher.identify_items.call_count == 1
    assert tasks.match_identification_items(request([])) == []


def test_image_options_frozen_and_cancelled_after_one_parse():
    options = {"type": "drive", "shape_id": "H_2"}
    job = tasks.IdentificationImageJob.from_options(Path("image.png"), options)
    options["shape_id"] = "H_4"
    frozen = tasks.IdentificationImageRequest(Path("shots"), Path("preview.json"),
                                             Path("config"), (job, job))
    cancelled = False
    def parse(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        assert kwargs["forced_shape_id"] == "H_2"
        return []
    with patch("src.integrations.vision.recognition.batch_processor.BatchProcessor") as processor:
        processor.return_value.parse_identify_items.side_effect = parse
        with pytest.raises(CancelledError):
            tasks.parse_identification_images(frozen, cancel_check=lambda: cancelled)
        assert processor.return_value.parse_identify_items.call_count == 1


def test_controller_submits_plain_frozen_request_not_owner_closure():
    from src.features.identification import controller
    from src.features.identification.dependencies import IdentificationDependencies
    from src.features.identification.task_controller import IdentificationTaskController
    item = equipment()
    dependencies = IdentificationDependencies("fixture", 1, Path("account"), Path("shots"),
        Path("user-config"), Path("old.sqlite3"), Path("config"), Path("static.sqlite3"))
    task_owner = IdentificationTaskController(lambda: dependencies)
    owner = SimpleNamespace(_identify_tasks=task_owner,
                            _identify_task=task_owner.begin(dependencies),
                            _set_identify_busy=Mock())
    with patch.object(task_owner._lane, "submit") as lane:
        controller.IdentificationController._start_identify_items(owner, [item])
        target = lane.call_args.args[0].read
        owner._identify_task = None
        item.sub_stats.clear()
    assert target.func is tasks.match_identification_items
    assert target.args[0].user_database_path == Path("old.sqlite3")
    assert target.args[0].items[0].sub_stats == {"暴击率%": 2.8}
    assert owner not in target.args
    task_owner.close()
