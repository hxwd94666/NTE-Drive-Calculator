# 冻结每批截图的坐标布局，保留有效内容区与原有缩放规则。
from __future__ import annotations

from dataclasses import dataclass

from src.integrations.vision.window_capture import game_content_rect, scale_region


@dataclass(frozen=True)
class RecognitionLayout:
    base_size: tuple[int, int]
    regions: tuple[tuple[str, tuple[int, int, int, int]], ...]

    def get_scaled_regions(self, target_width: int, target_height: int,
                           preserve_aspect: bool = True,
                           content_rect: tuple[int, int, int, int] | None = None) -> dict:
        # 返回私有投影；一张图片的裁切调整不污染后续图片或其他任务。
        return {name: scale_region(region, target_width, target_height, self.base_size,
                    preserve_aspect=preserve_aspect, content_rect=content_rect)
                for name, region in self.regions}

    def get_region_profiles(self, target_width: int, target_height: int) -> list[tuple[str, dict]]:
        content_rect = game_content_rect(target_width, target_height, self.base_size)
        return [("top_16_9", self.get_scaled_regions(target_width, target_height,
                                                   content_rect=content_rect))]


class ScannerConfig:
    BASE_WIDTH = 2560
    BASE_HEIGHT = 1440
    REGIONS_2K = {
        "identity_check": (2000, 330, 2250, 377),
        "drive_sub_stats": (1801, 819, 2200, 1063),
        "drive_shape_icon": (1762, 350, 2012, 590),
        "tape_main_stat": (1813, 660, 2200, 704),
        "tape_sub_stats": (1811, 758, 2200, 1063),
    }

    @classmethod
    def freeze(cls) -> RecognitionLayout:
        return RecognitionLayout((cls.BASE_WIDTH, cls.BASE_HEIGHT),
                                 tuple((name, tuple(region)) for name, region in cls.REGIONS_2K.items()))

    @classmethod
    def get_scaled_regions(cls, target_width: int, target_height: int,
                           preserve_aspect: bool = True,
                           content_rect: tuple[int, int, int, int] | None = None) -> dict:
        return cls.freeze().get_scaled_regions(target_width, target_height, preserve_aspect, content_rect)

    @classmethod
    def get_region_profiles(cls, target_width: int, target_height: int) -> list[tuple[str, dict]]:
        return cls.freeze().get_region_profiles(target_width, target_height)
