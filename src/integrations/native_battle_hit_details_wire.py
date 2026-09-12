# 校验原生详情共享表，逐击只保留索引，点击时再组合已有只读对象。
from types import MappingProxyType
import math

from src.domain.battle_hit_details import BattleHitDetailTables, BattleHitDetailLookup, BattlePageHitDetails
from src.domain.battle_report import BattleProjectedBuffModifier, BattleBuffProjectionDecision
from src.integrations.nte_analysis_core import NativeAnalysisError


def decode_hit_details(value, analysis, candidate):
    if value is None:
        return None
    try:
        if not isinstance(value, dict) or value.get("result_encoding") not in {"interned_v1", "interned_v2"}:
            raise ValueError
        evidence_fields = value["result_encoding"] == "interned_v2"
        def table(name):
            rows = value[name]
            if not isinstance(rows, list):
                raise ValueError
            return rows

        strings = tuple(table("strings"))
        if any(type(s) is not str for s in strings):
            raise ValueError

        def ref(table, index):
            if type(index) is not int or not 0 <= index < len(table):
                raise ValueError
            return table[index]

        def indices(table, values):
            if not isinstance(values, list):
                raise ValueError
            for index in values:
                ref(table, index)
            return tuple(values)

        def texts(values):
            return tuple(strings[i] for i in indices(strings, values))

        modifiers = []
        for row in table("modifiers"):
            if not isinstance(row,list) or len(row) != 6 or type(row[1]) not in (int, float) or not math.isfinite(row[1]):
                raise ValueError
            modifiers.append(BattleProjectedBuffModifier(
                ref(strings,row[0]),float(row[1]),texts(row[2]),texts(row[3]),
                ref(strings,row[4]),ref(strings,row[5])))
        decisions = []
        for row in table("decisions"):
            if not isinstance(row,list) or len(row) != (7 if evidence_fields else 5) or ref(strings,row[2]) not in {"applied","not_applied","unresolved"}:
                raise ValueError
            stacks = row[5] if evidence_fields else None
            confidence = ref(strings, row[6]) if evidence_fields else ""
            if (stacks is not None and (type(stacks) is not int or stacks < 0)) or confidence not in {"", "未知", "未解析", "低", "中", "高"}:
                raise ValueError
            decisions.append(BattleBuffProjectionDecision(
                ref(strings,row[0]),ref(strings,row[1]),ref(strings,row[2]),texts(row[3]),texts(row[4]),stacks,confidence))
        projections = []
        for row in table("projections"):
            if not isinstance(row,list) or len(row) != 7:
                raise ValueError
            ref(strings,row[0])
            ref(strings,row[5])
            projections.append((row[0],indices(modifiers,row[1]),indices(strings,row[2]),
                                indices(strings,row[3]),indices(strings,row[4]),row[5],indices(decisions,row[6])))
        jobs = {}
        for row in table("results"):
            if not isinstance(row,dict) or set(row) != {"job_id","projection_index"} or type(row["job_id"]) is not str:
                raise ValueError
            section, kind, event_id = row["job_id"].split(":",2)
            projection = ref(projections,row["projection_index"])
            if (section not in {"analysis","candidate"} or kind not in {"raw","formula"}
                    or strings[projection[0]] != event_id or row["job_id"] in jobs):
                raise ValueError
            jobs[row["job_id"]] = row["projection_index"]
        tables = BattleHitDetailTables(strings,tuple(modifiers),tuple(decisions),tuple(projections),MappingProxyType(jobs))
        return BattlePageHitDetails(BattleHitDetailLookup(tables,"analysis",analysis),
                                    BattleHitDetailLookup(tables,"candidate",candidate))
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        raise NativeAnalysisError("分析核心逐击详情共享表无效") from error
