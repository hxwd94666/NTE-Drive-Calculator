# 用原版面板纹理合成本地配装分享图，保留原布局并移除伤害测算区域。
"""NTEUID texture layout adapted to immutable Calc display values.

Upstream presentation: Wuyi / NTEUID. See the project's unified NOTICE.
No bot runtime, network request, login, or damage calculation is used here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import CancelledError
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from src.domain.loadout_share import LoadoutSharePanel, ShareEquipment
from src.integrations.bundled_resources import bundled_root
from src.integrations.loadout_share_assets import share_asset, share_template

WIDTH = 1100
BODY_TOP = 248
GAP = 28
ATTRIBUTE_ROW_CAPACITY = 10
SKILLS_PER_ROW = 4
HEADER_CONTACT_LINES = ("使用教程：B站飞行库冯", "Github：NTE-Drive-Calculator")
WHITE = "#ffffff"
MUTED = "#d2d0e4"
HIGHLIGHT = (255, 176, 74)
HIGHLIGHT_LOCKED = (255, 200, 130)


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    path = share_asset("fonts", "panel")
    if path is None:
        raise FileNotFoundError("分享面板缺少 MiSans 字体资源")
    font = ImageFont.truetype(str(path), size)
    # Official, unmodified MiSans Bold 4.009; same design as VF's 630 instance.
    return font


def _text(draw, xy, text: str, size: int, fill=WHITE, *, width=None, anchor="lm", shrink=False) -> None:
    font = _font(size)
    if shrink and width is not None:
        while size > 16 and draw.textlength(text, font=font) > width:
            size -= 1
            font = _font(size)
    if width is not None and draw.textlength(text, font=font) > width:
        while text and draw.textlength(text + "…", font=font) > width:
            text = text[:-1]
        text += "…"
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _image(path: Path | None) -> Image.Image | None:
    if path is None or not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            if source.width * source.height > 24_000_000:
                return None
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def _texture(name: str, size: tuple[int, int] | None = None) -> Image.Image:
    path = share_template(name)
    image = _image(path)
    if image is None:
        raise ValueError(f"分享面板模板图片损坏：{name}")
    return image.resize(size, Image.Resampling.LANCZOS) if size else image


def _paste(canvas, name: str, xy, size=None) -> None:
    canvas.alpha_composite(_texture(name, size), xy)


def _icon(canvas, path, box) -> bool:
    image = _image(path)
    if image is None:
        return False
    x, y, w, h = box
    image = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
    canvas.alpha_composite(image, (x + (w - image.width) // 2, y + (h - image.height) // 2))
    return True


def _header_strip(canvas: Image.Image) -> Image.Image:
    """Reuse the panel's texture under a violet tint and restrained neon patterns."""
    strip = canvas.crop((218, 52, 1080, 212)).convert("RGBA")
    tint = Image.new("RGBA", strip.size)
    gradient = ImageDraw.Draw(tint)
    for x in range(strip.width):
        t = x / (strip.width - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip((20, 15, 33), (36, 19, 48)))
        gradient.line((x, 0, x, 159), fill=(*color, 182))
    strip.alpha_composite(tint)
    ornament = Image.new("RGBA", strip.size)
    art = ImageDraw.Draw(ornament)
    art.line(((-80, 167), (242, 151), (387, -26)), fill=(224, 57, 147, 90), width=27)
    art.line(((540, 183), (737, -24), (925, -24)), fill=(77, 209, 228, 82), width=20)
    strip.alpha_composite(ornament.filter(ImageFilter.GaussianBlur(20)))
    ornament = Image.new("RGBA", strip.size)
    art = ImageDraw.Draw(ornament)
    # Angular layers and halftone dots echo the panel, without a separate bright tile.
    for x, color in ((278, (221, 88, 171, 20)), (554, (98, 181, 216, 20)), (738, (195, 120, 221, 22))):
        art.polygon(((x - 104, 160), (x + 30, 0), (x + 81, 0), (x - 53, 160)), fill=color)
        art.line(((x - 104, 160), (x + 30, 0)), fill=(*color[:3], 48), width=1)
    for y in range(8, 54, 12):
        for x in range(10, 470, 12):
            alpha = round(32 * (1 - x / 480) * (1 - y / 64))
            art.ellipse((x, y, x + 3, y + 3), fill=(229, 120, 194, alpha))
    art.line(((18, 140), (297, 140), (321, 151), (452, 151)), fill=(220, 101, 179, 105), width=1)
    art.line(((483, 17), (574, 17), (588, 8), (819, 8)), fill=(138, 210, 226, 100), width=1)
    art.line(((468, 38), (468, 120)), fill=(186, 151, 207, 70), width=1)
    for x in (22, 32, 42):
        art.polygon(((x, 152), (x + 4, 148), (x + 8, 148), (x + 4, 152)), fill=(231, 107, 184, 125))
    strip.alpha_composite(ornament)
    mask = Image.new("L", strip.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 861, 159), radius=24, fill=255)
    strip.putalpha(mask)
    draw = ImageDraw.Draw(strip)
    draw.rounded_rectangle((1, 1, 860, 158), radius=24, outline=(160, 127, 189, 150), width=2)
    draw.line((35, 0, 190, 0), fill=(215, 101, 185), width=2)
    draw.line((696, 159, 822, 159), fill=(114, 196, 219), width=2)
    return strip


