"""空格键的**有条件**拦截（CGEventTap）—— 三态交互的最后一环。

为什么非它不可
--------------
用户要的三态交互是：

    ① 视频在播   → 按空格：暂停、截图查词、弹浮窗
    ② 浮窗在显示 → 按空格：关掉浮窗、继续播放
    ③ 视频已停   → 按空格：继续播放

不管哪一态，「这一下空格」都必须**只被处理一次**。
如果既让空格原样送到播放器（播放器自己暂停一次）、我们又主动发一次暂停
指令，就成了双触发 —— 暂停完立刻又播回去，来回抵消。
所以必须把它吃掉。

但 pynput 的 `suppress=True` 是**全局无条件吞键**：只要看剧模式开着，
用户在任何一个应用里都打不出空格。绝对不能接受。

于是用 CGEventTap 做**有条件**的拦截，判据只有两条（见
`app.App.space_should_intercept`）：
    · 浮窗正显示 / 正在识别 / 正在等追问 → 这个空格归我们；
    · 否则，只有当前**前台应用是已知播放器**（IINA / Chrome / Edge / VLC…）
      才吃掉。
其余情况（Finder、Word、Slack、浏览器里的输入框……）一律原样放行，
空格该怎么用怎么用。

⚠️ 需要辅助功能权限（本程序本来就要）。没有权限时 `CGEventTapCreate`
   返回 None → `available()` 为 False → 调用方自动退回老的 pynput 路径。

⚠️ 回调运行在 tap 自己的 run loop 线程上，**必须极快返回**：
   超过系统给的时间（约 0.25s，超时会收到 kCGEventTapDisabledByTimeout
   并被自动禁用）就会静默失效，表现为"按空格没反应"。
   所以真正的动作一律丢到别的线程去做（见 `_fire`）。

★ v1.2.1：这里**顺便接管了 Esc**（见 ESC_KEYCODE）。
  原来 Esc 是交给 pynput 的键盘监听去听的，而那个监听会**把整个 App 弄崩** ——
  见 `_callback` 里 Esc 分支的注释和 hotkey._avoid_pynput_media_key_crash()。
  现在这一个 tap 同时负责空格与 Esc，pynput 的键盘监听在默认配置下**根本不会创建**。
"""
from __future__ import annotations

import threading
from typing import NamedTuple

import config
from utils import log

# 空格的虚拟键码（Carbon kVK_Space / CGEvent 用的就是它）
SPACE_KEYCODE = 49

# Esc 的虚拟键码（Carbon kVK_Escape）。
# 用它是为了**替掉 pynput 的键盘监听** —— 后者会在收到系统定义事件
# （type 14，媒体键/亮度键那一类）时于事件 tap 线程上创建 NSEvent，
# 进而在 macOS 26 上触发 HIToolbox/TSM 的
# `dispatch_assert_queue` 断言失败 → SIGTRAP → 整个 App 当场消失。
# 详见 hotkey._avoid_pynput_media_key_crash 的完整说明与崩溃报告摘要。
ESC_KEYCODE = 53

# --- CGEventType（数值稳定，写常量省得依赖 Quartz 已导入）---
_EV_KEY_DOWN = 10
_EV_KEY_UP = 11
_EV_TAP_DISABLED_TIMEOUT = 0xFFFFFFFE
_EV_TAP_DISABLED_USERINPUT = 0xFFFFFFFF

# kCGKeyboardEventKeycode：从 CGEvent 里取虚拟键码用的字段号
_KEYCODE_FIELD = 9

# kCGEventFlagMask{Shift,Control,Alternate,Command}（见 CGEventTypes.h）
_FLAG_SHIFT = 1 << 17
_FLAG_CONTROL = 1 << 18
_FLAG_ALTERNATE = 1 << 19
_FLAG_COMMAND = 1 << 20
# 带这些修饰键的空格有别的用途，绝不抢：
#   Cmd+空格 = Spotlight、Ctrl+空格 = 输入法切换、Option+空格 = 特殊空格
_GUARD_FLAGS = _FLAG_COMMAND | _FLAG_CONTROL | _FLAG_ALTERNATE


def available() -> bool:
    """CGEventTap 这条路能不能用（依赖 pyobjc 的 Quartz + 开关）。"""
    if not config.SPACE_TAP_ENABLED:
        return False
    try:
        from Quartz import CGEventTapCreate  # noqa: F401
    except Exception as exc:
        log.debug("CGEventTap 不可用（pyobjc/Quartz 缺失？）: %s", exc)
        return False
    return True


