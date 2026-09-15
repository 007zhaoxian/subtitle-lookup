"""生成 App 图标（assets/AppIcon.icns）与图标预览图。

用法：
    python tools/make_app_icons.py

产物：
    assets/AppIcon.icns        ← 打包进 .app（Dock / 启动台 / 访达里显示的就是它）
    assets/AppIcon.png         ← 512 预览图
    assets/preview_menubar.png ← 菜单栏图标的深/浅色预览（放大 8 倍便于看细节）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ASSETS = ROOT / "assets"

# icns 需要的全部尺寸
_SPECS = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def _app():
    from PyQt6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication(["iconbuilder"])


def build_master() -> "object":
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage, QPainter

    import icon

    img = QImage(1024, 1024, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    icon.paint_app_icon(p, 1024.0)
    p.end()
    return img


def main() -> int:
    app = _app()
    ASSETS.mkdir(parents=True, exist_ok=True)

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage, QPainter

    import icon

    master = build_master()

    # ---- iconset ----
    iconset = ASSETS / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    for name, size in _SPECS:
        out = master.scaled(size, size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        out.save(str(iconset / name), "PNG")
        print(f"  {name:22s} {size}x{size}")

    icns = ASSETS / "AppIcon.icns"
    rc = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        print("iconutil 失败:", rc.stderr)
        return 1
    print(f"✅ {icns}  ({icns.stat().st_size // 1024} KB)")

    # ---- 预览图 ----
    master.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                  Qt.TransformationMode.SmoothTransformation).save(
        str(ASSETS / "AppIcon.png"), "PNG")

    # 菜单栏图标预览：上排 1:1 真实尺寸（检验清晰度），下排 8 倍放大（看细节）
    zoom = 8
    cell = 22 * zoom
    pad = 10
    prev = QImage(cell * 2 + pad * 3, cell + 22 + pad * 3,
                  QImage.Format.Format_ARGB32_Premultiplied)
    prev.fill(QColor("#3A3A3C"))
    inks = ((245, 246, 248), (24, 25, 28))
    for i, ink in enumerate(inks):
        glyph = QImage(cell, cell, QImage.Format.Format_ARGB32_Premultiplied)
        glyph.fill(Qt.GlobalColor.transparent)
        gp = QPainter(glyph)
        gp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        icon.paint_menu_glyph(gp, float(cell), ink)
        gp.end()
        pp = QPainter(prev)
        pp.drawImage(pad + i * (cell + pad), pad + 22, glyph)
        pp.end()

        real = QImage(22, 22, QImage.Format.Format_ARGB32_Premultiplied)
        real.fill(Qt.GlobalColor.transparent)
        rp = QPainter(real)
        rp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        icon.paint_menu_glyph(rp, 22.0, ink)
        rp.end()
        pp = QPainter(prev)
        pp.drawImage(pad + 40 + i * 120, 4, real)
        pp.end()
    prev.save(str(ASSETS / "preview_menubar.png"), "PNG")

    shutil.rmtree(iconset)
    print("✅ 预览图:", ASSETS / "AppIcon.png", "/", ASSETS / "preview_menubar.png")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
