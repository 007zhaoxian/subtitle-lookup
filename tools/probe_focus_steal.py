"""实测：浮窗显示时会不会把「前台 App」抢走。

【为什么需要这个探针】
用户反馈：浮窗弹出后顶部应用变成了本程序，再按空格播放器收不到，
必须先用鼠标点一下播放器。代码注释里也承认「查词过程中任何原因让本 App 被
激活都会触发一次」—— 但我们一直没实测过"到底激活没有"。

本探针就是在真·cocoa 平台下，把几种窗口写法逐个跑一遍，每次都先强行把
Finder 拉到前台，再显示窗口，然后读 `NSWorkspace.frontmostApplication()`
看前台有没有变成我们。

判定标准：
  · frontmost 仍是 Finder  → 不抢焦点（✅ 这就是我们要的）
  · frontmost 变成 Python  → 抢走了（❌）

用法：QT_QPA_PLATFORM 必须是 cocoa（默认），否则测不出真东西。
    .venv/bin/python tools/probe_focus_steal.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("QT_QPA_PLATFORM", None)      # 必须是真 cocoa，offscreen 测不出来

from PyQt6.QtCore import Qt                          # noqa: E402
from PyQt6.QtWidgets import (                        # noqa: E402
    QApplication, QFrame, QLabel, QVBoxLayout, QWidget,
)

import config                                        # noqa: E402

# NSWindowStyleMaskNonactivatingPanel —— 让 NSPanel 能当 key window 但**不激活 App**
NS_NONACTIVATING_PANEL = 1 << 7


def _ns():
    from AppKit import NSApplication, NSWorkspace
    return NSApplication, NSWorkspace


def frontmost_name() -> str:
    _, NSWorkspace = _ns()
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.localizedName()) if app else "(未知)"
    except Exception as exc:
        return f"(读取失败 {exc})"


def app_is_active() -> bool:
    NSApplication, _ = _ns()
    try:
        return bool(NSApplication.sharedApplication().isActive())
    except Exception:
        return False


def activate_finder() -> None:
    """把 Finder 拉到前台，作为"播放器"的替身。"""
    _, NSWorkspace = _ns()
    try:
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.bundleIdentifier() == "com.apple.finder":
                app.activateWithOptions_(1 << 1)
                break
    except Exception:
        pass


def pump(seconds: float) -> None:
    """跑事件循环 N 秒，让 Cocoa 把该发的通知都发完。"""
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def ns_window(widget):
    import objc
    from ctypes import c_void_p

    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window() if view is not None else None


class Card(QWidget):
    """和真实浮窗同款的窗口（标志按变体传进来）。"""

    def __init__(self, flags) -> None:
        super().__init__()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        card = QFrame(self)
        card.setStyleSheet(
            "background-color: rgba(22,24,30,0.92); border-radius: 14px;")
        lay = QVBoxLayout(card)
        lab = QLabel("焦点探针")
        lab.setStyleSheet("color:#EAECEF;font-size:14px;")
        lay.addWidget(lab)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)
        self.resize(320, 90)


BASE_FLAGS = (Qt.WindowType.FramelessWindowHint
              | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.Tool)


def variant(name: str, flags, *, nonactivating: bool,
            key_only_if_needed: bool | None, level: bool,
            step: str = "full") -> dict:
    """跑一个变体：拉 Finder 到前台 → 显示窗口 → 读前台是谁。

    step 用来**逐级隔离**到底哪个调用把 App 激活了：
      · "noop"   什么都不做（对照组，确认基线没被抢）
      · "show"   只 show()
      · "raise"  show() + raise_()
      · "full"   再 + orderFrontRegardless()（= 真实代码的做法）
    """
    activate_finder()
    pump(0.7)
    before = frontmost_name()

    w = Card(flags)
    win = None
    if step != "noop":
        w.show()
        if step in ("raise", "full"):
            w.raise_()

    if step in ("full", "front"):
        win = ns_window(w)
        if win is not None:
            if nonactivating:
                try:
                    win.setStyleMask_(int(win.styleMask()) | NS_NONACTIVATING_PANEL)
                except Exception as exc:
                    print(f"    setStyleMask 失败: {exc}")
            if key_only_if_needed is not None:
                try:
                    win.setBecomesKeyOnlyIfNeeded_(bool(key_only_if_needed))
                except Exception as exc:
                    print(f"    setBecomesKeyOnlyIfNeeded 失败: {exc}")
            if level:
                try:
                    from AppKit import NSStatusWindowLevel
                    from AppKit import (
                        NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary,
                        NSWindowCollectionBehaviorIgnoresCycle,
                        NSWindowCollectionBehaviorStationary,
                    )
                    win.setCollectionBehavior_(
                        NSWindowCollectionBehaviorCanJoinAllSpaces
                        | NSWindowCollectionBehaviorFullScreenAuxiliary
                        | NSWindowCollectionBehaviorStationary
                        | NSWindowCollectionBehaviorIgnoresCycle)
                    win.setLevel_(NSStatusWindowLevel)
                except Exception as exc:
                    print(f"    置顶设置失败: {exc}")
            try:
                win.orderFrontRegardless()      # = 真实代码里的做法
            except Exception as exc:
                print(f"    orderFrontRegardless 失败: {exc}")

    pump(1.2)
    after = frontmost_name()
    active = app_is_active()
    is_key = False
    if win is None and step != "noop":
        win = ns_window(w)
    if win is not None:
        try:
            is_key = bool(win.isKeyWindow())
        except Exception:
            pass

    w.close()
    w.deleteLater()
    pump(0.5)

    stolen = after.lower().startswith("python")
    print(f"  {name}")
    print(f"    前台：{before}  →  {after}    {'❌ 被抢走' if stolen else '✅ 没抢'}")
    print(f"    NSApp.isActive={active}   我们的窗口 isKeyWindow={is_key}")
    print()
    return {"name": name, "stolen": stolen, "after": after}


def main() -> int:
    app = QApplication(sys.argv)
    print("=" * 68)
    print("浮窗抢焦点实测（每个变体都先把 Finder 拉到前台，再显示窗口）")
    print("=" * 68)
    print()

    results = []

    results.append(variant(
        "V1  只 show()（对照组，确认 show 本身不抢）",
        BASE_FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus,
        nonactivating=False, key_only_if_needed=None, level=False,
        step="show"))

    results.append(variant(
        "V2  show() + raise_()  ← 已定位为元凶",
        BASE_FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus,
        nonactivating=False, key_only_if_needed=None, level=False,
        step="raise"))

    results.append(variant(
        "V3  实测可疑项：show() + raise_() + orderFrontRegardless（现状代码）",
        BASE_FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus,
        nonactivating=False, key_only_if_needed=None, level=False,
        step="full"))

    results.append(variant(
        "V4  ★ 候选修复：show() + orderFrontRegardless()，**不调 raise_**",
        BASE_FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus,
        nonactivating=True, key_only_if_needed=True, level=True,
        step="front"))

    results.append(variant(
        "V5  ★ 候选修复：同上，但不设 NonactivatingPanel（只用 orderFrontRegardless）",
        BASE_FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus,
        nonactivating=False, key_only_if_needed=True, level=True,
        step="front"))

    print("=" * 68)
    print("结论")
    print("=" * 68)
    for r in results:
        flag = "❌ 抢焦点" if r["stolen"] else "✅ 不抢"
        print(f"  {flag}   {r['name'].split()[0]}  →  前台={r['after']}")
    print()

    ok = [r for r in results if not r["stolen"]]
    if ok:
        print(f"可行方案（不抢焦点）：{', '.join(r['name'].split()[0] for r in ok)}")
    else:
        print("⚠️ 所有变体都抢焦点 —— 需要换思路（改用 CGEventPostToPid 直投播放器）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
