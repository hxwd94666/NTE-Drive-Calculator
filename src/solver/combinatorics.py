"""Shape combination generation used before board placement solving."""

from typing import List, Dict
from src.models.equipment import DriveShape

class PuzzleCombinatorics:
    def __init__(self, shapes_db: Dict[str, DriveShape]):
        self.shapes_db = shapes_db
        # Exclude virtual tape shape from physical puzzle
        self.shape_list = [shape for shape in shapes_db.values() if shape.shape_id != "TAPE_15"]

    def generate_piece_combinations(self, set_shapes: List[str], extra_label: str) -> List[List[str]]:
        set_area = sum(self.shapes_db[shape_id].area for shape_id in set_shapes)
        remain_area = 20 - set_area

        if remain_area < 0:
            raise ValueError(f"套装总面积 ({set_area}格) 已超过底盘上限 (20格)！")
        if remain_area == 0:
            return [[]]

        all_valid_combos = []

        def find_combinations(target_area: int, current_combo: List[str], start_idx: int):
            if target_area == 0:
                all_valid_combos.append(list(current_combo))
                return
            for i in range(start_idx, len(self.shape_list)):
                shape = self.shape_list[i]
                if target_area - shape.area >= 0:
                    current_combo.append(shape.shape_id)
                    find_combinations(target_area - shape.area, current_combo, i)
                    current_combo.pop()

        find_combinations(remain_area, [], 0)

        if not all_valid_combos:
            raise ValueError(f"无法用现有的形状库凑出刚好等于 {remain_area} 格的散件组合！")

        combo_scores = []
        for combo in all_valid_combos:
            extra_count = sum(1 for shape_id in combo if self.shapes_db[shape_id].label == extra_label)
            combo_scores.append((extra_count, combo))

        max_extra_count = max(score[0] for score in combo_scores)
        best_combos = [score[1] for score in combo_scores if score[0] == max_extra_count]
        best_combos.sort(key=len)

        return best_combos
