"""真机验证：点一下浮窗的输入框，是否真的进入输入态、并拿到键盘。

【为什么要单独验一次】
用户原话：「我点击悬浮窗的输入框，我发现并不能输入任何东西」。
根因是 QLineEdit 会自己吃掉鼠标事件、不冒泡到窗口的 mouseReleaseEvent，
所以点击只留下一次 setFocus —— 而浮窗带 WindowDoesNotAcceptFocus，
这个 focus 落不实。修法是给 edit 装的事件过滤器里补上 MouseButtonPress。

这里用 QTest.mouseClick 走**和真人点击完全相同的事件派发路径**，
再问系统"现在 key window 是谁"，最后截图存档。

用法:  python tools/probe_click_input.py [/tmp/输出目录]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DL_SETTINGS_DIR", str(Path.home() / "Library/Application Support/DoubaoLookup"))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/dl_click")
OUT.mkdir(parents=True, exist_ok=True)

from PyQt6.QtCore import QTimer, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import overlay as ov_mod  # noqa: E402
from utils import log  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""), flush=True)


def nswin(w):
    import objc
    from ctypes import c_void_p

    return objc.objc_object(c_void_p=int(w.winId())).window()


class Rig:
    def __init__(self, app, qapp):
        self.app = app
        self.qapp = qapp
        self.ov = None
        self.ui = None

    def start(self):
        self.ui = ov_mod.SharedUiState()
        cls = ov_mod._make_widget_class()
        self.ov = cls(
            "豆包 · 字幕生词",
            "sugarcoat —— v. 粉饰，委婉美化（坏消息）\n"
            "face the music —— 承担后果 / 接受惩罚",
            on_closed=lambda: None,
            on_ask=lambda t: None,
            ui_state=self.ui,
            prev_app=None,
        )
        self.ov.show()
        ov_mod._float_over_everything(self.ov)
        QTimer.singleShot(900, self.s1_before)
        QTimer.singleShot(1400, self.s2_click)
        QTimer.singleShot(2300, self.s3_after)
        QTimer.singleShot(2800, self.s4_type)
        QTimer.singleShot(3600, self.s5_finish)

    def s1_before(self):
        print("\n【1】浮窗刚显示（还没点）")
        check("初始不在输入态", self.ui.typing() is False)

    def s2_click(self):
        """用真实的鼠标事件点击输入框 —— 走的正是用户手指那条路。"""
        print("\n【2】点一下输入框（QTest.mouseClick，真实事件派发）")
        from PyQt6.QtTest import QTest

        QTest.mouseClick(self.ov.edit, Qt.MouseButton.LeftButton)
        self.qapp.processEvents()

    def s3_after(self):
        print("\n【3】点击之后")
        check("进入输入态（浮窗接管键盘）", self.ui.typing() is True)
        check("输入框拿到了焦点", bool(self.ov.edit.hasFocus()))
        try:
            from AppKit import NSApplication

            app = NSApplication.sharedApplication()
            check("本 App 已激活", bool(app.isActive()))
            kw = app.keyWindow()
            check("浮窗成为 key window（键盘会送到它）",
                  kw is not None and str(kw) == str(nswin(self.ov)))
            w = nswin(self.ov)
            check("NSPanel 允许成为 key（becomesKeyOnlyIfNeeded 已关）",
                  bool(w.canBecomeKeyWindow()))
        except Exception as exc:
            check("NS 层查询", False, str(exc))
        try:
            from PyQt6.QtWidgets import QApplication

            fw = QApplication.focusWidget()
            check("Qt 的 focusWidget 就是输入框", fw is self.ov.edit)
        except Exception as exc:
            check("Qt 焦点查询", False, str(exc))

    def s4_type(self):
        """往输入框里塞字，确认按钮会亮（控件链路是通的）。"""
        print("\n【4】输入内容 → 发送按钮应变亮")
        self.ov.edit.setText("这句里的 face the music 是什么语气？")
        self.qapp.processEvents()
        time.sleep(0.3)
        self.app.processEvents()
        check("发送按钮已可用", bool(self.ov.btn_send.isEnabled()))
        check("输入框显示了我打的字",
              self.ov.edit.text().endswith("语气？"), self.ov.edit.text())

    def s5_finish(self):
        os.system(f'screencapture -x "{OUT}/点输入框后可打字.png" 2>/dev/null')
        print(f"\n截图已存 → {OUT}/点输入框后可打字.png")
        self.app.quit()


def main() -> int:
    qapp = QApplication([sys.argv[0]])
    print("=" * 62)
    print("真机验证：点浮窗输入框 → 能否进入输入态")
    print("=" * 62)
    rig = Rig(qapp, qapp)
    rig.start()
    qapp.exec()

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n共 {len(RESULTS)} 项，失败 {len(bad)}")
    for n in bad:
        print("  ❌", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
