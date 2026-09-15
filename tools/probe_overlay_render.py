"""单独验证悬浮窗到底画没画出内容。

不触发豆包、不截屏，只把浮窗建出来：
  * 用 widget.grab() 把 Qt 自己画的内容存成 PNG（看文字有没有被绘制）；
  * 再用 screencapture 抓一张全屏（看真实合成结果，可能因权限失败）。
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.expanduser("~/Library/Logs/DoubaoLookup/overlay_render")

BODY = (
    "sugarcoat —— 动词 / 粉饰，委婉美化（这句话指不把话说好听，不遮掩糟糕事实）；\n"
    "face the music —— 俚语 / 承担后果，接受惩罚（意为面对现实、为行为买单）；\n"
    "themed —— 形容词 / 有特定主题的，句中修饰 party，表示主题（派对）。\n"
    "这是一段用来测试换行与滚动条的较长文本，重复若干次以撑高内容区域。" * 2
)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    import overlay as ov

    app = QApplication(["probe"])
    runtime = ov.QtRuntime(lambda: None)
    runtime._app = app
    runtime._ctrl = None

    def step1():
        runtime._show("查词结果", BODY)
        w = runtime._overlay
        print("window:", w.width(), "x", w.height(), "visible=", w.isVisible())
        print("browser:", w.browser.width(), "x", w.browser.height(),
              "visible=", w.browser.isVisible())
        print("browser.toPlainText len:", len(w.browser.toPlainText()))
        print("browser html len:", len(w.browser.toHtml()))
        subprocess.run(["screencapture", "-x", f"{OUT}/screen.png"],
                       capture_output=True)
        QTimer.singleShot(900, step2)

    def step2():
        w = runtime._overlay
        pix = w.grab()
        ok = pix.save(f"{OUT}/widget.png")
        print("grab saved:", ok, pix.width(), "x", pix.height())
        subprocess.run(["screencapture", "-x", f"{OUT}/screen2.png"],
                       capture_output=True)
        app.quit()

    QTimer.singleShot(500, step1)
    app.exec()
    print("done ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
