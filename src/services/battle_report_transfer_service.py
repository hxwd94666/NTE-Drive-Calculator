# 编排账号内战报包导出与事务式导入。

from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.domain.battle_report_transfer import (
    BattleReportExportOutcome,
    BattleReportImportOutcome,
    BattleReportTransferEntry,
    battle_equipment_sha256,
    canonical_battle_equipment_json,
)
from src.integrations.battle_report_bundle import (
    BATTLE_REPORT_BUNDLE_EXTENSION,
    read_battle_report_bundle,
    write_battle_report_bundle_atomic,
)
from src.services.account_naming_service import AccountNamingService
from src.services.battle_report_history_service import BattleReportHistoryService
from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
    UserDataDao,
    UserDataError,
    UserDataValidationError,
)


BATTLE_REPORT_TRANSFER_FORMAT = "nte-drive-calculator.battle-report-package"
BATTLE_REPORT_TRANSFER_VERSION = 2
SUPPORTED_SOURCE_USER_DATABASE_SCHEMAS = frozenset({36, 37, 38, 39})


@dataclass(frozen=True, slots=True)
class BattleReportTransferDependencies:
    account_id: str
    generation: int
    user_database_path: Path
    accounts_index_path: Path
    static_database_path: Path
    static_manifest_path: Path
    application_version: str


BattleReportTransferContextGuard = Callable[
    [BattleReportTransferDependencies],
    bool,
]


class StaleBattleReportTransferContextError(RuntimeError):
    """The dialog's frozen account or generation is no longer current."""


