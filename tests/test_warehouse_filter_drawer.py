# 验证仓库筛选抽屉的交互与筛选投影。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _projected_items(source: str) -> list[dict[str, object]]:
    from src.features.inventory.warehouse import warehouse_item_view

    rows = [
        {
            "kind": "core",
            "uid_slot": 1,
            "uid_serial": 11,
            "quality": "orange",
            "suit_id": "Suit6",
            "main_stats": [
                {"property_id": "CritBase", "value": 0.1, "percent": True}
            ],
            "sub_stats": [
                {"property_id": "AtkUp", "value": 0.1, "percent": True},
                {
                    "property_id": "CritDamageBase",
                    "value": 0.1,
                    "percent": True,
                },
            ],
            "equipped": True,
            "locked": True,
        },
        {
            "kind": "module",
            "uid_slot": 2,
            "uid_serial": 22,
            "quality": "purple",
            "geometry": "EquipmentGeometry_Hen3",
            "sub_stats": [
                {"property_id": "AtkUp", "value": 0.1, "percent": True}
            ],
        },
    ]
    return [warehouse_item_view(row, source=source) for row in rows]


def test_projection_keeps_official_filter_ids_for_packet_and_visual_snapshots() -> None:
    for source in ("nte_core", "gamepad"):
        core, module = _projected_items(source)
        assert core["suit_id"] == "Suit6"
        assert core["item_type_id"] == "core:Suit6"
        assert module["shape_id"] == "EquipmentGeometry_Hen3"
        assert module["item_type_id"] == "module:EquipmentGeometry_Hen3"
        assert core["main_stats"][0]["property_id"] == "CritBase"
        assert core["sub_stats"][0]["property_id"] == "AtkUp"


def test_stat_filter_has_identical_results_for_packet_and_visual_snapshots() -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )

    spec = WarehouseFilterSpec(
        kind="core",
        qualities=frozenset(("gold",)),
        main_property_ids=frozenset(("CritBase",)),
        sub_property_ids=frozenset(("AtkUp", "CritDamageBase")),
        min_sub_stat_matches=2,
    )
    for source in ("nte_core", "gamepad"):
        result = filter_projected_warehouse_items(_projected_items(source), spec)
        assert [item["uid"] for item in result] == ["nte-core-1-11"]


def test_state_filter_uses_equipped_locked_discarded_and_other() -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )

    equipped = WarehouseFilterSpec(statuses=frozenset(("equipped",)))
    other = WarehouseFilterSpec(statuses=frozenset(("other",)))
    assert len(filter_projected_warehouse_items(_projected_items("nte_core"), equipped)) == 1
    assert filter_projected_warehouse_items(_projected_items("gamepad"), equipped) == []
    assert len(filter_projected_warehouse_items(_projected_items("gamepad"), other)) == 2
    assert len(
        filter_projected_warehouse_items(
            _projected_items("gamepad"),
            WarehouseFilterSpec(kind="module", statuses=frozenset(("other",))),
        )
    ) == 1

    packet_items = _projected_items("nte_core")
    packet_items.append(
        {
            **packet_items[1],
            "uid": "discarded-drive",
            "locked": False,
            "discarded": True,
        }
    )
    locked = WarehouseFilterSpec(statuses=frozenset(("locked",)))
    discarded = WarehouseFilterSpec(statuses=frozenset(("discarded",)))
    assert [item["uid"] for item in filter_projected_warehouse_items(packet_items, locked)] == [
        "nte-core-1-11"
    ]
    assert [
        item["uid"] for item in filter_projected_warehouse_items(packet_items, discarded)
    ] == ["discarded-drive"]


