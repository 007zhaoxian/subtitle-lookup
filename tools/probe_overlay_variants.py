"""对比三种毛玻璃挂法的真实合成结果（用区域截屏说话）。

变体：
  A 现状      —— NSVisualEffectView 作为 Qt contentView 的子视图
  B 关掉毛玻璃 —— 纯 Qt 半透明卡片
  C 换父级    —— 让 effect 当 window.contentView，Qt 的视图挂到它下面
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.expanduser("~/Library/Logs/DoubaoLookup/overlay_variants")
BODY = (
    "sugarcoat —— 动词 / 粉饰，委婉美化（不把话说好听）；\n"
    "face the music —— 俚语 / 承担后果，接受惩罚。\n"
    "themed —— 形容词 / 有特定主题的。"
)


def attach_reparent(widget):
    """让毛玻璃真正待在 Qt 内容下面。"""
    import objc
    from AppKit import (
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialHUDWindow,
        NSVisualEffectStateActive,
        NSVisualEffectView,
    )

    content = objc.objc_object(c_void_p=int(widget.winId()))
    win = content.window()
    effect = NSVisualEffectView.alloc().initWithFrame_(content.frame())
    effect.setAutoresizingMask_(18)  # WidthSizable | HeightSizable
    effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
    effect.setState_(NSVisualEffectStateActive)
    effect.setWantsLayer_(True)
    layer = effect.layer()
    layer.setCornerRadius_(18.0)
    layer.setMasksToBounds_(True)
    content.setAutoresizingMask_(18)
    win.setContentView_(effect)
    effect.addSubview_(content)
    return effect


def attach_sibling(widget):
    """把毛玻璃挂成 Qt contentView 的「兄弟」，排在它下面。

    关键：NSView 的子视图永远画在父视图自己的 drawRect 之上，
    所以插进 Qt contentView 的任何子视图都会盖住文字。
    这里改插到 contentView 的父视图（窗口的 theme frame）里，
    用 NSWindowBelow 相对 contentView 定位 —— 真正的「下层」。
    """
    import objc
    from AppKit import (
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialHUDWindow,
        NSVisualEffectStateActive,
        NSVisualEffectView,
        NSWindowBelow,
    )

    content = objc.objc_object(c_void_p=int(widget.winId()))
    win = content.window()
    frame = content.superview()
    effect = NSVisualEffectView.alloc().initWithFrame_(content.frame())
    effect.setAutoresizingMask_(18)
    effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
    effect.setState_(NSVisualEffectStateActive)
    effect.setWantsLayer_(True)
    layer = effect.layer()
    layer.setCornerRadius_(18.0)
    layer.setMasksToBounds_(True)
    frame.addSubview_positioned_relativeTo_(effect, NSWindowBelow, content)
    return effect


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    import config
    import overlay as ov

    variant = sys.argv[1] if len(sys.argv) > 1 else "A"
    if variant == "B":
        config.OVERLAY_VIBRANCY = False

    app = QApplication(["probe"])
    runtime = ov.QtRuntime(lambda: None)
    runtime._app = app

    def step1():
        runtime._show("查词结果", BODY)
        w = runtime._overlay
        if variant == "C":
            ov._attach_vibrancy = lambda _w: None
            attach_reparent(w)
        if variant == "G":
            ov._attach_vibrancy = lambda _w: None
            attach_sibling(w)
        g = w.geometry()
        print("geometry:", g.x(), g.y(), g.width(), g.height())
        QTimer.singleShot(900, capture)

    def capture():
        subprocess.run(["screencapture", "-x", f"{OUT}/{variant}.png"],
                       capture_output=True)
        QTimer.singleShot(200, lambda: app.quit())

    QTimer.singleShot(700, step1)
    app.exec()
    print("variant", variant, "->", f"{OUT}/{variant}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
