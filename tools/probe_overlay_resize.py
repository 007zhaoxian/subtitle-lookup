"""验证悬浮窗：能拖动缩放 + 真实屏幕上文字可见 + 尺寸被记住。

会做三件事：
  1. 建窗 → 真实截屏（确认文字还在，手柄画出来了）
  2. 程序化模拟「拖右下角」把窗口放大到 900x520 → 再截屏
  3. 打印 settings 里记住的尺寸
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.expanduser("~/Library/Logs/DoubaoLookup/overlay_resize")

BODY = (
    "sugarcoat —— 动词 / 粉饰，委婉美化（不把话说好听，不遮掩糟糕事实）；\n"
    "face the music —— 俚语 / 承担后果，接受惩罚（面对现实、为行为买单）；\n"
    "themed —— 形容词 / 有特定主题的，句中修饰 party，表示主题（派对）；\n"
    "in hot water —— 俚语 / 陷入麻烦，处境不妙。" * 3
)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    from PyQt6.QtCore import QPoint, QTimer
    from PyQt6.QtWidgets import QApplication

    import overlay as ov
    import settings

    app = QApplication(["probe"])
    runtime = ov.QtRuntime(lambda: None)
    runtime._app = app

    def step1():
        runtime._show("查词结果", BODY)
        w = runtime._overlay
        print("初始尺寸:", w.width(), "x", w.height())
        print("手柄:", sorted(w._grips.keys()) if w._grips else "无")
        QTimer.singleShot(700, lambda: shot("1-默认"))

    def shot(name: str):
        subprocess.run(["screencapture", "-x", f"{OUT}/{name}.png"], capture_output=True)
        print("已截图:", name)
        QTimer.singleShot(200, next_step)

    def next_step():
        nonlocal_step()

    step_i = {"n": 0}
    steps = ["resize_bigger", "resize_small", "done"]

    def nonlocal_step():
        i = step_i["n"]
        step_i["n"] += 1
        name = steps[i]
        if name == "done":
            print("记住的尺寸:", settings.get_overlay_size())
            app.quit()
            return
        w = runtime._overlay
        pos0, size0 = w.pos(), w.size()
        if name == "resize_bigger":
            # 等价于用户拖右下角 280x180
            w.apply_resize("se", pos0, size0, 280, 180)
            w.finish_resize()
        else:
            w.apply_resize("se", pos0, size0, -420, -160)
            w.finish_resize()
        print(f"{name}: -> {w.width()}x{w.height()}", "正文",
              w.browser.width(), "x", w.browser.height())
        QTimer.singleShot(500, lambda: shot(f"2-{name}"))

    QTimer.singleShot(600, step1)
    app.exec()
    print("done ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
