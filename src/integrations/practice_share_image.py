# 用原版练度横幅、霓虹表头和排行条带合成本地统计图，不引入 Bot 依赖。
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageOps

from src.domain.practice_share import PracticeEntry, PracticeSharePanel, PracticeValue
from src.integrations.bundled_resources import bundled_root
from src.integrations.loadout_share_assets import share_asset
from src.integrations.loadout_share_image import HEADER_CONTACT_LINES, _font, _grade, _icon, _image, _text

WIDTH = 1860
BANNER_H = 300
COLHEAD_H = 80
ROW_H = 136
ROW_GAP = 14
ROW_X = 24
ROW_W = 1832
WHITE = '#ffffff'
MUTED = '#c6b8e0'
CYAN = '#60d0e8'
GOLD = '#ffd060'
MAGENTA = '#ec4896'
GRADE_COLORS = {'ACE': GOLD, 'SSS': '#ff7278', 'SS': '#ffc66c', 'S': '#ffcf76',
                'A': '#c696f8', 'B': '#76b2f8', 'C': '#78d6a2', 'D': MUTED}


def _cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise CancelledError()


@lru_cache(maxsize=12)
def _asset(name: str) -> Image.Image:
    image = _image(share_asset('level', name))
    if image is None:
        raise FileNotFoundError(f'练度统计缺少本地纹理：{name}')
    return image


@lru_cache(maxsize=4)
def _bar(name: str, height: int, cap: int) -> Image.Image:
    asset = _asset(name)
    scaled_w = round(asset.width * height / asset.height)
    scaled = asset.resize((scaled_w, height), Image.Resampling.LANCZOS)
    if ROW_W <= scaled_w:
        return scaled.resize((ROW_W, height), Image.Resampling.LANCZOS)
    output = Image.new('RGBA', (ROW_W, height))
    output.paste(scaled.crop((0, 0, cap, height)), (0, 0))
    output.paste(scaled.crop((cap, 0, scaled_w-cap, height)).resize((ROW_W-2*cap, height), Image.Resampling.LANCZOS), (cap, 0))
    output.paste(scaled.crop((scaled_w-cap, 0, scaled_w, height)), (ROW_W-cap, 0))
    return output


def _value(draw, xy, value: PracticeValue, color=WHITE, size=42) -> None:
    _text(draw, xy, value.text, size, color, anchor='mm')
    if value.configured:
        length = draw.textlength(value.text, font=_font(size))
        _text(draw, (xy[0]+length/2+8, xy[1]-15), '＊', 17, MUTED, anchor='mm')


def _banner_info(canvas: Image.Image) -> None:
    """Three equal-width centered frames, tinted to the cool blue banner artwork."""
    info = Image.new('RGBA', (480, 180))
    draw = ImageDraw.Draw(info)
    for index, text in enumerate((*HEADER_CONTACT_LINES, '权重数据来自异环工坊')):
        y = index*60
        draw.rounded_rectangle((0, y, 479, y+48), radius=10,
                               fill=(18, 25, 43, 165), outline=(137, 158, 193, 110), width=1)
        draw.line((0, y+15, 0, y+33), fill=(130, 183, 214, 160), width=2)
        _text(draw, (240, y+24), text, 22, MUTED, anchor='mm', width=440)
    canvas.alpha_composite(info, (1332, 78))


def _banner(canvas: Image.Image, total: int) -> None:
    canvas.alpha_composite(ImageOps.fit(_asset('title'), (WIDTH, BANNER_H), Image.Resampling.LANCZOS, centering=(.5, .3)))
    gradient = Image.new('RGBA', (1, 176))
    for y in range(176):
        gradient.putpixel((0, y), (8, 6, 20, round(220*y/175)))
    canvas.alpha_composite(gradient.resize((WIDTH, 176)), (0, BANNER_H-176))
    draw = ImageDraw.Draw(canvas)
    _text(draw, (48, 96), '面板练度统计', 62)
    draw.line((52, 140, 372, 140), fill=MAGENTA, width=6)
    draw.line((52, 140, 252, 140), fill=CYAN, width=6)
    _icon(canvas, bundled_root()/'assets/app_icon.png', (40, 164, 118, 118))
    _text(draw, (182, 208), 'NTE DRIVE CALC', 44)
    _text(draw, (184, 260), f'游戏配装 · 共 {total} 名角色', 28, MUTED)
    _banner_info(canvas)