def test_snapshot_presenter_routes_packet_and_visual_rows_into_the_same_filter(
    monkeypatch,
) -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )
    from src.features.inventory import warehouse_presenter

    class Service:
        source = "nte_core"

        def __init__(self, *_args, **_kwargs):
            pass

        def load_current_snapshot(self):
            return {
                "snapshot_id": 7,
                "source": self.source,
                "rows": [
                    {
                        "kind": "core",
                        "uid_slot": 9,
                        "uid_serial": 10,
                        "quality": "orange",
                        "suit_id": "Suit6",
                        "main_stats": [
                            {
                                "property_id": "CritBase",
                                "value": 0.1,
                                "percent": True,
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(warehouse_presenter, "WarehouseInventoryService", Service)
    spec = WarehouseFilterSpec(main_property_ids=frozenset(("CritBase",)))
    for source in ("nte_core", "gamepad"):
        Service.source = source
        snapshot = warehouse_presenter.load_warehouse_snapshot("unused.sqlite3")
        assert snapshot["source"] == source
        assert len(filter_projected_warehouse_items(snapshot["items"], spec)) == 1


def test_catalog_counts_each_property_once_per_item() -> None:
    from src.domain.warehouse_filter import build_warehouse_filter_catalog

    catalog = build_warehouse_filter_catalog(_projected_items("nte_core"))
    sub_counts = {option.value: option.count for option in catalog.sub_properties}
    assert sub_counts == {"AtkUp": 2, "CritDamageBase": 1}
    assert {option.value for option in catalog.item_types} == {
        "core:Suit6",
        "module:EquipmentGeometry_Hen3",
    }


def test_catalog_matches_game_property_shape_and_status_order() -> None:
    from src.domain.warehouse_filter import build_warehouse_filter_catalog

    main_order = (
        "HPMaxUp",
        "AtkUp",
        "DefUp",
        "CritBase",
        "CritDamageBase",
        "MagBase",
        "UnbalIntensityBase",
        "HealUp",
        "DamageUpCosmosBase",
        "DamageUpNatureBase",
        "DamageUpIncantationBase",
        "DamageUpChaosBase",
        "DamageUpPsycheBase",
        "DamageUpLakshanaBase",
        "DamageUpPsychicallyBase",
    )
    sub_order = (
        "HPMaxUp",
        "AtkUp",
        "DefUp",
        "HPMaxAdd",
        "AtkAdd",
        "DefAdd",
        "CritBase",
        "CritDamageBase",
        "MagBase",
        "UnbalIntensityBase",
        "DamageUpGeneralBase",
    )
    shape_order = (
        "EquipmentGeometry_Hen2",
        "EquipmentGeometry_Shu2",
        "EquipmentGeometry_Hen3",
        "EquipmentGeometry_Shu3",
        "EquipmentGeometry_ZhiJiao1",
        "EquipmentGeometry_ZhiJiao2",
        "EquipmentGeometry_ZhiJiao3",
        "EquipmentGeometry_ZhiJiao4",
        "EquipmentGeometry_Hen4",
        "EquipmentGeometry_Shu4",
        "EquipmentGeometry_Z3",
        "EquipmentGeometry_Z4",
    )
    core_rows = [
        {
            "kind": "core",
            "item_type_id": "core:Suit1",
            "item_type_label": "套装",
            "quality": "gold",
            "main_stats": [
                {"property_id": value, "label": value} for value in reversed(main_order)
            ],
            "sub_stats": [
                {"property_id": value, "label": value} for value in reversed(sub_order)
            ],
        }
    ]
    module_rows = [
        {
            "kind": "module",
            "item_type_id": f"module:{value}",
            "item_type_label": value,
            "quality": "gold",
        }
        for value in reversed(shape_order)
    ]

    core_catalog = build_warehouse_filter_catalog(core_rows, kind="core")
    module_catalog = build_warehouse_filter_catalog(module_rows, kind="module")

    assert tuple(option.value for option in core_catalog.main_properties) == main_order
    assert tuple(option.value for option in core_catalog.sub_properties) == sub_order
    assert tuple(
        option.value.removeprefix("module:") for option in module_catalog.item_types
    ) == shape_order
    assert tuple((option.value, option.label) for option in core_catalog.statuses) == (
        ("equipped", "已装备"),
        ("locked", "已锁定"),
        ("discarded", "已弃置"),
        ("other", "其他"),
    )


def test_snapshot_refresh_normalizes_the_applied_spec_not_a_cancelled_draft() -> None:
    from PySide6.QtWidgets import QApplication, QWidget

    from src.domain.warehouse_filter import WarehouseFilterSpec
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = WarehouseFilterDrawer(host)
    applied = WarehouseFilterSpec(qualities=frozenset(("gold",)))
    drawer.set_items(_projected_items("nte_core"), applied)
    drawer.open_for(applied)
    drawer._draft = WarehouseFilterSpec(qualities=frozenset(("purple",)))

    normalized = drawer.set_items(_projected_items("nte_core"), applied)

    assert normalized.qualities == frozenset(("gold",))
    assert drawer.draft.qualities == frozenset(("gold",))


def test_filter_handles_two_thousand_visual_rows_without_source_specific_fields() -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )

    base = _projected_items("gamepad")[1]
    rows = [{**base, "uid": f"vision-module-{index}"} for index in range(2000)]
    result = filter_projected_warehouse_items(
        rows,
        WarehouseFilterSpec(
            kind="module",
            sub_property_ids=frozenset(("AtkUp",)),
            min_sub_stat_matches=1,
        ),
    )
    assert len(result) == 2000


def test_catalog_keeps_counts_for_two_thousand_rows() -> None:
    from src.domain.warehouse_filter import build_warehouse_filter_catalog

    core, _module = _projected_items("gamepad")
    rows = [{**core, "uid": f"vision-core-{index}"} for index in range(2000)]

    catalog = build_warehouse_filter_catalog(rows, kind="core")

    assert catalog.item_types[0].count == 2000
    assert catalog.main_properties[0].count == 2000
    assert {option.value: option.count for option in catalog.statuses}["other"] == 2000
