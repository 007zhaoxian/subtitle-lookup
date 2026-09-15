"""全局鼠标监听：浮窗显示时，**点击浮窗外面**就把它关掉。

用户要的手感（2026-09-15）：「点一下浮窗外面，浮窗就自己消失」。
这比"必须按空格关"更顺手 —— 尤其当你想慢慢看结果、又不想让视频继续播的时候：
按空格关窗会把那一下空格送给播放器（= 恢复播放），点外面关则**完全不碰播放状态**。

【坐标系已经实测确认，别再改】
pynput 报告的鼠标坐标、Qt 的 widget geometry、Quartz 的 kCGWindowBounds
三者完全一致：原点都在主屏**左上角**，y 轴向下。所以这里直接比较即可，
不需要任何翻转（实测脚本见 tools/probe_input_focus.py 的同款思路）。

【为什么只在"按下"时判断】
拖动浮窗时，鼠标松手的位置常常已经在浮窗外面了。如果监听 release，
一拖就误关。而"按下"这个瞬间必然发生在浮窗内部（否则根本拖不起来），
所以只判断 press 就天然避开了拖动的误伤。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from utils import log


class ClickOutsideWatcher:
    """浮窗显示期间，监听全局鼠标点击；点在浮窗矩形外就回调。"""

    def __init__(
        self,
        is_active: Callable[[], bool],
        get_rect: Callable[[], tuple | None],
        on_outside: Callable[[], None],
        grace_sec: float = 0.35,
    ) -> None:
        self._is_active = is_active
        self._get_rect = get_rect
        self._on_outside = on_outside
        self._grace = grace_sec
        self._armed_at = 0.0
        self._was_active = False
        self._listener = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        try:
            from pynput import mouse
        except Exception as exc:
            log.warning("鼠标监听不可用（需要『辅助功能』权限）: %s", exc)
            return False
        try:
            self._listener = mouse.Listener(on_click=self._on_click)
            self._listener.daemon = True
            self._listener.start()
            log.info("鼠标监听已启动（点浮窗外面即关闭）")
            return True
        except Exception as exc:
            log.warning("鼠标监听启动失败: %s", exc)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # ------------------------------------------------------------ 状态
    def arm(self) -> None:
        """浮窗刚显示时调用：开始计时，忽略紧接着的这一瞬间。"""
        with self._lock:
            self._armed_at = time.time()

    def disarm(self) -> None:
        with self._lock:
            self._armed_at = 0.0

    # ------------------------------------------------------------ 回调
    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return
        try:
            active = bool(self._is_active())
            with self._lock:
                if not active:
                    # 浮窗不在：把状态复位，下一次浮窗出现时重新计宽容期
                    self._was_active = False
                    self._armed_at = 0.0
                    return
                if not self._was_active:
                    # 浮窗**刚刚**出现 —— 这一下点击可能是触发查词那次操作的余波，
                    # 先记个时间，宽容期内一律不关（见 _grace）。
                    self._was_active = True
                    self._armed_at = time.time()
                    return
                armed = self._armed_at
            if armed <= 0 or time.time() - armed < self._grace:
                return
            rect = self._get_rect()
            if not rect or len(rect) != 4:
                return
            gx, gy, gw, gh = rect
            if gw <= 0 or gh <= 0:
                return
            pad = 6      # 边缘留几个像素的余量，免得贴边点击被判成"外面"
            if (gx - pad) <= x <= (gx + gw + pad) and (gy - pad) <= y <= (gy + gh + pad):
                return                      # 落在浮窗里 —— 正常交互
            log.info("检测到浮窗外的点击（%d,%d 不在 %s）→ 关闭浮窗",
                     int(x), int(y), rect)
            self.disarm()
            self._on_outside()
        except Exception:
            log.exception("外部点击判定异常")