def _header_contacts(strip: Image.Image) -> None:
    """Center two compact, translucent link frames in the right half of the strip."""
    overlay = Image.new("RGBA", strip.size)
    draw = ImageDraw.Draw(overlay)
    for index, color in enumerate(((123, 201, 217), (216, 142, 195))):
        y = 39 + index * 46
        draw.rounded_rectangle((488, y, 841, y + 36), radius=9,
                               fill=(29, 19, 38, 140), outline=(174, 145, 193, 85), width=1)
        draw.line((488, y + 11, 488, y + 25), fill=(*color, 180), width=2)
        if index == 0:
            draw.rounded_rectangle((504, y + 11, 523, y + 25), radius=3, outline=(*color, 195), width=1)
            draw.line((509, y + 7, 513, y + 11, 518, y + 7), fill=(*color, 165), width=1)
            draw.polygon(((512, y + 15), (512, y + 22), (518, y + 18)), fill=(*color, 195))
        else:
            draw.line(((509, y + 13), (504, y + 18), (509, y + 23)), fill=(*color, 195), width=1)
            draw.line(((518, y + 13), (523, y + 18), (518, y + 23)), fill=(*color, 195), width=1)
            draw.line((516, y + 11, 511, y + 25), fill=(*color, 195), width=1)
    strip.alpha_composite(overlay)
    draw = ImageDraw.Draw(strip)
    for index, text in enumerate(HEADER_CONTACT_LINES):
        _text(draw, (540, 57 + index * 46), text, 18, MUTED, width=292)


def _title(canvas, panel: LoadoutSharePanel) -> None:
    """Calculator identity, not an imitation player profile or account decoration."""
    title = Image.new("RGBA", (1060, 208))
    strip = _header_strip(canvas)
    draw = ImageDraw.Draw(strip)
    _text(draw, (32, 49), "NDC", 52, "#75e4e5")
    _text(draw, (164, 49), "异环驱动计算器", 40, width=284)
    _text(draw, (34, 113), panel.loadout_kind + " · " + panel.slot_name, 28, "#d7ccdf", width=416)
    _header_contacts(strip)
    title.alpha_composite(strip, (198, 26))
    icon = _image(bundled_root() / "assets" / "app_icon.png")
    if icon is None:
        raise FileNotFoundError("分享面板缺少计算器图标")
    icon = ImageOps.fit(icon, (176, 176), Image.Resampling.LANCZOS)
    icon_mask = Image.new("L", icon.size)
    ImageDraw.Draw(icon_mask).ellipse((0, 0, 175, 175), fill=255)
    title.paste(icon, (6, 18), icon_mask)
    canvas.alpha_composite(title, (20, 26))


