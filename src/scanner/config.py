# 定义扫描识别所需的区域和阈值配置。
"""Resolution-aware scan regions and coordinate scaling helpers."""

from src.scanner.window_capture import scale_region


class ScannerConfig:
    BASE_WIDTH = 2560
    BASE_HEIGHT = 1440

    REGIONS_2K = {
        "identity_check": (2000, 330, 2250, 377),

        "drive_sub_stats": (1801, 819, 2200, 1063),
        "drive_shape_icon": (1762, 350, 2012, 590),

        "tape_main_stat": (1813, 660, 2200, 704),
        "tape_sub_stats": (1811, 758, 2200, 1063)
    }

    @classmethod
    def get_scaled_regions(
        cls,
        target_width: int,
        target_height: int,
        preserve_aspect: bool = True,
        content_rect: tuple[int, int, int, int] | None = None,
    ) -> dict:
        scaled_regions = {}
        for region_name, region in cls.REGIONS_2K.items():
            scaled_regions[region_name] = scale_region(
                region,
                target_width,
                target_height,
                (cls.BASE_WIDTH, cls.BASE_HEIGHT),
                preserve_aspect=preserve_aspect,
                content_rect=content_rect,
            )

        return scaled_regions

    @classmethod
    def get_region_profiles(cls, target_width: int, target_height: int) -> list[tuple[str, dict]]:
        """Return the single top-aligned 16:9 coordinate profile."""
        base_aspect = cls.BASE_WIDTH / cls.BASE_HEIGHT
        target_aspect = target_width / max(1, target_height)
        if target_aspect < base_aspect:
            content_width = target_width
            content_height = min(target_height, round(target_width / base_aspect))
            content_rect = (0, 0, content_width, content_height)
        else:
            content_height = target_height
            content_width = min(target_width, round(target_height * base_aspect))
            left = round((target_width - content_width) / 2)
            content_rect = (left, 0, content_width, content_height)
        return [("top_16_9", cls.get_scaled_regions(target_width, target_height, content_rect=content_rect))]
