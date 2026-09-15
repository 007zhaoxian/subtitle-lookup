"""全局按键监听（pynput）。

* 只有“看剧模式”开启时才响应按键；关闭时监听器直接忽略一切按键，
  程序对系统输入零干预。
* 触发键默认是 **N**（2026-09-14 定稿，路径：空格 → M → N）：空格在绝大多数播放器
  里是暂停/继续，每查一次词视频就被暂停一次；M 在很多播放器里是静音，按下去
  声音直接没了。N 在主流播放器里没有绑定，最安全。
* **关闭键默认是空格**：只在「浮窗正显示着」这一刻生效 —— 按空格 = 关掉浮窗。
  浮窗不在时这个键完全不干预，空格该怎么暂停/播放就怎么暂停/播放。
  判断靠外部传进来的 `is_close_active()`（app.is_overlay_showing），
  本模块自己不持有状态 —— 这样"什么时候空格才有特殊含义"只有一个答案。
* pynput 的 Listener 默认**不吞键**（不同于 keyboard 库的 suppress=True），
  所以按键会原样送达前台播放器。用户要的手感正是：按空格关掉浮窗之后，
  再按一次空格就能接着播放。
* 防误触由 App 的状态机负责（busy / showing / asking 期间不重复触发），
  另外这里还挡掉「带 ⌘/⌃/⌥ 的组合键」—— 免得 Cmd+M 最小化窗口也触发查词。

★ v1.2.1：**默认配置下这个监听器根本不会被创建。**
  空格由 keytap 管、关闭键默认也是空格、Esc 从 v1.2.1 起同样由 keytap 旁听
  —— 三样都不需要 pynput，于是 `start()` 直接返回、一个 tap 都不建。
  原因不只是"少一个监听"，而是 **pynput 的键盘 tap 会让整个 App 崩溃**：
  它订阅了系统定义事件（type 14），并在 tap 线程上把它们转成 NSEvent，
  在 macOS 26 上会撞 HIToolbox 的 `dispatch_assert_queue` 断言 → SIGTRAP。
  完整分析与崩溃栈见 `_avoid_pynput_media_key_crash`。
  只有"触发键被改成非空格"或"keytap 不可用"时这个监听才会起来，
  那时也会先摘掉那一位订阅（同一个函数）。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import config
from utils import log

_KEY_ALIASES = {
    "space": "space",
    "空格": "space",
    "空格键": "space",
    "enter": "enter",
    "return": "enter",
    "回车": "enter",
    "tab": "tab",
    "esc": "esc",
    "escape": "esc",
}

# 按住这些修饰键时，触发键不算数（Cmd+M / Ctrl+M / Option+M 都有自己的用途）
_MODIFIER_ATTRS = ("cmd", "ctrl", "alt", "cmd_l", "cmd_r", "ctrl_l", "ctrl_r",
                   "alt_l", "alt_r", "alt_gr")


def _avoid_pynput_media_key_crash() -> None:
    """★★ 崩溃修复（2026-09-15 真机抓到的 SIGTRAP，见下）：别让 pynput 的键盘 tap
    订阅**系统定义事件（type 14 = 媒体键/亮度键那一类）**。

    【崩溃长什么样】macOS 崩溃报告
    `~/Library/Logs/DiagnosticReports/SubtitleLookup-2026-09-15-194150.ips`：

        EXC_BREAKPOINT / SIGTRAP
        m_CGEventTapCallBack
          → <Python 帧> → objcsel_vectorcall
          → +[NSEvent eventWithCGEvent:]
          → HIToolbox CreateEventWithCGEvent
          → TSMSetCapsLockKeyTransitionDetected → TSMAdjustCapsLockPressAndHold
          → TSMGetInputSourceProperty → islGetInputSourceListWithAdditions
          → dispatch_assert_queue 失败 → dispatch_assert_queue_fail → trap

    一句话：**在事件 tap 线程（非主线程）上把 CGEvent 转成 NSEvent，
    撞上了 HIToolbox 文本输入系统"必须在主队列"的断言，进程直接被打死。**
    用户侧看到的就是"用着用着 App 自己没了"，日志里连一行都没有。

    【为什么是 pynput 惹的祸】整个 Python 环境里，
    `NSEvent.eventWithCGEvent_` 只有**一个**调用点：
    `pynput/keyboard/_darwin.py:303`，而且只在
    `event_type == NSSystemDefined`（type 14）时才会走到。
    pynput 的键盘 tap 掩码里恰好带了这一位（`_EVENTS` 含
    `CGEventMaskBit(NSSystemDefined)`）—— 于是只要系统流里出现一个
    type 14 事件（按一下键盘上的 ⏯ / ⏭ / 音量 / 亮度键，
    或者**我们自己合成的媒体键**），它就在 tap 线程上转 NSEvent，然后崩。

    本项目根本不需要媒体键事件（只关心 Esc / 空格 / 关闭键），
    所以把这一位从掩码里摘掉即可 —— 不是"少听一个事件"，
    而是**把那行会崩的代码变成永远走不到的代码**。

    ⚠️ 必须在**创建任何 keyboard.Listener 之前**调用（掩码是在建 tap 时读的）。
    默认配置下我们连 Listener 都不建（见 HotkeyWatcher.start），
    这里是给"用户把触发键改成非空格"或"keytap 不可用"的兜底路径上一道保险。
    """
    try:
        from Quartz import CGEventMaskBit, NSSystemDefined

        from pynput.keyboard import _darwin as pk

        cls = getattr(pk, "Listener", None)
        old = getattr(cls, "_EVENTS", None)
        if old is None:
            log.debug("pynput 的键盘掩码取不到，跳过防崩补丁")
            return
        bit = CGEventMaskBit(NSSystemDefined)
        if not (old & bit):
            log.debug("pynput 键盘掩码里本来就没有 NSSystemDefined")
            return
        cls._EVENTS = old & ~bit
        log.info("已摘掉 pynput 键盘监听对 NSSystemDefined 的订阅"
                 "（%#x → %#x）：这是 macOS 26 上那个 SIGTRAP 崩溃的唯一触发路径",
                 old, cls._EVENTS)
    except Exception:
        log.exception("给 pynput 打防崩补丁失败（不影响其它功能）")


def _resolve_key(name: str | None = None):
    """把键名字符串解析成 pynput 的 Key 对象（默认取 config.TRIGGER_KEY）。"""
    from pynput import keyboard

    raw = (config.TRIGGER_KEY if name is None else name) or ""
    name = _KEY_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    # 支持单字符，例如 "m" / "f"
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"不支持的按键: {raw}")


def _key_id(key) -> int | None:
    """取按键的虚拟键码 —— 用它比对最可靠。"""
    vk = getattr(key, "vk", None)
    return vk if isinstance(vk, int) else None


def _key_matches(key, target, target_vk) -> bool:
    """判断按下的键是不是目标键。

    为什么要三重比对：单字符键在不同输入状态下 char 会变（Shift+N 是 'N'，
    Caps Lock 开着也是 'N'），只比 char 会漏触发；所以优先比虚拟键码。
    """
    if target is None:
        return False
    if key == target:
        return True
    vk = _key_id(key)
    if vk is not None and target_vk is not None and vk == target_vk:
        return True
    c1 = getattr(key, "char", None)
    c2 = getattr(target, "char", None)
    if isinstance(c1, str) and isinstance(c2, str) and c1 and c2:
        return c1.lower() == c2.lower()
    return False


class HotkeyWatcher:
    def __init__(
        self,
        is_enabled: Callable[[], bool],
        on_trigger: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
        is_close_active: Callable[[], bool] | None = None,
        on_close: Callable[[], None] | None = None,
        watch_trigger: bool = True,
        esc_handled_elsewhere: bool = False,
    ) -> None:
        self._is_enabled = is_enabled
        self._on_trigger = on_trigger
        self._on_cancel = on_cancel
        # 关闭键（默认空格）：只有 is_close_active() 为真（浮窗正显示着）时才响应
        self._is_close_active = is_close_active
        self._on_close = on_close
        # 触发键要不要由 pynput 管。
        # 默认配置下触发键就是【空格】，而 keytap.SpaceTap 装了之后会**吃掉**
        # 空格并自己回调 —— 那时这里必须关掉，否则一次按键走两条路径，
        # 状态机会被连着推两次。
        self._watch_trigger = bool(watch_trigger)
        # Esc 是不是已经由 keytap 那条 tap 旁听了（v1.2.1 起的默认路径）。
        # 是的话这里就不需要为 Esc 而维持一个 pynput 键盘监听 ——
        # 而 pynput 的键盘监听正是那个 SIGTRAP 崩溃的载体，能不起就不起。
        self._esc_elsewhere = bool(esc_handled_elsewhere)
        self._key = None
        self._key_vk: int | None = None
        self._close = None
        self._close_vk: int | None = None
        self._listener = None
        self._pressed = False
        self._last = 0.0
        self._last_fired = 0.0
        self._mods: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        from pynput import keyboard

        if not self._watch_trigger:
            self._key = self._key_vk = None
            log.info("触发键交由空格拦截层（keytap）处理，pynput 不重复监听")
        else:
            self._key = _resolve_key()
            self._key_vk = _key_id(self._key)
        # 关闭键（默认空格）：浮窗显示时按它 = 关掉浮窗。
        # 和触发键撞车时不装 —— 一个键两种含义必然乱套。
        if (config.CLOSE_KEY_ENABLED and config.CLOSE_KEY
                and config.CLOSE_KEY != config.TRIGGER_KEY):
            try:
                self._close = _resolve_key(config.CLOSE_KEY)
                self._close_vk = _key_id(self._close)
                log.info("关闭键 = %s（仅浮窗显示时生效，且不吞键）",
                         config.CLOSE_KEY_LABEL)
            except Exception as exc:
                log.warning("关闭键 %s 解析失败，已忽略: %s", config.CLOSE_KEY, exc)
                self._close = self._close_vk = None
        else:
            self._close = self._close_vk = None

        # ★ 还有没有活儿需要 pynput 键盘监听干？
        #   触发键（默认由 keytap 管）、关闭键（默认空格，也和触发键撞车）、
        #   Esc（v1.2.1 起由 keytap 管）—— 三样都不需要时**一个 tap 都不建**。
        #   这不是优化，是修 bug：pynput 的键盘 tap 会在收到系统定义事件时
        #   崩溃（见 _avoid_pynput_media_key_crash），能不起就不起。
        need = bool(self._watch_trigger or self._close is not None
                    or (self._on_cancel is not None and not self._esc_elsewhere))
        if not need:
            log.info("无需 pynput 键盘监听：触发键/关闭键/Esc 全部由空格拦截层"
                     "（keytap，CGEventTap）接管 —— 少一个 tap，也少一类崩溃")
            return

        _avoid_pynput_media_key_crash()
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        if self._watch_trigger:
            log.info("按键监听已启动，触发键 = %s (%s)",
                     config.TRIGGER_LABEL, config.TRIGGER_KEY)
        else:
            log.info("按键监听已启动（仅 Esc / 关闭键）")

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    # ------------------------------------------------------------ 比对
    def _matches(self, key) -> bool:
        """判断按下的键是不是**触发键**。"""
        return _key_matches(key, self._key, self._key_vk)


    @staticmethod
    def _modifier_name(key) -> str | None:
        try:
            from pynput import keyboard
        except Exception:
            return None
        for name in _MODIFIER_ATTRS:
            target = getattr(keyboard.Key, name, None)
            if target is not None and key == target:
                return name
        return None

    # ------------------------------------------------------------ 回调
    def _on_press(self, key) -> None:
        mod = self._modifier_name(key)
        if mod:
            with self._lock:
                self._mods.add(mod)
            return
        # 【关闭键】默认空格：只有浮窗正显示时才有意义。
        # 浮窗不在时这里**什么都不做** —— 空格原样归播放器（暂停/播放）。
        if self._close is not None and _key_matches(key, self._close, self._close_vk):
            with self._lock:
                mods = set(self._mods)
            if mods:
                return
            self._dispatch_close()
            return
        if self._matches(key):
            with self._lock:
                self._pressed = True
                self._last = time.time()
                mods = set(self._mods)
            if mods:
                log.debug("触发键带着修饰键按下（%s），忽略", ",".join(sorted(mods)))
                return
            self._dispatch()
            return
        if self._on_cancel is not None and key is not None:
            # Esc 若已由 keytap 旁听，这里就不能再回调 —— 否则一次 Esc
            # 关两遍（浮窗直接消失得莫名其妙）。见 esc_handled_elsewhere。
            if self._esc_elsewhere:
                return
            try:
                from pynput.keyboard import Key

                if key == Key.esc:
                    self._on_cancel()
            except Exception:
                pass

    def _on_release(self, key) -> None:
        mod = self._modifier_name(key)
        if mod:
            with self._lock:
                self._mods.discard(mod)
            return
        if self._matches(key):
            with self._lock:
                self._pressed = False

    def _dispatch(self) -> None:
        if not self._is_enabled():
            return  # 看剧模式关闭 → 完全放行，不做任何事
        # 去抖：极短时间内的重复按下（长按连发）只算一次
        if time.time() - self._last_fired < config.DEBOUNCE_SEC:
            return
        self._last_fired = time.time()
        try:
            self._on_trigger()
        except Exception:
            log.exception("触发回调异常")

    def _dispatch_close(self) -> None:
        """按了关闭键（空格）。

        两道判断，缺一不可：
        · 看剧模式必须开着（关了 App 就完全不干预键盘）；
        · **浮窗必须正显示着** —— 否则直接返回，把空格留给播放器。

        这里**不做去抖**：长按空格时系统会连发 keyPress，但第一次按下就已经
        把浮窗关掉、状态变成 idle 了，后续连发在 is_close_active() 这一步
        自然全部返回 False，不必再靠时间去抖。
        """
        if not self._is_enabled():
            return
        if self._is_close_active is None or not self._is_close_active():
            return
        if self._on_close is None:
            return
        try:
            self._on_close()
        except Exception:
            log.exception("关闭回调异常")
