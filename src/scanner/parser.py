"""OCR text normalization and equipment object synthesis."""

import os
import json
import re
import difflib
import time
from typing import Dict, Any, List
import hashlib
from src.utils.logger import logger
from src.utils.exceptions import ConfigMissingError
from src.utils.name_resolver import resolve_name
from src.models.equipment import Drive, Tape


class DriveDataParser:
    """数据清洗与推理引擎：OCR 原文解析、品质逆推、词条提取"""
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self.stat_pattern = re.compile(r"([\u4e00-\u9fa5]+?)(?:增加|提升)?([0-9\.]+)(%?)")
        self.REAL_SETS_WHITE_LIST = self._load_sets_from_json()
        self.GOLD_BASE_VALUES, self.TAPE_MAIN_STATS_POOL = self._load_stats_from_json()

    def _generate_uid(self, prefix: str, **kwargs) -> str:
        """根据装备特征生成唯一 MD5 指纹"""
        stable_str = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
        hash_val = hashlib.md5(stable_str.encode('utf-8')).hexdigest()[:12]
        return f"{prefix}_{hash_val}"

    def _load_sets_from_json(self) -> List[str]:
        sets_path = os.path.join(self.config_dir, "sets.json")
        try:
            with open(sets_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                loaded_sets = list(data.get("sets", {}).keys())
                logger.info(f"Parser 成功加载 {len(loaded_sets)} 个套装配置。")
                return loaded_sets
        except Exception:
            logger.warning(f"无法读取 sets.json，使用兜底套装。")
            return ["森林萤火之心", "迪亚波罗斯", "音速蓝刺猬", "守卫王国", "失落光芒"]

    def _load_stats_from_json(self) -> tuple[Dict[str, float], List[str]]:
        stats_path = os.path.join(self.config_dir, "stats.json")
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                gold_bases = data.get("gold_base_values", {})
                tape_mains = data.get("tape_main_stats_pool", [])
                logger.info(f"Parser 成功加载数值引擎。")
                return gold_bases, tape_mains
        except Exception as e:
            raise ConfigMissingError(f"找不到或无法解析 {stats_path}: {e}")

    def _clean_stats(self, raw_texts: List[str]) -> Dict[str, float]:
        clean_stats = {}
        joined_text = "".join(raw_texts).replace(" ", "")

        for match in self.stat_pattern.finditer(joined_text):
            stat_name = match.group(1)
            stat_value = float(match.group(2))
            is_percent = match.group(3) == "%"

            final_name = f"{stat_name}%" if is_percent else stat_name
            clean_stats[final_name] = stat_value

        return clean_stats

    def _fuzzy_match_set_name(self, raw_text: str) -> str:
        if not raw_text:
            return "未知套装"
        clean_text = re.sub(r'[^\u4e00-\u9fa5]', '', raw_text)
        for skip_word in ["型", "驱动", "卡带", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"]:
            clean_text = clean_text.replace(skip_word, "")

        resolved = resolve_name(clean_text, self.REAL_SETS_WHITE_LIST, cutoff=0.2)
        if resolved:
            return resolved
        matches = difflib.get_close_matches(clean_text, self.REAL_SETS_WHITE_LIST, n=1, cutoff=0.2)
        return matches[0] if matches else "未知套装"

    def _fuzzy_match_tape_main(self, raw_text: str) -> str:
        clean_text = re.sub(r'[^\u4e00-\u9fa5]', '', raw_text)
        matches = difflib.get_close_matches(clean_text, self.TAPE_MAIN_STATS_POOL, n=1, cutoff=0.4)
        if matches:
            return matches[0]
        else:
            return "未知主词条"

    # ==========================================
    # 通过副词条数值逆推品质
    # ==========================================
    def _infer_quality(self, sub_stats_dict: Dict[str, float], grid_equivalent: int) -> str:
        """
        逆推公式：实际数值 / (一格金数值 * 物理格数)
        结果约 1.0 为金，0.8 为紫，0.6 为蓝
        """
        if not sub_stats_dict:
            return "Gold"  # 兜底

        # 取第一个已知副词条进行验算
        for stat_name, actual_val in sub_stats_dict.items():
            base_gold_val = self.GOLD_BASE_VALUES.get(stat_name)
            if base_gold_val:
                expected_gold_val = base_gold_val * grid_equivalent
                ratio = actual_val / expected_gold_val

                if ratio >= 0.9:
                    return "Gold"
                elif ratio >= 0.7:
                    return "Purple"
                else:
                    return "Blue"

        # 无已知词条可验算，兜底默认金
        return "Gold"

    def _calculate_drive_main_stats(self, area: int, quality: str) -> Dict[str, float]:
        """推导驱动满级主词条潜力"""
        base_atk = 21.0
        base_hp = 280.0

        multiplier = 1.0
        if quality == "Purple":
            multiplier = 0.8
        elif quality == "Blue":
            multiplier = 0.6

        return {
            "攻击力": round(base_atk * area * multiplier, 2),
            "生命值": round(base_hp * area * multiplier, 2)
        }

    def synthesize_drive(self, shape_id: str, raw_sub_texts: List[str]) -> Drive:
        """驱动管线 - 返回 Drive 模型"""
        area = 2
        match_area = re.search(r"(\d+)", shape_id)
        if match_area:
            area = int(match_area.group(1))

        sub_stats_dict = self._clean_stats(raw_sub_texts)
        quality = self._infer_quality(sub_stats_dict, grid_equivalent=area)
        main_stats_dict = self._calculate_drive_main_stats(area, quality)

        uid = self._generate_uid("drive", shape=shape_id, quality=quality, main=main_stats_dict, sub=sub_stats_dict)
        return Drive(
            uid=uid,
            item_type="drive",
            shape_id=shape_id,
            area=area,
            quality=quality,
            main_stats=main_stats_dict,
            sub_stats=sub_stats_dict
        )

    def synthesize_tape(self, set_name: str, raw_main_texts: List[str], raw_sub_texts: List[str]) -> Tape:
        """卡带管线"""
        raw_main_joined = "".join(raw_main_texts)
        main_stat_name = self._fuzzy_match_tape_main(raw_main_joined)

        sub_stats_dict = self._clean_stats(raw_sub_texts)
        quality = self._infer_quality(sub_stats_dict, grid_equivalent=10)

        uid = self._generate_uid("tape", set_name=set_name, quality=quality, main=main_stat_name, sub=sub_stats_dict)
        return Tape(
            uid=uid,
            item_type="tape",
            shape_id="TAPE_15",
            area=15,
            quality=quality,
            set_name=set_name,
            main_stats=main_stat_name,
            sub_stats=sub_stats_dict
        )
