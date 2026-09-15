"""图标：菜单栏小图标（单色） + App 图标（彩色，用于 Dock / 启动台 / 访达）。

两条完全不同的需求，所以分成两套绘制：

1. **菜单栏图标**（22pt）：必须单色、剪影清晰。设计语言是「屏幕 + 播放三角 +
   两行字幕条」—— 一眼看出"看剧 + 字幕"，且在深色/浅色菜单栏都清楚。
   缓存文件名带 v2 / dark|light，改设计后不会复用旧图。

2. **App 图标**（1024pt 主图 → .icns）：彩色、有层次。
   * 形状：用**超椭圆 squircle**（n=5）而不是普通圆角矩形 —— 更像 macOS 原生图标；
   * 背景：深靛蓝渐变 + 左上角光晕 + 顶部高光；
   * 前景：白色玻璃播放按钮（符号表示"追剧"）+ 字幕卡片（青色高亮条表示"字幕"）
     + 放大镜（表示"查词"）。
   运行时只生成 512px 给 Dock 用；打包时由 tools/make_app_icons.py 生成全套 .icns。
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import config
from utils import log


def is_dark_menu_bar() -> bool:
    """当前是否深色模式（决定图标该用白色还是黑色）。"""
    try:
        out = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=3,
        )
        return "dark" in (out.stdout or "").strip().lower()
    except Exception:
        return True


def auto_ink() -> tuple[int, int, int]:
    """深色菜单栏 → 接近纯白；浅色菜单栏 → 接近纯黑。"""
    return (245, 246, 248) if is_dark_menu_bar() else (24, 25, 28)


# ==================================================================== 菜单栏图标
def paint_menu_glyph(p, size: float, ink) -> None:
    """画菜单栏剪影：屏幕（挖空播放三角 + 两条字幕条）+ 支架底座。

    所有坐标都以「22pt 设计稿」为基准，这里按 size/22 缩放，保证任何尺寸都对得上。
    """
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QBrush, QColor, QPainterPath, QPen

    k = size / 22.0
    col = QColor(ink[0], ink[1], ink[2], 255)

    # ---- 屏幕本体 ----
    screen = QPainterPath()
    screen.addRoundedRect(
        QRectF(1.35 * k, 3.10 * k, 19.30 * k, 12.30 * k), 3.30 * k, 3.30 * k
    )
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(col))
    p.drawPath(screen)

    # ---- 挖空（负空间）：播放三角 + 一条字幕条 ----
    p.setCompositionMode(p.CompositionMode.CompositionMode_Clear)

    tri = QPainterPath()
    tri.moveTo(QPointF(8.75 * k, 5.35 * k))
    tri.lineTo(QPointF(15.35 * k, 8.10 * k))
    tri.lineTo(QPointF(8.75 * k, 10.85 * k))
    tri.closeSubpath()
    p.drawPath(tri)

    # 字幕条：22pt 下只能放一条，否则会被抗锯齿糊成灰点
    p.drawRoundedRect(QRectF(6.10 * k, 11.85 * k, 9.80 * k, 1.65 * k),
                      0.82 * k, 0.82 * k)

    p.setCompositionMode(p.CompositionMode.CompositionMode_SourceOver)

    # ---- 支架 + 底座（用稍有收窄的梯形更像真机）----
    stand = QPainterPath()
    stand.moveTo(QPointF(9.90 * k, 15.40 * k))
    stand.lineTo(QPointF(12.10 * k, 15.40 * k))
    stand.lineTo(QPointF(12.65 * k, 17.85 * k))
    stand.lineTo(QPointF(9.35 * k, 17.85 * k))
    stand.closeSubpath()
    p.setBrush(QBrush(col))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(stand)

    p.setPen(QPen(col, 1.55 * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(6.60 * k, 18.75 * k), QPointF(15.40 * k, 18.75 * k))


def ensure_icon(
    size: int = 22,
    color: tuple[int, int, int] | None = None,
    name: str | None = None,
) -> Path | None:
    """生成（或复用）状态栏图标 PNG。返回路径；失败返回 None。"""
    if color is None:
        color = auto_ink()
    if name is None:
        name = "menu_icon_v2_" + ("dark" if sum(color) > 380 else "light")

    path = config.TMP_DIR / f"{name}_{size}.png"
    if path.exists():
        return path

    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPainter

        scale = 2  # Retina
        img = QImage(size * scale, size * scale,
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_menu_glyph(p, size * scale, color)
        p.end()
        img.save(str(path), "PNG")
        return path
    except Exception as exc:
        log.warning("生成图标失败，改用文字图标: %s", exc)
        return None


# ==================================================================== App 图标
def _squircle_path(rect, n: float = 5.0, samples: int = 360):
    """超椭圆（squircle）路径：|x/a|^n + |y/b|^n = 1。

    macOS 原生图标不是普通圆角矩形，而是这种"连续圆角"，用它会明显更像系统图标。
    """
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QPainterPath

    cx = rect.x() + rect.width() / 2.0
    cy = rect.y() + rect.height() / 2.0
    a = rect.width() / 2.0
    b = rect.height() / 2.0
    e = 2.0 / n

    path = QPainterPath()
    for i in range(samples + 1):
        t = 2.0 * math.pi * i / samples
        ct, st = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(ct) ** e, ct)
        y = cy + b * math.copysign(abs(st) ** e, st)
        if i == 0:
            path.moveTo(QPointF(x, y))
        else:
            path.lineTo(QPointF(x, y))
    path.closeSubpath()
    path.setFillRule(Qt.FillRule.WindingFill)
    return path


def paint_app_icon(p, size: float) -> None:
    """在 1024x1024 设计稿坐标系下画 App 图标（p 已按 size/1024 缩放过）。"""
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import (
        QBrush, QColor, QLinearGradient, QPainterPath, QPen, QRadialGradient,
    )

    k = size / 1024.0
    p.setRenderHint(p.RenderHint.Antialiasing, True)

    body = QRectF(100 * k, 100 * k, 824 * k, 824 * k)
    shape = _squircle_path(body, 5.0)

    # ---------------- 背景渐变 ----------------
    p.save()
    p.setClipPath(shape)

    bg = QLinearGradient(QPointF(body.left(), body.top()),
                         QPointF(body.right(), body.bottom()))
    bg.setColorAt(0.00, QColor("#3B4AD8"))
    bg.setColorAt(0.45, QColor("#2A2E8F"))
    bg.setColorAt(1.00, QColor("#151A45"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRect(body)

    # 左上角光晕
    glow = QRadialGradient(QPointF(body.left() + body.width() * 0.30,
                                  body.top() + body.height() * 0.20),
                           body.width() * 0.72)
    glow.setColorAt(0.0, QColor(120, 150, 255, 120))
    glow.setColorAt(1.0, QColor(120, 150, 255, 0))
    p.setBrush(QBrush(glow))
    p.drawRect(body)

    # 右下角暖色反光，避免整图一片冷色
    glow2 = QRadialGradient(QPointF(body.right() - body.width() * 0.14,
                                   body.bottom() - body.height() * 0.10),
                            body.width() * 0.62)
    glow2.setColorAt(0.0, QColor(150, 90, 235, 110))
    glow2.setColorAt(1.0, QColor(150, 90, 235, 0))
    p.setBrush(QBrush(glow2))
    p.drawRect(body)

    # 顶部玻璃高光
    gloss = QLinearGradient(QPointF(0, body.top()), QPointF(0, body.top() + body.height() * 0.45))
    gloss.setColorAt(0.0, QColor(255, 255, 255, 52))
    gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(gloss))
    p.drawRect(body)
    p.restore()

    # ---------------- 播放按钮 ----------------
    cxy = QPointF(body.left() + body.width() * 0.50,
                  body.top() + body.height() * 0.315)
    r = body.width() * 0.148

    # 按钮下方的柔光
    halo = QRadialGradient(cxy, r * 2.15)
    halo.setColorAt(0.0, QColor(120, 200, 255, 105))
    halo.setColorAt(1.0, QColor(120, 200, 255, 0))
    p.setBrush(QBrush(halo))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(cxy, r * 2.15, r * 2.15)

    p.setBrush(QBrush(QColor(255, 255, 255, 246)))
    p.drawEllipse(cxy, r, r)

    tri = QPainterPath()
    tri.moveTo(QPointF(cxy.x() - r * 0.29, cxy.y() - r * 0.425))
    tri.lineTo(QPointF(cxy.x() + r * 0.435, cxy.y()))
    tri.lineTo(QPointF(cxy.x() - r * 0.29, cxy.y() + r * 0.425))
    tri.closeSubpath()
    grad_tri = QLinearGradient(QPointF(0, cxy.y() - r), QPointF(0, cxy.y() + r))
    grad_tri.setColorAt(0.0, QColor("#3B4AD8"))
    grad_tri.setColorAt(1.0, QColor("#1B2064"))
    p.setBrush(QBrush(grad_tri))
    p.drawPath(tri)

    # ---------------- 字幕卡片 ----------------
    card = QRectF(body.left() + body.width() * 0.115,
                  body.top() + body.height() * 0.565,
                  body.width() * 0.770,
                  body.height() * 0.255)
    cr = card.height() * 0.34
    cpath = QPainterPath()
    cpath.addRoundedRect(card, cr, cr)

    p.setBrush(QBrush(QColor(255, 255, 255, 40)))
    p.setPen(QPen(QColor(255, 255, 255, 92), 3.2 * k))
    p.drawPath(cpath)

    bar_h = card.height() * 0.175
    bar_r = bar_h / 2.0

    # 第一行（青色高亮 = 生词被标出来了）
    b1 = QRectF(card.left() + card.width() * 0.105,
                card.top() + card.height() * 0.215,
                card.width() * 0.470, bar_h)
    g1 = QLinearGradient(b1.topLeft(), b1.topRight())
    g1.setColorAt(0.0, QColor("#7FE3FF"))
    g1.setColorAt(1.0, QColor("#4FB6F5"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g1))
    p.drawRoundedRect(b1, bar_r, bar_r)

    # 第二行（白色半透明）
    b2 = QRectF(b1.left(), card.top() + card.height() * 0.575,
                card.width() * 0.330, bar_h)
    p.setBrush(QBrush(QColor(255, 255, 255, 132)))
    p.drawRoundedRect(b2, bar_r, bar_r)

    # ---------------- 放大镜（"查词"）----------------
    lens_c = QPointF(card.left() + card.width() * 0.792,
                     card.top() + card.height() * 0.430)
    lens_r = card.width() * 0.086
    pen = QPen(QColor(255, 255, 255, 238), card.width() * 0.030,
               Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(255, 255, 255, 26)))
    p.drawEllipse(lens_c, lens_r, lens_r)

    # 手柄
    ang = math.radians(45)
    hx = lens_c.x() + math.cos(ang) * lens_r * 0.98
    hy = lens_c.y() + math.sin(ang) * lens_r * 0.98
    p.drawLine(QPointF(hx, hy),
               QPointF(hx + math.cos(ang) * lens_r * 0.78,
                       hy + math.sin(ang) * lens_r * 0.78))

    # ---------------- 内描边（让图标边界更利落）----------------
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 46), 2.6 * k))
    p.drawPath(shape)


def make_app_icon(size: int = 512, path: Path | None = None) -> Path | None:
    """生成彩色 App 图标 PNG（默认 512，给 Dock / 预览用）。"""
    path = path or (config.TMP_DIR / f"app_icon_{size}.png")
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPainter

        # 先在 1024 设计稿上画，再平滑缩放，细节更干净
        master = 1024
        img = QImage(master, master, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        paint_app_icon(p, float(master))
        p.end()
        if size != master:
            from PyQt6.QtCore import Qt as _Qt

            img = img.scaled(size, size,
                             _Qt.AspectRatioMode.KeepAspectRatio,
                             _Qt.TransformationMode.SmoothTransformation)
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(path), "PNG")
        return path
    except Exception as exc:
        log.warning("生成 App 图标失败: %s", exc)
        return None
