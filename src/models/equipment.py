# 定义驱动盘和音擎的数据模型。
"""Typed equipment models shared by scanning, scoring, and allocation."""

from pydantic import BaseModel, Field, model_validator
from typing import Dict, Literal, List


class DriveShape(BaseModel):
    shape_id: str
    label: str
    matrix: List[List[int]]
    area: int
    description: str = ""

    @model_validator(mode='after')
    def validate_matrix_and_area(self):
        if self.shape_id == "TAPE_15":
            return self

        calculated_area = sum(cell for row in self.matrix for cell in row)
        if calculated_area != self.area:
            raise ValueError(f"形状 {self.shape_id} 数据异常: 标定面积为 {self.area}, 实际矩阵面积为 {calculated_area}")
        return self


class BaseEquipment(BaseModel):
    uid: str
    item_type: Literal["drive", "tape"]
    quality: Literal["Gold", "Purple", "Blue"]
    area: int
    sub_stats: Dict[str, float] = Field(default_factory=dict)

    role_scores: Dict[str, float] = Field(default_factory=dict)
    max_score: float = Field(default=0.0)
    is_mvp: bool = Field(default=False)
    pick_order: int = Field(default=0)


class Drive(BaseEquipment):
    item_type: Literal["drive"] = "drive"
    shape_id: str
    set_name: str = "未知套装"
    main_stats: Dict[str, float] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_drive_rules(self):
        if len(self.main_stats) != 2:
            raise ValueError(f"驱动 {self.uid} 必须且仅包含 2 条主词条")
        if len(self.sub_stats) > 4:
            raise ValueError(f"驱动 {self.uid} 副词条不能超过 4 条")
        if self.area not in [1, 2, 3, 4]:
            raise ValueError(f"驱动 {self.uid} 面积异常: 物理驱动面积只能是 1~4")
        return self


class Tape(BaseEquipment):
    item_type: Literal["tape"] = "tape"
    shape_id: str = "TAPE_15"
    set_name: str
    main_stats: str

    # Tape main_stats is a plain text label, not a numeric dict
    @model_validator(mode='after')
    def validate_cartridge_rules(self):
        if self.area != 15:
            raise ValueError(f"卡带 {self.uid} 乘区必须固定为 15 格当量")
        return self