def _colhead(canvas: Image.Image) -> None:
    canvas.alpha_composite(_bar('colhead', COLHEAD_H, 150), (ROW_X, BANNER_H))
    draw = ImageDraw.Draw(canvas)
    for x, label in ((165, '#'), (268, '角色'), (700, 'Lv'), (780, '觉醒'), (860, '好感'),
                     (940, '普'), (1002, '技'), (1064, '终'), (1126, '连'),
                     (1335, '弧盘'), (1620, '配装 · 评分')):
        _text(draw, (x, BANNER_H+COLHEAD_H//2-2), label, 32, anchor='mm')


def _row(canvas: Image.Image, y: int, rank: int, entry: PracticeEntry) -> None:
    canvas.alpha_composite(_bar('row', ROW_H, 180), (ROW_X, y))
    draw = ImageDraw.Draw(canvas)
    mid = y+ROW_H//2
    _text(draw, (165, mid), str(rank), 42, {1: GOLD, 2: '#d6dcec', 3: '#e3aa78'}.get(rank, WHITE), anchor='mm')
    avatar = _image(entry.avatar)
    if avatar is not None:
        avatar = ImageOps.fit(avatar, (104, 104), Image.Resampling.LANCZOS)
        mask = Image.new('L', (104, 104))
        ImageDraw.Draw(mask).ellipse((0, 0, 103, 103), fill=255)
        canvas.paste(avatar, (218, mid-52), mask)
    else:
        draw.ellipse((218, mid-52, 322, mid+52), fill='#28243c')
    draw.ellipse((218, mid-52, 322, mid+52), outline='#b5abcb', width=3)
    if entry.element_icon is not None:
        draw.ellipse((212, mid-60, 260, mid-12), fill='#211a33', outline=MAGENTA, width=2)
        _icon(canvas, entry.element_icon, (218, mid-54, 36, 36))
    _text(draw, (336, mid), entry.name, 40, '#ffb25c', width=320)
    _value(draw, (700, mid), entry.level)
    _value(draw, (780, mid), entry.awakening, GOLD)
    _value(draw, (860, mid), entry.heart, '#ff96c4', 32)
    for index in range(4):
        _value(draw, (940+index*62, mid), entry.skills[index] if index < len(entry.skills) else PracticeValue(), size=38)
    if entry.fork_name:
        _icon(canvas, entry.fork_icon, (1166, mid-36, 72, 72))
        _text(draw, (1248, mid-15), entry.fork_name, 30, width=270)
        _value(draw, (1285, mid+22), PracticeValue(
            entry.fork_refinement.text+'阶' if entry.fork_refinement.text != '—' else '—阶',
            entry.fork_refinement.configured), CYAN, 24)
    else:
        _text(draw, (1248, mid), '—', 32, MUTED)
    if entry.score is None:
        _text(draw, (1745, mid-10), '—', 44, MUTED, anchor='rm')
    else:
        _grade(canvas, entry.grade, (1530, mid-27), 54)
        _text(draw, (1745, mid-10), f'{entry.score:.1f}', 50, GRADE_COLORS.get(entry.grade, MUTED),
              width=150, shrink=True, anchor='rm')
    set_text = f'· {entry.active_set_count}件' if entry.active_set_count is not None else '· —'
    _text(draw, (1745, mid+32), set_text, 24, MUTED, anchor='rm')


def render_practice_share_png(
    panel: PracticeSharePanel, *, cancelled: Callable[[], bool] = lambda: False,
) -> bytes:
    _cancel(cancelled)
    if not 1 <= len(panel.entries) <= 80:
        raise ValueError('练度统计角色数量超出展示范围')
    end = BANNER_H+COLHEAD_H+len(panel.entries)*ROW_H+(len(panel.entries)-1)*ROW_GAP
    height = end+140
    canvas = ImageOps.fit(_asset('background'), (WIDTH, height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(Image.new('RGBA', canvas.size, (10, 9, 24, 170)))
    _banner(canvas, len(panel.entries))
    _colhead(canvas)
    for index, entry in enumerate(panel.entries):
        _cancel(cancelled)
        _row(canvas, BANNER_H+COLHEAD_H+index*(ROW_H+ROW_GAP), index+1, entry)
    canvas.alpha_composite(_asset('marquee').resize((ROW_W, 54), Image.Resampling.LANCZOS), (ROW_X, end+16))
    draw = ImageDraw.Draw(canvas)
    _text(draw, (WIDTH//2, end+108), '配装生成 NTE DRIVE CALC · 面板模板 Wuyi / NTEUID',
          28, MUTED, anchor='mm')
    _cancel(cancelled)
    output = BytesIO()
    canvas.convert('RGB').save(output, format='PNG', optimize=True)
    _cancel(cancelled)
    return output.getvalue()