def should_intercept(keycode: int, flags: int, wants: bool) -> bool:
    """**纯函数**（离线可测）：这一下按键该不该被我们吃掉。

    · 只吞空格，其它键一律放行（触发键如果被改成别的字母，那个字母
      由 pynput 负责，这里不碰 —— 别的键本来也不跟播放器冲突）；
    · 带 Cmd / Ctrl / Option 的空格放行；
    · 剩下的看调用方给的意图 `wants`（"此刻这个空格是不是归我们管"）。
    """
    if keycode != SPACE_KEYCODE:
        return False
    if flags & _GUARD_FLAGS:
        return False
    return bool(wants)


class Decision(NamedTuple):
    """一个按键事件的处理结果。"""
    swallow: bool       # 要不要吃掉（False = 原样放行给系统和播放器）
    fire: bool          # 要不要回调 on_space
    down: bool          # 更新后的"这次按下已处理"标记


def decide(etype: int, keycode: int, flags: int, wants: bool,
           down: bool) -> Decision:
    """**纯函数**：给定一个事件和当前状态，返回该怎么处理。

    抽出来是为了能把几个最难复现的边界离线钉死：
      · 长按连发（系统会连着发 keyDown）只能动作一次，否则会
        暂停→播放→暂停……来回抽；
      · keyUp 必须跟着 keyDown 一起吞（只吞一半会漏一个孤儿抬起事件）；
      · 带 Cmd/Ctrl/Option 的空格（输入法切换、Spotlight）**绝不能**抢；
      · 从"该吞"切到"不该吞"时，`down` 要复位，否则下次真按下会被
        误判成长按连发而丢掉动作。
    """
    if keycode != SPACE_KEYCODE:
        return Decision(False, False, down)
    if flags & _GUARD_FLAGS:
        return Decision(False, False, False)
    if not wants:
        return Decision(False, False, False)
    if etype == _EV_KEY_UP:
        return Decision(True, False, False)
    if down:
        return Decision(True, False, True)      # 长按连发的重复：吞掉，但不重复动作
    return Decision(True, True, True)



def esc_should_fire(etype: int, keycode: int, has_cb: bool = True) -> bool:
    """**纯函数**（离线可测）：这一下 Esc 该不该回调。

    只有两条判据，都很要紧：
      · 只认 keyDown —— 若 keyUp 也回调一次，用户按一下 Esc 会被处理两遍
        （浮窗"啪"地连关两层，或者退出输入态 + 关窗一起发生）；
      · `has_cb` 为假（没接回调）时什么都不做 —— 这是唯一一个"我们只是旁听"
        的键，没接就别浪费一次线程。
    """
    if not has_cb:
        return False
    if keycode != ESC_KEYCODE:
        return False
    return etype != _EV_KEY_UP


def tap_mask() -> int:
    """要挂到事件流上的监听掩码：**只监听 keyDown / keyUp**。

    ⚠️ 这里踩过一个坑，别再改回去：`kCGEventTapDisabledByTimeout`(0xFFFFFFFE)
       和 `kCGEventTapDisabledByUserInput`(0xFFFFFFFF) 是**事件类型编号**，
       不是"位号"。曾经写过 `(1 << _EV_TAP_DISABLED_TIMEOUT)` ——
       那是在构造一个 2^4294967294 的整数，等于要分配 512MB 内存：
         · 启动时会白卡 ~30 秒（两次移位就是 1GB）；
         · 结果掩码远超 64 位，CGEventTapCreate 直接抛
           `ValueError: depythonifying 'unsigned long long'`，
           表现是**空格拦截整个失效**（日志里只有一行 ERROR，很难联想）。
       而且这两个"被系统禁用"的通知**不需要写进掩码**：系统会无条件把它们
       投给回调（见 _callback 里的分支）。
    """
    return (1 << _EV_KEY_DOWN) | (1 << _EV_KEY_UP)


