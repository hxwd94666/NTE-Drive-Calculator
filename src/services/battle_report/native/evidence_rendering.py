# 将原生逐击效果快照渲染为来源明确的只读详情，不计算 Buff 收益。
from __future__ import annotations

import json
import math

from src.domain.battle_report.timeline.events import BattleHitFieldEvidence, BattleNativeHitEvidence
from src.domain.localization.catalog import TextCatalog, localized_text
from src.integrations.preferences.locale_files import enum_text


_LOCALE_PREFIX = "battle_report.native_evidence."
_CRITICAL_STATE_LABELS = {
    "critical": "暴击", "non_critical": "未暴击",
    "not_applicable": "不适用", "unknown": "未知",
}
_CRITICAL_SOURCE_LABELS = {
    "unknown": "未知", "static_rule": "静态规则", "static_ge": "静态规则",
    "native_prediction": "客户端预测观测", "native_execution": "执行观测",
    "server_settlement": "服务器结算观测", "native_metadata": "原生观测",
}


def _text(
    text_catalog: TextCatalog | None,
    suffix: str,
    fallback: str,
    **values: object,
) -> str:
    """本文件用户可见文案统一挂在本模块前缀下；目录缺该 key 时逐字回退中文基线。"""

    return localized_text(text_catalog, f"{_LOCALE_PREFIX}{suffix}", fallback, **values)


def _label_table(
    text_catalog: TextCatalog | None, table: dict[str, str],
) -> dict[str, str]:
    """整表投影代码内状态说明；未注入目录时保留中文基线，服务层不读取资源文件。"""

    return table if text_catalog is None else enum_text(text_catalog, table)


def field_critical_label(
    evidence: BattleHitFieldEvidence, *, text_catalog: TextCatalog | None = None,
) -> str:
    """Render the core's decision without interpreting the captured payload."""
    state = _label_table(text_catalog, _CRITICAL_STATE_LABELS)[evidence.critical_state]
    source = evidence.critical_source
    kind = _label_table(text_catalog, _CRITICAL_SOURCE_LABELS).get(
        source,
        _text(text_catalog, "critical_source.unrecognized", "来源未识别"),
    )
    return _text(
        text_catalog,
        "field_critical.label",
        "{state}（{kind}，{confidence}）",
        state=state,
        kind=kind,
        confidence=evidence.critical_confidence,
    )


def render_field_evidence(
    evidence: BattleHitFieldEvidence | None, *, text_catalog: TextCatalog | None = None,
) -> str:
    if evidence is None:
        return _text(
            text_catalog,
            "field_evidence.missing",
            "字段判定：尚无独立证据结果；公式判定须按推断理解。",
        )
    return "\n".join((
        _text(text_catalog, "field_evidence.heading", "字段判定（分析核心）"),
        _text(
            text_catalog,
            "field_evidence.critical",
            "暴击：{critical}；来源：{source}",
            critical=field_critical_label(evidence, text_catalog=text_catalog),
            source=evidence.critical_source,
        ),
        _text(
            text_catalog,
            "field_evidence.attribute",
            "属性来源：{source}；置信度：{confidence}",
            source=evidence.damage_attribute_source,
            confidence=evidence.damage_attribute_confidence,
        ),
        *evidence.notes,
    ))


def _value(value: object, *, text_catalog: TextCatalog | None = None) -> str:
    if value is None or value == "":
        return _text(text_catalog, "value.unknown", "未知")
    if isinstance(value, bool):
        if value:
            return _text(text_catalog, "value.yes", "是")
        return _text(text_catalog, "value.no", "否")
    if isinstance(value, (str, int)):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    return _text(text_catalog, "value.unknown", "未知")


