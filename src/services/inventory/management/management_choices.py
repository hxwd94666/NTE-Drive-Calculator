# 准备管理编辑器的静态选择与账号角色元数据，界面不执行 SQL 或素材目录探测。
from pathlib import Path
from src.domain.names.role_name_order import role_name_sort_key
from src.integrations.runtime.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.services.allocation.inputs.inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.rewind.recommendation import RewindShapeRecommendationService


def load_drive_shape_options(static_database_path: Path | None = None) -> list[tuple[str, int]]:
    with (
        StaticGameDataDao(static_database_path) if static_database_path is not None else StaticGameDataDao()
    ) as static_dao:
        options = [(legacy_shape_id(shape["shape_id"]), int(shape["cell_count"])) for shape in static_dao.list_shapes()]
    return sorted(options, key=lambda item: (item[1], item[0]))


def load_set_name_options(static_database_path: Path | None = None) -> list[str]:
    with (
        StaticGameDataDao(static_database_path) if static_database_path is not None else StaticGameDataDao()
    ) as static_dao:
        return [str(suit["name_zh"]) for suit in static_dao.list_suits()]


def load_role_options(
    user_database_path: Path | None = None,
    static_database_path: Path | None = None,
    asset_root: Path | None = None,
) -> list[tuple[int, str, str, bool]]:
    """Return role identity, optional official portrait, and custom-role flag."""

    asset_catalog = GameUiAssetCatalog(asset_root or bundled_game_ui_asset_root())
    with (
        StaticGameDataDao(static_database_path) if static_database_path is not None else StaticGameDataDao()
    ) as static_dao:
        options = [
            (
                int(character["character_id"]),
                str(character.get("name_zh") or character["character_id"]),
                str(asset_catalog.character_icon(int(character["character_id"])) or ""),
                False,
            )
            for character in static_dao.list_role_template_characters()
        ]
    if user_database_path is not None and Path(user_database_path).is_file():
        with UserDataDao(user_database_path) as user_dao:
            official_ids = {character_id for character_id, _name, _avatar, _custom in options}
            options.extend(
                (
                    character_id,
                    str(role.get("name_zh") or character_id),
                    "",
                    True,
                )
                for role in user_dao.list_custom_characters()
                if (character_id := int(role["character_id"])) not in official_ids
            )
    return sorted(options, key=lambda item: (role_name_sort_key(item[1]), item[0]))


def load_role_selection_data(
    user_database_path: Path | None, static_database_path: Path | None, asset_root: Path | None = None
):
    """Return the same scored role/slot records used by the rewind picker."""
    if user_database_path is None or static_database_path is None:
        return ()
    try:
        return RewindShapeRecommendationService(
            user_database_path=user_database_path,
            static_database_path=static_database_path,
            asset_root=asset_root,
        ).list_target_roles()
    except (OSError, ValueError, TypeError):
        return ()