class SpaceTap:
    """把有空格的拦截挂在系统事件流上。

    只做两件事：判断要不要吞、要不要触发；判断逻辑由调用方注入，
    自己完全不持有业务状态（这样"什么时候空格归我们"只有一个答案）。

    v1.2.1 起**顺便旁听 Esc**（`on_esc`）：只回调、**绝不吞键**，
    这样不用再起 pynput 的键盘监听（它会崩，见模块开头）。
    """

    def __init__(self, should_intercept_fn, on_space, on_esc=None) -> None:
        self._wants = should_intercept_fn
        self._on_space = on_space
        self._on_esc = on_esc
        self._thread: threading.Thread | None = None
        self._runloop = None
        self._tap = None
        self._down = False              # 这次按下已经处理过了吗（挡长按连发）
        self._lock = threading.Lock()
        self.ready = threading.Event()
        self.ok = False

    # ----------------------------------------------------------- 生命周期
    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="SpaceTap")
        self._thread.start()
        self.ready.wait(3.0)

    def stop(self) -> None:
        with self._lock:
            rl = self._runloop
        if rl is not None:
            try:
                from Quartz import CFRunLoopStop

                CFRunLoopStop(rl)
            except Exception:
                log.debug("停掉空格拦截失败（可忽略）")

    # ----------------------------------------------------------- tap 线程
    def _serve(self) -> None:
        from Quartz import (
            CFMachPortCreateRunLoopSource,
            CFRunLoopAddSource,
            CFRunLoopGetCurrent,
            CFRunLoopRun,
            CGEventTapCreate,
            CGEventTapEnable,
            kCFRunLoopCommonModes,
            kCGEventTapOptionDefault,
            kCGHeadInsertEventTap,
            kCGSessionEventTap,
        )

        with self._lock:
            self._runloop = CFRunLoopGetCurrent()

        mask = tap_mask()
        try:
            tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                                  kCGEventTapOptionDefault, mask,
                                  self._callback, None)
        except Exception:
            log.exception("创建空格事件识别失败")
            self.ready.set()
            return
        if tap is None:
            log.warning("空格三态交互不可用：CGEventTapCreate 返回 None"
                        "（多半是没给辅助功能权限）—— 退回不吞键的老路径")
            self.ready.set()
            return

        with self._lock:
            self._tap = tap
        src = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        self.ok = True
        self.ready.set()
        log.info("空格拦截已就绪（有条件吞键：浮窗显示时 / 前台是播放器时）")
        CFRunLoopRun()          # 阻塞直到 stop()

    # ----------------------------------------------------------- 回调
    def _callback(self, proxy, etype, event, refcon):        # noqa: ANN001
        from Quartz import CGEventGetFlags, CGEventGetIntegerValueField

        try:
            if etype in (_EV_TAP_DISABLED_TIMEOUT, _EV_TAP_DISABLED_USERINPUT):
                # 系统把 tap 关了（回调太慢 / 用户输入事件风暴）。
                # 必须重新打开，否则拦截会**静默失效** —— 用户只会觉得
                # "空格突然不好使了"，根本查不出来。
                log.warning("空格拦截被系统暂停（type=%s），重新启用", etype)
                with self._lock:
                    tap = self._tap
                if tap is not None:
                    try:
                        from Quartz import CGEventTapEnable

                        CGEventTapEnable(tap, True)
                    except Exception:
                        log.exception("重新启用空格拦截失败")
                return event

            keycode = int(CGEventGetIntegerValueField(event, _KEYCODE_FIELD))
            flags = int(CGEventGetFlags(event))

            # ---- Esc：只旁听，绝不吞 ----
            # 为什么放在空格之前、而且**永远返回 event**：
            #   Esc 在别处（对话框、播放器、IDE）都有自己的用途，我们只是
            #   "顺带知道用户按了 Esc"，绝不能像空格那样有条件地吃掉它。
            if esc_should_fire(etype, keycode, self._on_esc is not None):
                threading.Thread(target=self._fire_esc, daemon=True,
                                 name="SpaceTapEsc").start()
            if keycode == ESC_KEYCODE:
                return event

            if keycode != SPACE_KEYCODE:
                return event

            try:
                wants = bool(self._wants())
            except Exception:
                log.exception("判断空格是否该拦截时出错（本次放行）")
                return event

            d = decide(etype, keycode, flags, wants, self._down)
            self._down = d.down
            if not d.swallow:
                return event
            if d.fire:
                # ★ 绝不在 tap 线程里做事：这里一慢，系统就把 tap 关了
                #   （kCGEventTapDisabledByTimeout），表现是"空格突然没反应"。
                threading.Thread(target=self._fire, daemon=True,
                                 name="SpaceTapFire").start()
            return None
        except Exception:
            log.exception("空格回调出错（放行这一下）")
            return event


    def _fire(self) -> None:
        try:
            self._on_space()
        except Exception:
            log.exception("空格动作异常")

    def _fire_esc(self) -> None:
        try:
            self._on_esc()
        except Exception:
            log.exception("Esc 回调异常")