def _execution(
    value: object, *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    lines = [_text(text_catalog, "execution.heading", "【执行输入与输出（原始观测）】")]
    if value is None:
        return lines + [_text(
            text_catalog,
            "execution.missing",
            "本击没有已关联的执行输入记录；已有暴击或元素字段不代表完整输入已采集。",
        )]
    if not isinstance(value, dict):
        return lines + [_text(
            text_catalog, "execution.invalid", "执行证据格式无效，不能解释为没有修正项。")]
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        return lines + [_text(
            text_catalog,
            "execution.unsupported_version",
            "执行证据版本尚不支持解释，原文仍随逐击保留。",
        )]
    lines.extend([
        _text(
            text_catalog,
            "execution.identity",
            "执行 ID：{execution_id}；采集代次：{generation}",
            execution_id=_value(value.get("executionId"), text_catalog=text_catalog),
            generation=_value(value.get("generation"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "execution.coverage",
            "求值覆盖：{coverage}（未观测不等于没有参与项）",
            coverage=_value(value.get("evaluationCoverage"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "execution.name_resolution",
            "名称解析：{resolution}；FName 索引只作本次运行身份，不能直接还原标签名称。",
            resolution=_value(value.get("nameResolution"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "execution.spec_frozen",
            "以下是执行阶段冻结的 Spec 输入与输出；捕获定义、标签或 Spec 修正项存在，"
            "不等于该项实际被公式采用。",
        ),
        _text(
            text_catalog,
            "execution.dll_note",
            "DLL 不复算伤害；尚未观测的属性求值、条件过滤及来源链不作为已知事实。",
        ),
    ])
    phases = (
        ("before", _text(text_catalog, "execution.phase.before", "执行前")),
        ("after", _text(text_catalog, "execution.phase.after", "执行后")),
    )
    for key, label in phases:
        phase = value.get(key)
        lines.append(_text(
            text_catalog, "execution.phase_line", "{phase}：", phase=label))
        if not isinstance(phase, dict):
            lines.append(_text(
                text_catalog,
                "execution.phase_missing",
                "未取得该阶段记录，不能与另一时点拼成完整输入。",
            ))
        else:
            lines.append(_text(
                text_catalog,
                "execution.phase_time",
                "时间（Unix 微秒）：{observed}；状态：{status}",
                observed=_value(phase.get("observedUnixUs"), text_catalog=text_catalog),
                status=_value(phase.get("status"), text_catalog=text_catalog),
            ))
            for name, item in phase.items():
                if name in ("observedUnixUs", "status"):
                    continue
                # 只展示采样值，不把定义解析成已生效的加成。
                try:
                    raw = json.dumps(item, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":"))
                except (ValueError, TypeError):
                    raw = _text(
                        text_catalog,
                        "execution.phase_raw_invalid",
                        "格式无效；原始记录保留，不能按零或空列表解释",
                    )
                lines.append(_text(
                    text_catalog, "execution.phase_field", "{name}：{raw}",
                    name=name, raw=raw,
                ))
    return lines


def _snapshot(
    title: str,
    value: object,
    names: dict[str, str],
    *,
    text_catalog: TextCatalog | None = None,
) -> list[str]:
    lines = [_text(text_catalog, "snapshot.heading", "【{title}】", title=title)]
    if value is None:
        return lines + [_text(
            text_catalog, "snapshot.missing", "未取得快照；不能判断该侧是否存在 Buff。")]
    if not isinstance(value, dict) or not isinstance(value.get("effects"), list):
        return lines + [_text(
            text_catalog, "snapshot.invalid", "快照格式无效；不能解释为空 Buff 列表。")]
    effects = value["effects"]
    complete = value.get("complete") is True
    live = value.get("ended") is False
    lines.extend([
        _text(
            text_catalog,
            "snapshot.actor",
            "对象：{actor_name} / {actor}",
            actor_name=_value(value.get("actorName"), text_catalog=text_catalog),
            actor=_value(value.get("actor"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "snapshot.identity",
            "快照 ID：{snapshot_id}；对象索引/代次：{actor_index}/{actor_serial}",
            snapshot_id=_value(value.get("id"), text_catalog=text_catalog),
            actor_index=_value(value.get("actorIndex"), text_catalog=text_catalog),
            actor_serial=_value(value.get("actorSerial"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "snapshot.times",
            "快照内容版本时间（Unix 微秒）：{observed}；世界时间（秒）：{world_time}",
            observed=_value(value.get("observedUs"), text_catalog=text_catalog),
            world_time=_value(value.get("worldSeconds"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "snapshot.state",
            "快照列表完整：{complete}；状态：{status}；对象已结束：{ended}",
            complete=(
                _text(text_catalog, "value.yes", "是") if complete
                else _text(text_catalog, "snapshot.unconfirmed", "未确认")
            ),
            status=_value(value.get("status"), text_catalog=text_catalog),
            ended=_value(value.get("ended"), text_catalog=text_catalog),
        ),
    ])
    if not effects:
        if complete and live:
            return lines + [_text(
                text_catalog,
                "snapshot.empty_complete",
                "已采集有效空列表：该对象本次快照中没有效果条目。",
            )]
        return lines + [_text(
            text_catalog,
            "snapshot.empty_partial",
            "本次返回 0 条；快照不完整或对象已结束，不能认定没有 Buff。",
        )]
    lines.append(_text(
        text_catalog,
        "snapshot.effects_count",
        "观察到 {count} 条效果（存在记录不等于对本击生效）：",
        count=len(effects),
    ))
    for index, effect in enumerate(effects, 1):
        if not isinstance(effect, dict):
            lines.append(_text(
                text_catalog, "snapshot.effect_invalid", "{index}. 条目格式无效",
                index=index,
            ))
            continue
        name = names.get(str(effect.get('key', '')))
        if name:
            lines.append(_text(
                text_catalog,
                "snapshot.effect_name_catalog",
                "{index}. {name}（静态目录名称）",
                index=index,
                name=name,
            ))
        else:
            lines.append(_text(
                text_catalog,
                "snapshot.effect_name",
                "{index}. {name}",
                index=index,
                name=_value(
                    effect.get('name') or effect.get('key'), text_catalog=text_catalog),
            ))
        lines.extend([
            _text(
                text_catalog,
                "snapshot.effect_definition",
                "   定义：{effect_key}；实例：{instance}",
                effect_key=_value(effect.get('key'), text_catalog=text_catalog),
                instance=_value(effect.get('instanceKey'), text_catalog=text_catalog),
            ),
            _text(
                text_catalog,
                "snapshot.effect_source",
                "   来源：{source}；种类：{kind}",
                source=_value(effect.get('source'), text_catalog=text_catalog),
                kind=_value(effect.get('kind'), text_catalog=text_catalog),
            ),
            _text(
                text_catalog,
                "snapshot.effect_stacks",
                "   层数：{stacks}；等级：{level}；已抑制：{inhibited}",
                stacks=_value(effect.get('stacks'), text_catalog=text_catalog),
                level=_value(effect.get('level'), text_catalog=text_catalog),
                inhibited=_value(effect.get('inhibited'), text_catalog=text_catalog),
            ),
            _text(
                text_catalog,
                "snapshot.effect_duration",
                "   持续时间（秒）：{duration}；开始世界时间：{start_world_time}",
                duration=_value(effect.get('duration'), text_catalog=text_catalog),
                start_world_time=_value(
                    effect.get('startWorldTime'), text_catalog=text_catalog),
            ),
        ])
        if effect.get("description"):
            lines.append(_text(
                text_catalog,
                "snapshot.effect_description",
                "   描述：{description}",
                description=_value(effect.get('description'), text_catalog=text_catalog),
            ))
    return lines


def render_native_evidence(
    evidence: BattleNativeHitEvidence | None, *, text_catalog: TextCatalog | None = None,
) -> str:
    """Display immutable observations without joining inferred identity or modifier state."""
    heading = _text(text_catalog, "native.heading", "原生 DLL 逐击证据")
    if evidence is None:
        return heading + "\n" + _text(
            text_catalog,
            "native.missing",
            "未采集原生逐击证据（旧战报或该击没有原生证据）。",
        )
    try:
        payload = json.loads(evidence.payload_json)
    except (ValueError, TypeError):
        return heading + "\n" + _text(
            text_catalog, "native.invalid", "原生证据格式无效，不能解释为空列表。")
    if not isinstance(payload, dict) or not isinstance(payload.get("rawHit"), dict):
        return heading + "\n" + _text(
            text_catalog, "native.invalid", "原生证据格式无效，不能解释为空列表。")
    hit = payload["rawHit"]
    critical_known = hit.get("criticalKnown") is True or hit.get("metadataKnown") is True
    element_known = hit.get("damageTypeKnown") is True or hit.get("metadataKnown") is True
    unknown = _text(text_catalog, "value.unknown", "未知")
    critical = _value(hit.get("critical"), text_catalog=text_catalog) if critical_known else unknown
    lines = [heading,
             _text(
                 text_catalog,
                 "native.source",
                 "提供方：{provider}；采集场次：{capture}；原生逐击 ID：{hit_id}",
                 provider=_value(payload.get('providerId'), text_catalog=text_catalog),
                 capture=_value(payload.get('captureId'), text_catalog=text_catalog),
                 hit_id=_value(payload.get('hitId'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.damage_source",
                 "伤害来源：{source}；关联依据：{association}",
                 source=_value(hit.get('source'), text_catalog=text_catalog),
                 association=_value(hit.get('association'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.capture_stage",
                 "伤害采样阶段：{damage_stage}；效果采样阶段：{effects_stage}",
                 damage_stage=_value(hit.get('captureStage'), text_catalog=text_catalog),
                 effects_stage=_value(hit.get('effectsStage'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.effect_read_times",
                 "本击来源侧 / 目标侧效果读取时间（Unix 微秒）：{attacker} / {victim}",
                 attacker=_value(
                     hit.get('attackerEffectsObservedUnixUs'), text_catalog=text_catalog),
                 victim=_value(
                     hit.get('victimEffectsObservedUnixUs'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.settlement_damage",
                 "正式结算伤害：{damage}；GE：{effect_name}",
                 damage=_value(hit.get('damage'), text_catalog=text_catalog),
                 effect_name=_value(hit.get('gameplayEffectName'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.critical",
                 "原生暴击：{critical}；证据来源：{source}",
                 critical=critical,
                 source=_value(hit.get('criticalSource'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.flags",
                 "原生字段标志：metadataKnown={metadata_known}；"
                 "criticalKnown={critical_known}；damageTypeKnown={damage_type_known}；"
                 "正式显示类型：{display_type}",
                 metadata_known=_value(hit.get('metadataKnown'), text_catalog=text_catalog),
                 critical_known=_value(hit.get('criticalKnown'), text_catalog=text_catalog),
                 damage_type_known=_value(hit.get('damageTypeKnown'), text_catalog=text_catalog),
                 display_type=_value(hit.get('displayType'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.damage_type",
                 "伤害类型原始枚举：{damage_type}；证据来源：{source}",
                 damage_type=(
                     _value(hit.get('damageType'), text_catalog=text_catalog)
                     if element_known else unknown
                 ),
                 source=_value(hit.get('damageTypeSource'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.settlement_components",
                 "结算标识：{settlement_key}；分量：{ordinal}；末分量：{last_target_component}",
                 settlement_key=_value(hit.get('settlementKey'), text_catalog=text_catalog),
                 ordinal=_value(hit.get('componentOrdinal'), text_catalog=text_catalog),
                 last_target_component=_value(
                     hit.get('lastTargetComponent'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.victim_hp",
                 "服务器结算生命：{hp}；采样阶段：{stage}",
                 hp=_value(hit.get('victimHp'), text_catalog=text_catalog),
                 stage=_value(hit.get('victimHpStage'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.victim_max_hp",
                 "观测最大生命：{hp}；采样阶段：{stage}",
                 hp=_value(hit.get('victimMaxHp'), text_catalog=text_catalog),
                 stage=_value(hit.get('victimMaxHpStage'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.victim_hp_times",
                 "生命 / 上限采样时间（Unix 微秒）：{hp_time} / {max_hp_time}",
                 hp_time=_value(hit.get('victimHpObservedUnixUs'), text_catalog=text_catalog),
                 max_hp_time=_value(
                     hit.get('victimMaxHpObservedUnixUs'), text_catalog=text_catalog),
             ),
             _text(
                 text_catalog,
                 "native.hp_note",
                 "生命与上限按各自采样阶段展示，不拼成同一时点的前后血量。",
             ),
             _text(
                 text_catalog,
                 "native.completeness_note",
                 "完整性：仅描述当时读到的对象效果；全场来源覆盖未知，"
                 "时停完整性由本场时钟证据单独说明。",
             ),
             _text(
                 text_catalog,
                 "native.effects_note",
                 "效果存在、层数和采样时刻是观察值；不据此认定增伤比例、对本击生效或完整队伍 Buff。",
             )]
    # 原始来源和目标保留采样身份，不随重放面板归属改变。
    names = dict(evidence.static_names)
    lines.extend(_execution(hit.get("executionEvidence"), text_catalog=text_catalog))
    lines.extend(_snapshot(
        _text(text_catalog, "snapshot.attacker.title", "辅助：伤害来源侧效果"),
        hit.get("attackerEffects"),
        names,
        text_catalog=text_catalog,
    ))
    lines.extend(_snapshot(
        _text(text_catalog, "snapshot.victim.title", "辅助：受击目标侧效果"),
        hit.get("victimEffects"),
        names,
        text_catalog=text_catalog,
    ))
    return "\n".join(lines)