def _level(panel: LoadoutSharePanel, label: str) -> str:
    row = next((row for row in panel.cultivation if label in row.label), None)
    return row.value.split(" · ", 1)[0] if row else "—"


def _hero(canvas, panel: LoadoutSharePanel, skill_y: int) -> None:
    art = _image(panel.portrait) or _image(panel.avatar)
    if art is not None:
        bounds = art.getbbox()
        if bounds:
            art = art.crop(bounds)
        art_height = max(120, min(948, skill_y - (BODY_TOP - 48) - 16))
        art = ImageOps.contain(art, (700, art_height), Image.Resampling.LANCZOS)
        canvas.alpha_composite(art, (-32 + (700 - art.width) // 2, BODY_TOP - 48))
    else:
        _text(ImageDraw.Draw(canvas), (286, 610), "角色立绘暂缺", 32, MUTED, anchor="mm")
    x, y = 620, BODY_TOP + 8
    _paste(canvas, "base_info_bg", (x, y))
    _paste(canvas, "char_ring", (x + 8, y + 7), (44, 44))
    _icon(canvas, panel.element_icon, (x + 12, y + 11, 36, 36))
    draw = ImageDraw.Draw(canvas)
    _text(draw, (x + 111, y + 29), "Lv" + _level(panel, "等级"), 30, anchor="mm")
    _text(draw, (x + 176, y + 29), panel.role_name, 44, width=252)
    y = BODY_TOP + 75
    _paste(canvas, "banner", (x, y), (300, 81))
    _text(draw, (x + 105, y + 41), "角色属性", 34, "#191919")
    _paste(canvas, "heart", (x + 305, y + 15), (60, 52))
    _text(draw, (x + 335, y + 41), str(panel.likeability_level) if panel.likeability_level is not None else "—", 26, anchor="mm")
    _paste(canvas, "jue", (x + 374, y + 17), (63, 48))
    _text(draw, (x + 405, y + 41), _level(panel, "觉醒"), 26, width=59, anchor="mm")
    for index, stat in enumerate(panel.attributes):
        y = BODY_TOP + 160 + index * 64
        _paste(canvas, "attr_bar", (620, y))
        _icon(canvas, stat.icon, (630, y + 10, 40, 40))
        color = HIGHLIGHT if stat.highlighted else WHITE
        value_width = draw.textlength(stat.value, font=_font(34))
        _text(draw, (686, y + 31), stat.label, 28, color, width=max(75, 330 - value_width - 12), shrink=True)
        _text(draw, (1034, y + 31), stat.value, 34, color, anchor="rm")
    if not panel.attributes:
        _paste(canvas, "attr_bar", (620, BODY_TOP + 160))
        _text(draw, (644, BODY_TOP + 190), "最终面板资料未齐备", 27, MUTED)
    for index, skill in enumerate(panel.skills):
        x, y = 58 + index % SKILLS_PER_ROW * 130, skill_y + index // SKILLS_PER_ROW * 148
        _paste(canvas, "skill_bg", (x, y))
        _icon(canvas, skill.icon, (x + 18, y + 18, 64, 64))
        _paste(canvas, "skill_fg", (x, y))
        _text(draw, (x + 50, y + 89), skill.label[:4], 18, anchor="mm")
        _text(draw, (x + 50, y + 115), skill.value.split(" · ", 1)[0], 20, width=90, anchor="mm")


def _grade(canvas, grade: str, xy, size: int) -> None:
    # Sizes are baked directly from the upstream original, never resized twice.
    asset = share_asset(f"grades_{size}", grade) or share_asset("grades", grade)
    if asset is not None and _icon(canvas, asset, (*xy, size, size)):
        return
    if grade in {"ACE", "SSS", "SS", "S", "A+", "A", "B", "C", "D"}:
        raise FileNotFoundError(f"分享面板缺少或损坏评级资源：{grade}")
    # Preserve unknown legacy grades without silently substituting another rank.
    draw = ImageDraw.Draw(canvas)
    x, y = xy
    color = "#ffd95f" if grade == "ACE" else "#ede2f7"
    _text(draw, (x + size // 2, y + size // 2), grade, max(16, int(size * .52)), color,
          width=size, anchor="mm", shrink=True)


def _score_weapon(canvas, panel: LoadoutSharePanel, y: int) -> None:
    _paste(canvas, "score_bg", (18, y))
    _paste(canvas, "score_fg", (18, y))
    _grade(canvas, panel.grade, (151, y + 58), 92)
    draw = ImageDraw.Draw(canvas)
    _text(draw, (198, y + 218), f"{panel.score:.1f}", 42, anchor="mm")
    _text(draw, (28, y - 14), "评分来源：「异环工坊」 · " + panel.score_source, 20, MUTED)
    x = 406
    weapon_h = max(272, 125 + len(panel.fork_stats) * 66)
    _paste(canvas, "weapon_bg", (x, y), (676, weapon_h))
    _icon(canvas, panel.fork_icon, (x + 20, y + 12, 205, 205))
    _paste(canvas, "weapon_fg", (x, y), (676, weapon_h))
    _text(draw, (x + 318, y + 41), panel.fork_name, 36, width=244, shrink=True)
    stage = panel.fork_refinement
    stage_text = f"{stage}阶" if stage is not None else "—阶"
    draw.rounded_rectangle((x + 568, y + 23, x + 657, y + 69), radius=23, fill="#e72e3a")
    _text(draw, (x + 612, y + 46), stage_text, 26, anchor="mm")
    for index in range(5):
        _paste(canvas, "drive_star" if stage is not None and index < stage else "drive_star_none",
               (x + 257 + index * 38, y + 74), (34, 34))
    _text(draw, (x + 124, y + 224), f"Lv{panel.fork_level}" if panel.fork_level is not None else "Lv—", 36,
          anchor="mm")
    for index, stat in enumerate(panel.fork_stats):
        px, py = x + 251, y + 125 + index * 66
        _paste(canvas, "weapon_attr_bar", (px, py))
        _icon(canvas, stat.icon, (px + 8, py + 7, 48, 48))
        value = stat.value if stat.value.startswith(("+", "-", "—")) else "+" + stat.value
        value_width = draw.textlength(value, font=_font(26))
        _text(draw, (px + 78, py + 31), stat.label, 26, width=max(70, 250 - value_width - 8))
        _text(draw, (px + 334, py + 31), value, 26, anchor="rm")
    if not panel.fork_stats:
        _text(draw, (x + 263, y + 162), panel.fork_description, 23, MUTED, width=392)


def _equipment_height(item: ShareEquipment) -> int:
    sections = tuple(stats for _, stats in _equipment_sections(item) if stats)
    return max(404, 124 + sum(49 + len(stats) * 49 for stats in sections) + 30)


def _equipment_sections(item: ShareEquipment):
    main = item.main_stats[:1] if item.kind == "空幕" else ()
    return (("词条属性", main + item.sub_stats[:4]),)


def _layout(panel: LoadoutSharePanel) -> tuple[int, int]:
    """Reserve ten attribute rows and preserve every skill without overlap."""
    rows = max(ATTRIBUTE_ROW_CAPACITY, len(panel.attributes))
    attribute_bottom = BODY_TOP + 160 + (rows - 1) * 64 + 60
    skill_y = attribute_bottom - 145
    skill_extra = max(0, (len(panel.skills) + SKILLS_PER_ROW - 1) // SKILLS_PER_ROW - 1) * 148
    score_y = max(attribute_bottom, skill_y + 145 + skill_extra) + GAP
    return skill_y, score_y


def _equipment(canvas, item: ShareEquipment, x: int, y: int, height: int) -> None:
    _paste(canvas, "ad_bg", (x, y), (360, height))
    _icon(canvas, item.icon, (x, y - 2, 128, 128))
    draw = ImageDraw.Draw(canvas)
    _text(draw, (x + 116, y + 34), item.name, 26, width=220)
    _grade(canvas, item.grade, (x + 110, y + 62), 48)
    score_text = "—" if item.score is None else f"{item.score:.1f}分"
    score_w = max(104, min(188, int(draw.textlength(score_text, font=_font(24))) + 34))
    draw.rounded_rectangle((x + 158, y + 70, x + 158 + score_w, y + 108), radius=19, fill="#ee4891")
    _text(draw, (x + 158 + score_w // 2, y + 89), score_text, 24, width=score_w - 16, anchor="mm")
    cursor = y + 124
    for title, stats in _equipment_sections(item):
        if not stats:
            continue
        _paste(canvas, "ad_title_bg", (x + 34, cursor))
        _text(draw, (x + 130, cursor + 18), title, 24, "#191919", anchor="mm")
        cursor += 49
        for stat in stats:
            _paste(canvas, "ad_attr_bg", (x + 20, cursor))
            _icon(canvas, stat.icon, (x + 28, cursor + 6, 34, 34))
            color = (HIGHLIGHT_LOCKED if stat.locked else HIGHLIGHT) if stat.highlighted else WHITE
            value_width = draw.textlength(stat.value, font=_font(24))
            _text(draw, (x + 72, cursor + 24), stat.label, 24, color, width=max(65, 246 - value_width - 12))
            _text(draw, (x + 326, cursor + 24), stat.value, 24, color, anchor="rm")
            if stat.locked:
                overlay = Image.new("RGBA", (320, 46))
                ImageDraw.Draw(overlay).rounded_rectangle((0, 0, 319, 45), radius=15, fill=(70, 70, 76, 125))
                canvas.alpha_composite(overlay, (x + 20, cursor))
            cursor += 49
    if item.virtual:
        _text(draw, (x + 180, y + height - 22), "虚拟占位 · 0 分", 23, MUTED, anchor="mm")
    elif not any(stats for _, stats in _equipment_sections(item)):
        _text(draw, (x + 180, cursor + 26), "暂无已确认词条", 24, MUTED, anchor="mm")


def render_loadout_share_png(
    panel: LoadoutSharePanel, *, cancelled: Callable[[], bool] = lambda: False,
) -> bytes:
    """Composite original textures; keep confirmed rows without a damage area."""
    if not 1 <= len(panel.equipment) <= 32 or len(panel.attributes) > 48 or len(panel.skills) > 32 or len(panel.fork_stats) > 16:
        raise ValueError("分享面板条目数量超出展示限制")
    if cancelled():
        raise CancelledError()
    skill_y, score_y = _layout(panel)
    weapon_h = max(272, 125 + len(panel.fork_stats) * 66)
    gear_y = score_y + weapon_h + GAP
    row_heights = [max(_equipment_height(item) for item in panel.equipment[i:i + 3]) for i in range(0, len(panel.equipment), 3)]
    height = gear_y + sum(row_heights) + (len(row_heights) - 1) * GAP + 74
    canvas = ImageOps.fit(_texture("bg3"), (WIDTH, height), Image.Resampling.LANCZOS)
    _hero(canvas, panel, skill_y)
    _title(canvas, panel)
    _score_weapon(canvas, panel, score_y)
    y = gear_y
    for row_index, row_h in enumerate(row_heights):
        if cancelled():
            raise CancelledError()
        for column, item in enumerate(panel.equipment[row_index * 3:row_index * 3 + 3]):
            _equipment(canvas, item, 4 + column * 366, y, row_h)
        y += row_h + GAP
    draw = ImageDraw.Draw(canvas)
    _text(draw, (WIDTH // 2, y + 10), "配装生成 NTE DRIVE CALC · 面板模板 Wuyi / NTEUID", 20, MUTED, anchor="mm")
    if cancelled():
        raise CancelledError()
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def save_share_png(path: str | Path, data: bytes) -> None:
    """Publish a completed PNG atomically; keep an existing file on write failure."""
    target = Path(path)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix=".ndc-share-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
