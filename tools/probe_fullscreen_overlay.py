#!/usr/bin/env python3
"""判定实验：让窗口盖在「别的 App 的全屏」之上。

背景
----
macOS 的原生全屏不是"把窗口放大铺满屏幕"，而是**新建一个独占的 Space**。
窗口默认只活在它创建那一刻所在的 Space 里，跨不过去。想跨过去要靠
`collectionBehavior` 和 `window level` 的组合，而这两个东西在 macOS 15+
上到底哪套组合还有效，只能实测 —— 文档没写，社区案例互相矛盾。

所以这个脚本把候选组合做成一个自动轮播：**窗口上会写明自己是哪个变体**。
你能看到它，就说明这个变体生效了。

用法
----
    python tools/probe_fullscreen_overlay.py              # 循环轮播，Ctrl-C 停
    python tools/probe_fullscreen_overlay.py --selftest    # 只验证脚本本身能否建窗
    python tools/probe_fullscreen_overlay.py --hold 8 --loops 0

看什么
------
①先看「基准」那一屏：那时候你应该在桌面上，一定能看到 —— 看不到说明脚本有问题；
②然后切到全屏的 IINA（三指滑 / Ctrl+←），盯着画面看轮播；
③只要看到任何一屏，**记住它写的编号**（V1/V2/…）告诉我。

脚本会把每一屏的编号和实际生效的 level / collectionBehavior 同时打到终端和
/tmp/dl_probe_fullscreen.log —— 万一窗口根本没出现，日志也能告诉我们原因。
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

LOG_PATH = "/tmp/dl_probe_fullscreen.log"


# ----------------------------------------------------------------- 常量
# AppKit 的 NSWindowLevel。用裸数字是为了不依赖 pyobjc 导出哪些名字，
# 真实值会在启动时和 AppKit 对一遍（见 _dump_levels）。
LEVEL_STATUS = 25          # NSStatusWindowLevel —— 现状用的就是它
LEVEL_POPUP = 101          # NSPopUpMenuWindowLevel
LEVEL_SCREENSAVER = 1000   # NSScreenSaverWindowLevel

try:
    from AppKit import (
        NSWindowCollectionBehaviorCanJoinAllSpaces as BEH_CANJOIN,
        NSWindowCollectionBehaviorFullScreenAuxiliary as BEH_FSAUX,
        NSWindowCollectionBehaviorIgnoresCycle as BEH_IGNORECYCLE,
        NSWindowCollectionBehaviorStationary as BEH_STATIONARY,
    )
except Exception as exc:                                   # pragma: no cover
    print("AppKit 不可用，这个实验必须在真机桌面会话里跑：", exc)
    sys.exit(2)

# 现状那套（overlay.py 的 _float_over_everything 就是这四个 flag）
BEH_CURRENT = BEH_CANJOIN | BEH_FSAUX | BEH_STATIONARY | BEH_IGNORECYCLE
# 社区案例里"只留 fullScreenAuxiliary"的那种写法
BEH_ONLY_FS = BEH_FSAUX

VARIANTS: list[dict] = [
    {"tag": "V1", "level": LEVEL_STATUS, "beh": BEH_CURRENT,
     "reassert": False, "desc": "现状：level=25 + 四flag"},
    {"tag": "V2", "level": LEVEL_POPUP, "beh": BEH_CURRENT,
     "reassert": False, "desc": "level=101 + 四flag"},
    {"tag": "V3", "level": LEVEL_SCREENSAVER, "beh": BEH_CURRENT,
     "reassert": False, "desc": "level=1000 + 四flag"},
    {"tag": "V4", "level": LEVEL_POPUP, "beh": BEH_ONLY_FS,
     "reassert": False, "desc": "level=101 + 只 fsaux"},
    {"tag": "V5", "level": LEVEL_SCREENSAVER, "beh": BEH_ONLY_FS,
     "reassert": False, "desc": "level=1000 + 只 fsaux"},
    {"tag": "V6", "level": LEVEL_SCREENSAVER, "beh": BEH_ONLY_FS,
     "reassert": True, "desc": "level=1000 + 只 fsaux + 每秒重设"},
    {"tag": "V7", "level": LEVEL_STATUS, "beh": BEH_CURRENT,
     "reassert": True, "desc": "现状四flag + 每秒重设"},
]
BASELINE = {"tag": "基准", "level": LEVEL_STATUS, "beh": BEH_CURRENT,
            "reassert": False, "desc": "桌面基准（一定看得到）"}


def say(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _cocoa() -> bool:
    """只有真·Cocoa 平台下 winId() 才是真的 NSView 指针。

    离屏/最小平台下把它交给 pyobjc 是**直接段错误**，Python 拦不住。
    """
    return (QGuiApplication.platformName() or "").lower() == "cocoa"


def _ns_window(widget):
    import objc
    from ctypes import c_void_p

    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window() if view is not None else None


def _top_fullscreen_owner() -> str:
    """最上层那个「铺满整屏」的普通窗口属于谁。

    用来判断前台 App 到底是不是**原生全屏**（占用独立 Space 的那种），
    而不是只是把窗口拉大铺满。只看 layer 0 的普通窗口，
    我们自己的浮窗是 layer 25，不在考虑范围内。
    """
    try:
        from AppKit import NSScreen
        from Quartz import (CGWindowListCopyWindowInfo,
                            kCGNullWindowID,
                            kCGWindowListOptionOnScreenOnly)

        scr = NSScreen.mainScreen()
        if scr is None:
            return ""
        frame = scr.frame()
        infos = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
        for w in infos:
            if w.get("kCGWindowLayer", 1) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            if (b.get("Width", 0) >= frame.size.width - 2
                    and b.get("Height", 0) >= frame.size.height - 2):
                return str(w.get("kCGWindowOwnerName") or "?")
    except Exception:
        pass
    return ""


def _dump_levels() -> None:
    """把裸数字和 AppKit 里的真实常量对一遍，防止我记错。"""
    try:
        from AppKit import (NSPopUpMenuWindowLevel, NSScreenSaverWindowLevel,
                            NSStatusWindowLevel)

        say(f"AppKit 层级常量核对：status={NSStatusWindowLevel} "
            f"popup={NSPopUpMenuWindowLevel} screensaver={NSScreenSaverWindowLevel}")
    except Exception as exc:
        say(f"（没能读到 AppKit 层级常量，直接用裸数字）{exc}")
    try:
        from AppKit import (NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorFullScreenAuxiliary,
                            NSWindowCollectionBehaviorIgnoresCycle,
                            NSWindowCollectionBehaviorStationary)

        say("AppKit collectionBehavior 常量："
            f"canJoinAllSpaces={NSWindowCollectionBehaviorCanJoinAllSpaces} "
            f"fullScreenAuxiliary={NSWindowCollectionBehaviorFullScreenAuxiliary} "
            f"stationary={NSWindowCollectionBehaviorStationary} "
            f"ignoresCycle={NSWindowCollectionBehaviorIgnoresCycle}")
    except Exception as exc:
        say(f"（没能读到 collectionBehavior 常量）{exc}")


def apply_variant(win, v: dict) -> None:
    """按变体设置窗口，并把**实际生效的值读回来**。

    读回来这一步是关键：如果 Qt 在 show 之后又自己设了一遍窗口属性，
    我们设的值就被覆盖了 —— 只看"调用没报错"是发现不了的。
    """
    ns = _ns_window(win)
    if ns is None:
        say("  ✗ 拿不到 NSWindow")
        return
    ns.setCollectionBehavior_(v["beh"])
    ns.setLevel_(v["level"])
    try:
        ns.setHidesOnDeactivate_(False)
        ns.setCanHide_(False)
    except Exception:
        pass
    ns.orderFrontRegardless()

    try:
        got_level = ns.level()
        got_beh = ns.collectionBehavior()
        on_space = ns.isOnActiveSpace()
    except Exception as exc:
        say(f"  （读回失败：{exc}）")
        return
    flag = "一致" if (got_level == v["level"] and got_beh == v["beh"]) else "★不一致"
    say(f"  读回：level={got_level}（期望 {v['level']}）"
        f" behavior={got_beh}（期望 {v['beh']}）"
        f" 在当前Space={bool(on_space)} → {flag}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=8.0, help="每个变体停留秒数")
    ap.add_argument("--loops", type=int, default=0, help="跑几轮，0 = 一直循环")
    ap.add_argument("--selftest", action="store_true",
                    help="只建窗 1.5 秒并打印诊断，用于确认脚本能在真机跑起来")
    ap.add_argument("--levels", default="",
                    help="只轮播这些层级，逗号分隔（如 25,1000）。"
                         "behavior 固定用现状那套四 flag —— 适合"
                         "「换一种全屏方式后，看看还需要多高的层级」这种复测。")
    args = ap.parse_args()

    # Ctrl-C 直接结束进程（Qt 的事件循环会吞掉 SIGINT）
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    say("=" * 62)
    say(f"全屏可见性判定实验启动｜平台={QGuiApplication.platformName()}")
    _dump_levels()
    if not _cocoa():
        say("★ 当前不是 cocoa 平台，本实验没有意义（需要在真机桌面会话跑）")
    say(f"日志同时写入 {LOG_PATH}")

    win = QWidget(
        None,
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool                       # → NSPanel
        | Qt.WindowType.WindowDoesNotAcceptFocus   # 跟真实浮窗一致
    )
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    win.setWindowTitle("全屏可见性判定实验")

    card = QFrame(win)
    card.setStyleSheet("background:#E24B4A;border-radius:14px;")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(24, 16, 24, 16)
    lay.setSpacing(6)
    lbl_tag = QLabel("准备中")
    lbl_tag.setStyleSheet(
        "color:#ffffff;font-size:34px;font-weight:700;background:transparent;"
        "font-family:'PingFang SC','Helvetica Neue',sans-serif;")
    lbl_desc = QLabel("")
    lbl_desc.setStyleSheet(
        "color:rgba(255,255,255,0.92);font-size:15px;background:transparent;"
        "font-family:'PingFang SC','Helvetica Neue',sans-serif;")
    lay.addWidget(lbl_tag)
    lay.addWidget(lbl_desc)

    outer = QVBoxLayout(win)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(card)

    W, H = 700, 152
    scr = QGuiApplication.primaryScreen()
    geo = scr.geometry() if scr is not None else None
    if geo is not None:
        win.setFixedSize(W, H)
        win.move(geo.x() + (geo.width() - W) // 2, geo.y() + 70)

    win.show()
    say("窗口已 show()")

    state = {"idx": -1, "reassert_on": False}

    def show_slot(v: dict) -> None:
        lbl_tag.setText(f"{v['tag']}")
        lbl_desc.setText(v["desc"])
        state["reassert_on"] = bool(v["reassert"])
        say(f"→ 现在显示 {v['tag']}：{v['desc']}")
        apply_variant(win, v)

    if args.levels:
        lvls = []
        for part in args.levels.split(","):
            part = part.strip()
            if part:
                lvls.append(int(part))
        seq = [{"tag": str(n), "level": lv, "beh": BEH_CURRENT,
                "reassert": False,
                "desc": f"level={lv}（behavior 用现状那套四 flag）"}
               for n, lv in enumerate(lvls, 1)]
        say(f"--levels 模式：只轮播 {lvls}")
    else:
        seq = [BASELINE] + VARIANTS

    def advance() -> None:
        state["idx"] += 1
        if state["idx"] >= len(seq):
            state["idx"] = 0
            state["loop"] = state.get("loop", 0) + 1
            if args.loops and state["loop"] >= args.loops:
                say("跑完了，退出")
                app.quit()
                return
            say(f"—— 第 {state['loop'] + 1} 轮 ——")
        show_slot(seq[state["idx"]])
        QTimer.singleShot(int(args.hold * 1000), advance)

    # 「每秒重设」那条变量：持续把 level/behavior 再按一遍，
    # 用来验证"是不是被系统/Qt 在运行中覆盖掉了"。
    #
    # 顺带做心跳：记录"窗口认为自己**在不在当前 Space**"+"当前前台是哪个 App"。
    # 这条信息比肉眼看更硬 —— 你切到全屏 IINA 时，如果日志里
    # `窗口在当前Space=False`，说明窗口压根没跟过去（Space 归属没生效）；
    # 如果是 True 而你还是看不见，那就是层级问题（被视频压在下面）。
    hb: dict = {"n": 0, "last": None}

    def tick() -> None:
        if state["reassert_on"] and 0 <= state["idx"] < len(seq):
            apply_variant(win, seq[state["idx"]])
        hb["n"] += 1
        if hb["n"] % 2:          # 每 2 秒记一次，只在变化时落日志
            return
        ns = _ns_window(win)
        try:
            on = bool(ns.isOnActiveSpace())
            lvl = ns.level()
        except Exception:
            return
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            front = app.bundleIdentifier() if app is not None else "?"
        except Exception:
            front = "?"
        top = _top_fullscreen_owner()
        sig = (on, lvl, front, top)
        if sig != hb["last"]:
            hb["last"] = sig
            say(f"● 心跳：窗口在当前Space={on} level={lvl} 前台={front} "
                f"铺满整屏的窗口属于={top or '（无）'}")

    guard = QTimer()
    guard.timeout.connect(tick)
    guard.start(1000)

    if args.selftest:
        say("--selftest：只显示 1.5 秒")
        QTimer.singleShot(1500, app.quit)
    else:
        say("先看「基准」那一屏（此时你应该在桌面上）。"
            "随后切到全屏的 IINA，盯着画面看轮播，记住看到的编号。")
        QTimer.singleShot(300, advance)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