class BattleReportTransferService:
    def __init__(
        self,
        *,
        dependencies: BattleReportTransferDependencies,
        context_is_current: BattleReportTransferContextGuard,
        history_service: BattleReportHistoryService,
    ) -> None:
        self._dependencies = BattleReportTransferDependencies(
            account_id=str(dependencies.account_id),
            generation=int(dependencies.generation),
            user_database_path=Path(dependencies.user_database_path).resolve(),
            accounts_index_path=Path(dependencies.accounts_index_path).resolve(),
            static_database_path=Path(dependencies.static_database_path).resolve(),
            static_manifest_path=Path(dependencies.static_manifest_path).resolve(),
            application_version=str(dependencies.application_version),
        )
        self._context_is_current = context_is_current
        self._history_service = history_service
        self._naming = AccountNamingService(
            accounts_index_path=self._dependencies.accounts_index_path,
            user_database_path=self._dependencies.user_database_path,
            account_id=self._dependencies.account_id,
            context_is_current=self._is_current,
        )

    def current_account_name(self) -> str:
        return self._naming.current_name()

    def rename_current_account(self, value: object) -> str:
        return self._naming.rename(value)

    def list_entries(self) -> tuple[BattleReportTransferEntry, ...]:
        self._ensure_current()
        result = []
        with self._open_current_dao() as user_dao:
            statuses = user_dao.battle_report_transfer_statuses()
        for record in self._history_service.list_records():
            status = statuses.get(int(record["battle_record_id"]))
            result.append(BattleReportTransferEntry(
                battle_record_id=int(record["battle_record_id"]),
                captured_at_utc=str(record["captured_at_utc"]),
                gameplay_label=self._gameplay_label(record),
                scope_label=self._scope_label(record),
                completeness_label=self._completeness_label(record),
                cursor_label=self._cursor_label(status),
                retention_label=(
                    "手动保存"
                    if str(record["retention_kind"]) == "manual"
                    else "自动保存"
                ),
                total_hits=int(record["total_hits"]),
            ))
        return tuple(result)

    def suggested_filename(self) -> str:
        name = self.current_account_name()
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
        safe = safe[:48] or "当前账号"
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        return f"战报-{safe}-{timestamp}{BATTLE_REPORT_BUNDLE_EXTENSION}"

    def export_reports(
        self,
        battle_record_ids: Sequence[int],
        target_path: str | Path,
    ) -> BattleReportExportOutcome:
        selected_ids = self._normalize_report_ids(battle_record_ids)
        self._ensure_current()
        static_metadata = self._load_static_metadata()
        account_name = self.current_account_name()
        exported_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        available = {
            int(record["battle_record_id"]): record
            for record in self._history_service.list_records()
        }
        missing_ids = [record_id for record_id in selected_ids if record_id not in available]
        if missing_ids:
            raise UserDataValidationError("所选战报已不存在，请刷新列表后重试")

        unavailable: list[dict[str, Any]] = []
        reports = []
        with self._open_current_dao() as user_dao:
            for record_id in selected_ids:
                row_graph = user_dao.load_battle_report_transfer_rows(record_id)
                if row_graph is None:
                    raise UserDataValidationError("所选战报已不存在，请刷新列表后重试")
                target_condition = user_dao.load_battle_target_condition(record_id)
                build = user_dao.load_battle_build_snapshot(record_id)
                build_edit = user_dao.load_battle_build_edit(record_id)
                import_origin = user_dao.load_battle_report_import_origin(record_id)
                import_locks = user_dao.load_battle_import_equipment_locks(record_id)
                reports.append(self._export_report(
                    record=available[record_id],
                    account_name=account_name,
                    row_graph=row_graph,
                    target_condition=target_condition,
                    frozen_build=build,
                    build_edit=build_edit,
                    import_origin=import_origin,
                    import_locks=import_locks,
                    unavailable=unavailable,
                ))
        payload = {
            "format": {
                "name": BATTLE_REPORT_TRANSFER_FORMAT,
                "version": BATTLE_REPORT_TRANSFER_VERSION,
                "content_encoding": "UTF-8 JSON",
                "container": "NTEBR binary + zlib + SHA-256",
            },
            "bundle": {
                "bundle_id": str(uuid4()),
                "exported_at_utc": exported_at,
                "application_version": self._dependencies.application_version,
                "source_account_nickname": account_name,
                "user_database_schema_version": SCHEMA_VERSION,
                "static_data": static_metadata,
            },
            "manifest": {
                "report_count": len(reports),
                "included_sections": [
                    "raw_nte_core_summary",
                    "raw_nte_core_record",
                    "raw_axis_hits_in_source_order",
                    "time_stop_intervals",
                    "frozen_character_builds_and_equipment",
                    "saved_build_edit_and_activation_pointer",
                    "locked_equipment_at_export",
                    "battle_report_import_origin",
                    "user_confirmed_target_environment_and_witch_buff",
                    "saved_page_scope_and_range_when_available",
                    "export_time_inference_projection",
                    "portable_database_rows",
                ],
                "unavailable_sections": unavailable,
                "import_policy": {
                    "idempotency": "capture_operation_id + raw_summary_sha256",
                    "local_id_remap": ["battle_record_id", "capture_id"],
                    "discarded_foreign_pointers": ["source_inventory_snapshot_id"],
                    "active_loadout_pointer_imported": False,
                },
            },
            "reports": reports,
        }

        def validate_before_replace() -> None:
            self._ensure_current()
            if self._load_static_metadata() != static_metadata:
                raise StaleBattleReportTransferContextError("静态 dataset 已变化")

        byte_count = write_battle_report_bundle_atomic(
            target_path,
            payload,
            before_replace=validate_before_replace,
        )
        return BattleReportExportOutcome(
            report_count=len(reports),
            byte_count=byte_count,
        )

    def import_bundle(self, source_path: str | Path) -> BattleReportImportOutcome:
        self._ensure_current()
        static_metadata = self._load_static_metadata()
        payload = read_battle_report_bundle(source_path)
        reports = self._validate_bundle(payload)
        self._ensure_current()

        def validate_before_commit() -> None:
            self._ensure_current()
            if self._load_static_metadata() != static_metadata:
                raise StaleBattleReportTransferContextError("静态 dataset 已变化")

        bundle = payload["bundle"]
        imported_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._open_current_dao() as user_dao:
            result = user_dao.import_battle_report_transfer_rows(
                [
                    self._prepare_import_row_graph(
                        report,
                        bundle=bundle,
                        imported_at_utc=imported_at,
                    )
                    for report in reports
                ],
                before_commit=validate_before_commit,
            )
        return BattleReportImportOutcome(
            imported_record_ids=tuple(result["imported_battle_record_ids"]),
            skipped_existing_count=int(result["skipped_existing_count"]),
        )

    def _export_report(
        self,
        *,
        record: Mapping[str, Any],
        account_name: str,
        row_graph: dict[str, Any],
        target_condition: dict[str, Any] | None,
        frozen_build: dict[str, Any] | None,
        build_edit: dict[str, Any] | None,
        import_origin: dict[str, Any] | None,
        import_locks: Mapping[int, Mapping[str, Any]],
        unavailable: list[dict[str, Any]],
    ) -> dict[str, Any]:
        record_id = int(record["battle_record_id"])
        tables = row_graph["tables"]
        raw_record = self._decoded_raw_row(
            tables["battle_axis_capture"], "raw_record_json"
        )
        raw_hits = []
        for row in tables["battle_hit_evidence"]:
            raw_hits.append({
                "sequence_text": row.get("sequence_text"),
                "sequence_order": row.get("sequence_order"),
                "source_fields": dict(row),
                "raw_hit": self._decoded_json(row.get("raw_hit_json")),
            })
        projection = self._analysis_projection(record_id, unavailable)
        if not tables["battle_axis_capture"]:
            unavailable.append(self._unavailable(record_id, "nte_core_record_axis", "not_persisted"))
        if frozen_build is None:
            unavailable.append(self._unavailable(record_id, "frozen_build", "not_persisted"))
        if build_edit is None:
            unavailable.append(self._unavailable(record_id, "saved_build_edit", "not_created"))
        if target_condition is None:
            unavailable.append(self._unavailable(
                record_id,
                "user_confirmed_target_condition",
                "not_saved",
            ))
        if row_graph.get("saved_page_state") is None:
            unavailable.append(self._unavailable(
                record_id,
                "saved_page_scope_and_range",
                "only_the_current_history_record_has_page_state",
            ))
        unavailable.append(self._unavailable(
            record_id,
            "saved_derived_analysis",
            "current_schema_recomputes_analysis_and_does_not_persist_the_projection",
        ))
        locked_equipment = self._locked_equipment_at_export(
            frozen_build=frozen_build,
            build_edit=build_edit,
            import_locks=import_locks,
        )
        source_account_nickname = str(
            (import_origin or {}).get("source_account_nickname") or account_name
        )
        return {
            "battle_record_id": record_id,
            "source_account_nickname": source_account_nickname,
            "export_account_nickname": account_name,
            "identity": {
                "capture_operation_id": record.get("capture_operation_id"),
                "captured_at_utc": record.get("captured_at_utc"),
                "finalized_at_utc": record.get("finalized_at_utc"),
                "gameplay": self._gameplay_label(record),
                "scope": self._scope_label(record),
                "completeness": self._completeness_label(record),
                "retention_kind": record.get("retention_kind"),
            },
            "nte_core": {
                "summary": {
                    "payload_schema_version": record.get("payload_schema_version"),
                    "sha256": record.get("raw_summary_sha256"),
                    "raw": self._decoded_json(
                        tables["battle_record"][0].get("raw_summary_json")
                    ),
                },
                "record": raw_record,
                "axis": {
                    "capture": (
                        None
                        if not tables["battle_axis_capture"]
                        else dict(tables["battle_axis_capture"][0])
                    ),
                    "hits": raw_hits,
                    "time_stop_intervals": [
                        {
                            "source_fields": dict(row),
                            "raw_interval": self._decoded_json(
                                row.get("raw_interval_json")
                            ),
                        }
                        for row in tables["battle_time_stop_interval"]
                    ],
                },
            },
            "frozen_build": frozen_build,
            "saved_build_edit": build_edit,
            "locked_equipment_at_export": locked_equipment,
            "target_context": {
                "user_confirmed": target_condition,
                "automatic_inference_at_export": projection["target_inference"],
            },
            "selection_state": row_graph.get("saved_page_state"),
            "derived_analysis": projection,
            "database_rows": row_graph,
        }

    @staticmethod
    def _locked_equipment_at_export(
        *,
        frozen_build: Mapping[str, Any] | None,
        build_edit: Mapping[str, Any] | None,
        import_locks: Mapping[int, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if import_locks:
            rows = []
            for character_id, lock in sorted(import_locks.items()):
                equipment = list(lock.get("equipment") or ())
                rows.append({
                    "character_id": int(character_id),
                    "equipment_source_kind": "imported_locked",
                    "original_equipment_source_kind": str(
                        lock.get("equipment_source_kind") or "imported_locked"
                    ),
                    "equipment_sha256": battle_equipment_sha256(equipment),
                    "equipment": equipment,
                })
            return rows

        edited = {
            int(row["character_id"]): dict(row.get("profile") or {})
            for row in (build_edit or {}).get("characters") or ()
        }
        rows = []
        for character in (frozen_build or {}).get("characters") or ():
            character_id = int(character["character_id"])
            profile = edited.get(character_id, {})
            if "equipment_override" in profile:
                equipment = list(profile.get("equipment_override") or ())
                source_kind = "calibrated_build_edit"
            else:
                equipment = list(character.get("equipment") or ())
                source_kind = "frozen_battle_snapshot"
            rows.append({
                "character_id": character_id,
                "equipment_source_kind": source_kind,
                "equipment_sha256": battle_equipment_sha256(equipment),
                "equipment": equipment,
            })
        return rows

    @staticmethod
    def _prepare_import_row_graph(
        report: Mapping[str, Any],
        *,
        bundle: Mapping[str, Any],
        imported_at_utc: str,
    ) -> dict[str, Any]:
        row_graph = deepcopy(report["database_rows"])
        tables = row_graph["tables"]
        record_rows = tables.get("battle_record") or ()
        if len(record_rows) != 1:
            raise UserDataValidationError("战报包缺少唯一原始战报行")
        record_id = int(record_rows[0]["battle_record_id"])
        source_account = str(report.get("source_account_nickname") or "").strip()
        export_account = str(report.get("export_account_nickname") or "").strip()
        bundle_id = str(bundle.get("bundle_id") or "").strip()
        if not source_account or not export_account or not bundle_id:
            raise UserDataValidationError("战报包来源账号信息不完整")

        tables["battle_report_import_origin"] = [{
            "battle_record_id": record_id,
            "source_bundle_id": bundle_id,
            "source_account_nickname": source_account,
            "last_export_account_nickname": export_account,
            "contract_version": BATTLE_REPORT_TRANSFER_VERSION,
            "imported_at_utc": imported_at_utc,
        }]
        locks = []
        expected_character_ids = {
            int(row["character_id"])
            for row in tables.get("battle_character_build_snapshot") or ()
        }
        for raw in report.get("locked_equipment_at_export") or ():
            if not isinstance(raw, Mapping):
                raise UserDataValidationError("战报包固化配装行无效")
            character_id = int(raw.get("character_id") or 0)
            equipment = raw.get("equipment")
            if isinstance(equipment, (str, bytes)) or not isinstance(equipment, Sequence):
                raise UserDataValidationError("战报包固化配装必须是数组")
            equipment_rows = [dict(item) for item in equipment]
            digest = battle_equipment_sha256(equipment_rows)
            if digest != str(raw.get("equipment_sha256") or ""):
                raise UserDataValidationError("战报包固化配装 SHA-256 不匹配")
            source_kind = str(raw.get("equipment_source_kind") or "")
            if source_kind not in {
                "calibrated_build_edit",
                "frozen_battle_snapshot",
                "imported_locked",
            }:
                raise UserDataValidationError("战报包固化配装来源无效")
            locks.append({
                "battle_record_id": record_id,
                "character_id": character_id,
                "equipment_source_kind": source_kind,
                "equipment_sha256": digest,
                "locked_equipment_json": canonical_battle_equipment_json(
                    equipment_rows
                ),
                "created_at_utc": imported_at_utc,
            })
        lock_character_ids = [int(row["character_id"]) for row in locks]
        if (
            set(lock_character_ids) != expected_character_ids
            or len(lock_character_ids) != len(set(lock_character_ids))
        ):
            raise UserDataValidationError("战报包必须为每个角色提供唯一固化配装")
        tables["battle_character_import_equipment_lock"] = locks

        for row in tables.get("battle_character_build_edit") or ():
            try:
                profile = json.loads(str(row.get("raw_profile_json") or "{}"))
            except json.JSONDecodeError as error:
                raise UserDataValidationError("战报角色修改副本 JSON 无效") from error
            for key in (
                "equipment_override",
                "equipment_context_key",
                "equipment_context_title",
                "equipment_source_kind",
            ):
                profile.pop(key, None)
            row["raw_profile_json"] = json.dumps(
                profile,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
        return row_graph

    def _analysis_projection(
        self,
        record_id: int,
        unavailable: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            analysis = self._history_service.load_analysis(
                record_id,
                include_buff_inference=False,
                include_hit_replays=False,
                include_buff_counterfactuals=False,
            )
        except Exception:
            analysis = None
        if analysis is None:
            unavailable.append(self._unavailable(
                record_id,
                "automatic_target_inference",
                "insufficient_or_unavailable_axis_evidence",
            ))
            return {
                "persistence_kind": "derived_snapshot_or_recomputed_at_export",
                "model_versions": {},
                "target_inference": None,
                "target_life_projection": None,
            }
        target_inference: dict[str, Any] | None = {
            "environment_kind": analysis.detected_environment_kind,
            "environment_ref": analysis.detected_environment_ref,
            "environment_name": analysis.detected_environment_name,
            "difficulty_id": analysis.detected_environment_difficulty_id,
            "options": list(analysis.detected_environment_options),
            "outer_realm_floor": analysis.detected_outer_realm_floor,
            "source": analysis.target_identity_inference_source,
            "confidence": analysis.target_identity_inference_confidence,
            "basis": analysis.target_identity_inference_basis,
            "ambiguous": analysis.target_identity_inference_ambiguous,
            "alternatives": list(analysis.target_identity_inference_alternatives),
        }
        if not (
            target_inference["source"]
            or target_inference["environment_kind"]
            or target_inference["environment_ref"]
        ):
            unavailable.append(self._unavailable(
                record_id,
                "automatic_target_inference",
                "no_unique_match_or_user_condition_superseded_inference",
            ))
            target_inference = None
        return {
            "persistence_kind": "derived_snapshot_or_recomputed_at_export",
            "evidence_source": "immutable_battle_axis_and_frozen_build",
            "model_versions": {
                "formula": analysis.formula_model_version,
                "name_mapping": analysis.name_mapping_version,
                "action_inference": analysis.action_inference_version,
                "timeline_projection": analysis.timeline_projection_version,
                "target_vital": analysis.target_vital_model_version,
            },
            "target_inference": target_inference,
            "target_life_projection": {
                "targets": [asdict(item) for item in analysis.targets],
                "max_hp_events": [asdict(item) for item in analysis.max_hp_events],
                "estimated_max_hp_events": [
                    asdict(item) for item in analysis.estimated_max_hp_events
                ],
                "effective_damage": analysis.effective_damage,
                "effective_dps": analysis.effective_dps,
            },
        }

    @staticmethod
    def _validate_bundle(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        format_info = payload.get("format")
        bundle = payload.get("bundle")
        manifest = payload.get("manifest")
        reports = payload.get("reports")
        if not isinstance(format_info, Mapping) or (
            format_info.get("name") != BATTLE_REPORT_TRANSFER_FORMAT
            or format_info.get("version") != BATTLE_REPORT_TRANSFER_VERSION
        ):
            raise UserDataValidationError("战报包内部格式版本不受支持")
        if not isinstance(bundle, Mapping):
            raise UserDataValidationError("战报包缺少 bundle 元数据")
        if (
            not str(bundle.get("bundle_id") or "").strip()
            or not str(bundle.get("source_account_nickname") or "").strip()
            or not isinstance(bundle.get("static_data"), Mapping)
        ):
            raise UserDataValidationError("战报包 bundle 元数据不完整")
        source_schema = bundle.get("user_database_schema_version")
        if (
            isinstance(source_schema, bool)
            or not isinstance(source_schema, int)
            or source_schema not in SUPPORTED_SOURCE_USER_DATABASE_SCHEMAS
        ):
            supported = "、".join(
                str(value)
                for value in sorted(SUPPORTED_SOURCE_USER_DATABASE_SCHEMAS)
            )
            raise UserDataValidationError(
                f"战报包用户数据库结构版本不兼容（当前支持 {supported}）"
            )
        if isinstance(reports, (str, bytes)) or not isinstance(reports, list) or not reports:
            raise UserDataValidationError("战报包中没有可导入记录")
        if not isinstance(manifest, Mapping) or manifest.get("report_count") != len(reports):
            raise UserDataValidationError("战报包 manifest 记录数量不一致")
        normalized = []
        for report in reports:
            if (
                not isinstance(report, dict)
                or not str(report.get("source_account_nickname") or "").strip()
                or not str(report.get("export_account_nickname") or "").strip()
                or not isinstance(report.get("database_rows"), dict)
                or not isinstance(report.get("locked_equipment_at_export"), list)
            ):
                raise UserDataValidationError("战报包缺少可导入数据库行")
            normalized.append(report)
        return normalized

    def _open_current_dao(self) -> UserDataDao:
        self._ensure_current()
        path = self._dependencies.user_database_path
        if not path.is_file():
            raise UserDataError("冻结账号的用户数据库不存在")
        user_dao = UserDataDao(path)
        try:
            if (
                str(user_dao.profile()["account_id"]) != self._dependencies.account_id
                or not self._is_current()
            ):
                raise StaleBattleReportTransferContextError("战报导出账号上下文已经变化")
        except BaseException:
            user_dao.close()
            raise
        return user_dao

    def _load_static_metadata(self) -> dict[str, Any]:
        try:
            value = json.loads(self._dependencies.static_manifest_path.read_text(
                encoding="utf-8"
            ))
            database = value["database"]
            return {
                "dataset_id": str(database["dataset_id"]),
                "schema_version": int(database["schema_version"]),
                "sha256": str(database["sha256"]),
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise UserDataError("无法读取静态 dataset manifest") from error

    def _ensure_current(self) -> None:
        if not self._is_current():
            raise StaleBattleReportTransferContextError("战报导出账号上下文已经变化")

    def _is_current(self) -> bool:
        return bool(self._context_is_current(self._dependencies))

    @staticmethod
    def _normalize_report_ids(values: Sequence[int]) -> tuple[int, ...]:
        if isinstance(values, (str, bytes)):
            raise UserDataValidationError("所选战报 ID 必须是数组")
        result = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise UserDataValidationError("所选战报 ID 无效")
            if value not in result:
                result.append(value)
        if not result:
            raise UserDataValidationError("请至少选择一场战报")
        return tuple(result)

    @staticmethod
    def _gameplay_label(record: Mapping[str, Any]) -> str:
        if str(record.get("combat_context_kind")) != "abyss":
            return "非轨外场景"
        floor = record.get("abyss_floor")
        return "轨外之境" if floor is None else f"轨外之境 · 第 {int(floor)} 层"

    @staticmethod
    def _scope_label(record: Mapping[str, Any]) -> str:
        first = bool(record.get("has_first_half"))
        second = bool(record.get("has_second_half"))
        if first and second:
            return "上半场 + 下半场"
        if first:
            return "上半场"
        if second:
            return "下半场"
        return "整场 / 未识别半场"

    @staticmethod
    def _completeness_label(record: Mapping[str, Any]) -> str:
        complete = record.get("axis_complete")
        if complete is None:
            return "仅聚合摘要"
        stored = int(record.get("axis_stored_hits") or 0)
        return ("逐击完整" if bool(complete) else "逐击不完整") + f" · {stored} 条"

    @staticmethod
    def _cursor_label(status: Mapping[str, Any] | None) -> str:
        if status is None:
            return "无 axis cursor"
        next_cursor = str(status.get("next_cursor") or "").strip()
        total_hits = str(status.get("total_hits") or "").strip()
        if bool(status.get("axis_complete")):
            return "尾页已排空" + (f" · cursor {next_cursor}" if next_cursor else "")
        detail = f"cursor {next_cursor}" if next_cursor else "cursor 未保存"
        return detail + (f" / total {total_hits}" if total_hits else "")

    @staticmethod
    def _decoded_json(value: Any) -> Any:
        if value in (None, ""):
            return None
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return {"decode_error": "invalid_json"}

    @classmethod
    def _decoded_raw_row(cls, rows: Sequence[Mapping[str, Any]], field: str) -> Any:
        if not rows:
            return None
        row = rows[0]
        return {
            "source_fields": dict(row),
            "raw": cls._decoded_json(row.get(field)),
            "sha256": row.get("raw_record_sha256"),
        }

    @staticmethod
    def _unavailable(record_id: int, section: str, reason: str) -> dict[str, Any]:
        return {
            "battle_record_id": record_id,
            "section": section,
            "reason": reason,
        }


__all__ = [
    "BATTLE_REPORT_TRANSFER_FORMAT",
    "BATTLE_REPORT_TRANSFER_VERSION",
    "BattleReportTransferDependencies",
    "BattleReportTransferService",
    "StaleBattleReportTransferContextError",
]
