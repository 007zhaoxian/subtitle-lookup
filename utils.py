"""日志 + macOS 权限检查工具。"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import config

_LOGGER: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """全局 logger：写文件（.app 无控制台时唯一能看到的地方）＋ stderr。"""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger("doubao_lookup")
    # 排查时想要更啰嗦的日志（含每次「为什么没弹面板」这类判断）：
    #   DL_DEBUG=1 /Applications/豆包查词.app/Contents/MacOS/DoubaoLookup
    _debug = config.VERSION.endswith("dev") or os.environ.get(
        "DL_DEBUG", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    logger.setLevel(logging.DEBUG if _debug else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s %(message)s", "%H:%M:%S"
    )
    try:
        fh = RotatingFileHandler(
            config.LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass

    # .app 由 Finder 启动时 stderr 无处可去，加 handler 也无害
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    _LOGGER = logger
    return logger


log = get_logger()


# ------------------------------------------------------------------ 权限
def is_accessibility_trusted() -> bool:
    """检查是否授予了『辅助功能』权限（pynput 监听键盘依赖它）。"""
    try:
        from ApplicationServices import AXIsProcessTrusted  # pyobjc

        return bool(AXIsProcessTrusted())
    except Exception:
        pass
    # 没有 pyobjc 时退化为 ctypes 调用
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("ApplicationServices")
        if not path:
            return True  # 无法判断时不阻塞用户
        lib = ctypes.cdll.LoadLibrary(path)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return True


def prompt_accessibility() -> bool:
    """主动触发系统的『辅助功能』授权弹窗。

    光调 AXIsProcessTrusted() 只是**查询**，不会弹窗、也不会把 App 加进
    系统设置列表；必须用 AXIsProcessTrustedWithOptions 并带上
    kAXTrustedCheckOptionPrompt=True，macOS 才会弹「是否允许…」并把
    本 App 自动登记到「隐私与安全性 → 辅助功能」里。
    没有这一步，用户会找不到要授权哪个 App。
    """
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception as exc:
        log.debug("AXIsProcessTrustedWithOptions 不可用: %s", exc)
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("ApplicationServices")
        if not path:
            return is_accessibility_trusted()
        lib = ctypes.cdll.LoadLibrary(path)
        lib.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        lib.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        # 构造 CFDictionary { kAXTrustedCheckOptionPrompt: true } 用 pyobjc 更稳，
        # ctypes 下退化为只查询，不弹窗。
        return is_accessibility_trusted()
    except Exception:
        return is_accessibility_trusted()


def open_privacy_pane(anchor: str) -> None:
    """打开系统设置中对应的隐私面板。

    anchor: Privacy_Accessibility / Privacy_ScreenCapture / Privacy_ListenEvent
    """
    url = f"x-apple.systempreferences:com.apple.preference.security?{anchor}"
    try:
        subprocess.run(["/usr/bin/open", url], check=False)
    except Exception as exc:  # pragma: no cover
        log.warning("打开系统设置失败: %s", exc)


PERMISSION_HINT = (
    "需要在『系统设置 → 隐私与安全性』中为本应用授权：\n"
    "  1) 辅助功能 (Accessibility)  —— 监听键盘（捕捉你按下的触发键）\n"
    "  2) 输入监控 (Input Monitoring) —— 部分 macOS 版本同样需要\n"
    "  3) 屏幕录制 (Screen Recording) —— 全屏截图\n"
    "授权后请完全退出并重新启动本应用（TCC 权限只在进程启动时生效）。"
)


def set_dock_icon_visible(visible: bool) -> None:
    """动态显示/隐藏 Dock 图标。

    为什么要这么绕：Info.plist 里若写 LSUIElement=True，App 就不会出现在
    启动台里（用户根本搜不到）；写 False 又会让 Dock 常驻一个图标。
    所以 plist 用 False 保证「启动台可见 / Spotlight 可搜」，
    运行时再把激活策略切成 Accessory 把 Dock 图标收起来 —— 两全其美。
    """
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSApplicationActivationPolicyRegular,
        )

        policy = (
            NSApplicationActivationPolicyRegular
            if visible
            else NSApplicationActivationPolicyAccessory
        )
        NSApplication.sharedApplication().setActivationPolicy_(policy)
        log.debug("Dock 图标 %s", "显示" if visible else "隐藏")
    except Exception as exc:
        log.debug("切换激活策略失败（不影响功能）: %s", exc)


def _coregraphics():
    """用 ctypes 直接调 CoreGraphics，省掉 pyobjc-framework-Quartz 依赖。"""
    import ctypes
    import ctypes.util

    path = (
        ctypes.util.find_library("CoreGraphics")
        or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    return ctypes.cdll.LoadLibrary(path)


def screen_capture_authorized() -> bool:
    """是否已拿到『屏幕录制』权限（macOS 10.15+）。判断不了时返回 True。"""
    try:
        import ctypes

        lib = _coregraphics()
        lib.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(lib.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def request_screen_capture() -> None:
    """触发系统原生授权弹窗，并把本 App 自动加进「屏幕录制」列表。"""
    try:
        import ctypes

        lib = _coregraphics()
        lib.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        lib.CGRequestScreenCaptureAccess()
    except Exception as exc:
        log.debug("CGRequestScreenCaptureAccess 不可用: %s", exc)
    open_privacy_pane("Privacy_ScreenCapture")


_NOTIFIER = None


def set_notifier(fn) -> None:
    """注册一个原生通知回调（Qt 起来后用 QSystemTrayIcon.showMessage）。

    为什么不用 osascript：`display notification` 发出的通知署名是
    「脚本编辑器」，用户看到来源会困惑；而且它每次都要起一个子进程。
    osascript 保留为兜底（Qt 没起来时用）。
    """
    global _NOTIFIER
    _NOTIFIER = fn


def notify(title: str, subtitle: str, text: str) -> None:
    """弹一条系统通知：优先 Qt 原生，退化到 osascript。"""
    if _NOTIFIER is not None:
        try:
            _NOTIFIER(title, text)
            return
        except Exception:
            pass

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{_esc(text)}" '
        f'with title "{_esc(title)}" subtitle "{_esc(subtitle)}"'
    )
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False,
                       capture_output=True, timeout=5)
    except Exception:
        pass


_LOCK_FH = None


_CRASH_GUARD_ON = False


def install_crash_guard() -> None:
    """装一个"槽里出错也不许退出程序"的保险。**必须在 QApplication 之前调用。**

    为什么非它不可（实测，别删）
    ----------------------------
    2026-09-15 用户反复反馈「App 时常意外退出」，崩溃报告给的是：

        sipWrapper_dealloc → QWidget::~QWidget → QObject::destroyed
          → PyQtSlotProxy::unislot → pyqt6_err_print
          → QMessageLogger::fatal → abort()        （SIGABRT）

    PyQt6 的规矩和 PyQt5 不一样：**任何一个 Python 槽里漏出来的异常，都会被
    升级成 qFatal() → abort()，整个进程当场死掉**。而 Qt 的槽遍布各处
    （定时器、按钮、`destroyed` 信号、event loop 回调），随便哪个角落有个
    没考虑到的边界，用户看到的就是"它自己退了"。

    实测对比（同一个抛异常的槽）：

        不装钩子 → 退出码 134 (SIGABRT)
        装了钩子 → 进程照常活着，异常只进日志

    所以这里把 `sys.excepthook` / `threading.excepthook` 换成"只记日志、
    绝不 re-raise"的版本。有它兜底，即使将来某个槽里再漏异常，最坏也只是
    这一次操作没生效，而不是 App 凭空消失。

    ⚠️ 它只是**安全网**，不是"可以不写 try/except"的借口 —— 真正的槽里该
       包的还是要包。安全网的意义是让用户永远看不到"莫名退出"。
    """
    global _CRASH_GUARD_ON
    if _CRASH_GUARD_ON:
        return
    _CRASH_GUARD_ON = True

    def _log_exc(kind: str, exc_type, exc, tb) -> None:
        try:
            import traceback

            text = "".join(traceback.format_exception(exc_type, exc, tb))
            log.error("%s里出现未处理异常（已拦下，程序继续运行）:\n%s",
                      kind, text)
        except Exception:
            pass

    def _excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log_exc("主线程", exc_type, exc, tb)

    def _thread_hook(args) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        _log_exc(f"线程 {getattr(args.thread, 'name', '?')}",
                 args.exc_type, args.exc_value, args.exc_traceback)

    try:
        sys.excepthook = _excepthook
    except Exception:
        pass
    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass
    # 用 INFO：这条是"App 莫名退出"类问题的第一判据 —— 日志里没有它，
    # 就说明这一版没跑到保险就挂了（或者根本没装）。
    log.info("已安装崩溃保险（槽内异常不再导致 App 退出）")


def acquire_single_instance_lock() -> bool:
    """确保同一时间只跑一份本 App。返回 False = 已有一份在跑。

    为什么必须（理由在 v1.2.2 换过，结论没变）：用户双击两次图标（非常常见）
    就会开出第二份 App，两份会**抢同一个全局空格 CGEventTap** —— 后起来的那份
    把前一份的"有条件的吞键"逻辑顶掉，于是空格时而查词、时而漏到播放器上，
    看起来就是"功能时好时坏"；日志文件也会互相覆盖。用文件锁把第二份挡住。
    """
    global _LOCK_FH
    try:
        import fcntl

        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = open(config.LOG_DIR / "app.lock", "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.write(f"pid={os.getpid()}\n")
        fh.flush()
        _LOCK_FH = fh      # 必须持有引用，否则文件对象被回收 = 锁被释放
        return True
    except Exception as exc:
        log.debug("单实例锁不可用（忽略，不影响使用）: %s", exc)
        return True
