"""A/B 矩阵：到底是哪个 window flag 把键盘挡在门外。

背景：浮窗 enter_input() 之后，Qt 与 NS 层都显示"焦点在输入框上"，
但真实按键就是进不去。所以逐个 flag 试，找出真凶。

用法:  python tools/probe_key_matrix.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DL_SETTINGS_DIR", "/tmp/dl_focus_probe")
os.makedirs("/tmp/dl_focus_probe", exist_ok=True)

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QLineEdit, QVBoxLayout, QWidget,
)

RESULTS: list[tuple[str, bool, str]] = []


def post_key(ch: str = "a") -> None:
    import Quartz

    code = {"a": 0, "b": 11, "c": 8}.get(ch, 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateKeyboardEvent(None, code, True))
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateKeyboardEvent(None, code, False))


def nswin(w):
    import objc
    from ctypes import c_void_p

    return objc.objc_object(c_void_p=int(w.winId())).window()


class Win(QWidget):
    def __init__(self, flags, translucent=True, show_without_activating=True):
        super().__init__()
        self.setWindowFlags(flags)
        if translucent:
            self.setAttribute(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt
                              .WidgetAttribute.WA_TranslucentBackground, True)
        if show_without_activating:
            self.setAttribute(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt
                              .WidgetAttribute.WA_ShowWithoutActivating, True)
        lay = QVBoxLayout(self)
        self.ed = QLineEdit()
        lay.addWidget(self.ed)


def apply_float(win) -> None:
    """复刻 overlay._float_over_everything 的效果（level / collectionBehavior）。"""
    try:
        import objc
        from AppKit import (
            NSStatusWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )

        w = nswin(win)
        w.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        w.setLevel_(NSStatusWindowLevel)
        w.setHidesOnDeactivate_(False)
        w.setCanHide_(False)
        w.orderFrontRegardless()
    except Exception:
        pass


class Runner:
    def __init__(self, app):
        self.app = app
        self.cases: list[tuple[str, Win]] = []
        self.i = 0
        self.cur = None

    def add(self, name: str, win: Win):
        self.cases.append((name, win))

    def start(self):
        QTimer.singleShot(200, self.next_case)

    def next_case(self):
        if self.i >= len(self.cases):
            self.finish()
            return
        name, win = self.cases[self.i]
        self.cur = (name, win)
        win.show()
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            w = nswin(win)
            w.makeKeyAndOrderFront_(None)
        except Exception:
            pass
        win.activateWindow()
        win.ed.setFocus()
        QTimer.singleShot(700, self.do_post)

    def do_post(self):
        name, win = self.cur
        post_key("a")
        QTimer.singleShot(900, self.check)

    def check(self):
        name, win = self.cur
        txt = win.ed.text()
        ok = (txt == "a")
        RESULTS.append((name, ok, f"edit={txt!r}"))
        print(f"  {'✅' if ok else '❌'} {name:52} edit={txt!r}")
        win.close()
        win.deleteLater()
        self.i += 1
        QTimer.singleShot(250, self.next_case)

    def finish(self):
        self.app.quit()


def main() -> int:
    from PyQt6.QtCore import Qt

    app = QApplication([sys.argv[0]])
    r = Runner(app)

    F = Qt.WindowType
    base_floating = F.FramelessWindowHint | F.WindowStaysOnTopHint

    # ① 基线：最普通的窗口（证明"模拟按键"这条路本身是通的）
    r.add("① 基线 普通窗口（无特殊 flag）", Win(F.Window))

    # ② 当前实现：Tool + DoesNotAcceptFocus（enter 时清掉 DNDF）
    w = Win(base_floating | F.Tool | F.WindowDoesNotAcceptFocus)
    r.add("② Tool + 清DNDF（当前实现）", w)

    # ③ 去掉 Tool
    w = Win(base_floating | F.WindowDoesNotAcceptFocus)
    r.add("③ 无 Tool + 清DNDF", w)

    # ④ 当前实现 + 加上置顶 NSWindow 效果（level=25 等）
    w = Win(base_floating | F.Tool | F.WindowDoesNotAcceptFocus)
    r.add("④ Tool + 清DNDF + NSWindow置顶", w)

    print("=" * 74)
    print("window flag A/B 矩阵：哪个 flag 挡住了键盘")
    print("=" * 74)
    # 给②④加置顶（在 show 之前挂钩）
    _orig_next = r.next_case

    def patched_next():
        if r.i < len(r.cases):
            name, win = r.cases[r.i]
            if "NSWindow置顶" in name:
                win.show()
                apply_float(win)
        _orig_next()

    r.next_case = patched_next  # type: ignore[assignment]

    r.start()
    app.exec()

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 74)
    print(f"共 {len(RESULTS)} 例，键盘进不去 {len(bad)} 例")
    for n in bad:
        print(f"   ❌ {n}")
    if RESULTS and RESULTS[0][1]:
        print("（① 通过 = 模拟按键通路正常，上面的失败是真的被 flag 挡住了）")
    elif RESULTS:
        print("（① 也失败 = 模拟按键这条路本身就不通，本轮结论无效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
