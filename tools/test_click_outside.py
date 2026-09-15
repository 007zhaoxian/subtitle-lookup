"""「点浮窗外面就关掉它」的回归测试。

用户诉求原文（2026-09-15）：「当有鼠标点击悬浮窗外面就自动退出悬浮窗」。

要钉死的边界：
  · 点在浮窗**里面** → 不关（那是正常交互，比如点输入框、拖选文字）；
  · 点在浮窗**外面** → 关；
  · 浮窗根本没显示   → 压根不监听（用户在干别的不该被打扰）；
  · **松手**的那一下不算（拖动浮窗时，松手常常已经在窗外了，会误关）；
  · 浮窗刚弹出来的那一瞬间不响应宽容期（避免"上一个动作"的余波误关）；
  · 输入框里还有没发出去的话 → 只退出输入态，**不许把用户打的字弄丢**。

用法: python tools/test_click_outside.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_click_")

import config  # noqa: E402
from mouse import ClickOutsideWatcher  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


RECT = (100, 100, 400, 300)      # 浮窗占的屏幕矩形
OUT = (900, 700)                 # 明显在浮窗外的点
INSIDE = (200, 200)              # 明显在浮窗内的点


class Rig:
    """把 watcher 的三个回调做成可控的开关。"""

    def __init__(self, grace: float = 0.0) -> None:
        self.active = False
        self.rect = RECT
        self.fired: list[str] = []
        self.w = ClickOutsideWatcher(
            is_active=lambda: self.active,
            get_rect=lambda: self.rect,
            on_outside=lambda: self.fired.append("outside"),
            grace_sec=grace,
        )

    def click(self, x: int, y: int, pressed: bool = True) -> None:
        self.w._on_click(x, y, None, pressed)

    def show(self) -> None:
        """浮窗出现（第一次点击只做"武装"，不判定）。"""
        self.active = True
        self.click(*OUT)


def test_watcher() -> None:
    r = Rig()

    # ① 浮窗没显示 → 完全不管（用户在干别的）
    r.click(*OUT)
    check("浮窗没显示 → 点击完全不响应", not r.fired)

    # ② 浮窗出现后的第一次点击：只武装，不判定
    r.show()
    check("浮窗刚出现的那一下不判定（宽容期）", not r.fired)

    # ③ 点在浮窗里面 → 不关
    r.click(*INSIDE)
    check("点在浮窗里面 → 不关", not r.fired)

    # ④ 点在浮窗外面 → 关
    r.click(*OUT)
    check("点在浮窗外面 → 关闭", r.fired == ["outside"])

    # ⑤ 松手事件不算（拖动浮窗时松手常在窗外）
    r2 = Rig()
    r2.show()
    r2.click(*OUT, pressed=False)
    check("鼠标松手不触发关闭（避免拖动时误关）", not r2.fired)

    # ⑥ 贴着边缘几个像素也算"里面"（别因为手抖就关掉）
    r3 = Rig()
    r3.show()
    l, t, w, h = RECT
    r3.click(l - 3, t - 3)
    check("贴边 3px 仍算浮窗内（有余量）", not r3.fired)
    r3.click(l + w + 200, t)
    check("明显在外 → 关闭", r3.fired == ["outside"])

    # ⑦ 拿不到矩形（浮窗已销毁）→ 不误关
    r4 = Rig()
    r4.show()
    r4.rect = None
    r4.click(*OUT)
    check("拿不到浮窗矩形 → 不误关", not r4.fired)

    # ⑧ 关掉之后不会连发（disarm）
    r5 = Rig()
    r5.show()
    r5.click(*OUT)
    r5.click(*OUT)
    r5.click(*OUT)
    check("只关闭一次（不会连环触发）", len(r5.fired) == 1, str(r5.fired))

    # ⑨ 宽容期真的存在
    r6 = Rig(grace=10.0)          # 10 秒宽容期，测试里肯定还没过
    r6.show()
    r6.click(*OUT)
    check("宽容期内不关闭", not r6.fired)


def test_app_layer() -> None:
    """App 侧：有未发送的追问时，点外面**不许**把字弄丢。"""
    import app as app_mod

    app_mod.notify = lambda *a, **k: None
    from app import IDLE, SHOWING, App

    class FakeRuntime:
        def __init__(self) -> None:
            self.hidden = 0
            self.typing = False
            self.draft = ""
            self.exit_calls = 0

        def typing_active(self) -> bool:
            return self.typing

        def draft_text(self) -> str:
            return self.draft

        def post_hide(self) -> None:
            self.hidden += 1

        def exit_input(self) -> None:
            self.exit_calls += 1
            self.typing = False

    def fresh(state=SHOWING):
        a = App()
        a.runtime = FakeRuntime()
        a._mode_on = True
        a._state = state
        return a

    # 浮窗显示 + 没有草稿 → 关窗
    a = fresh()
    a.on_click_outside()
    check("App：浮窗显示时点外面 → 关窗（且不碰播放器）",
          a._state == IDLE and a.runtime.hidden == 1)

    # 浮窗显示 + 正在打字且有内容 → 只退出输入态，保住没发出去的字
    a = fresh()
    a.runtime.typing = True
    a.runtime.draft = "这句里的 face the music 是什么语气？"
    a.on_click_outside()
    check("App：有未发送的追问时，点外面只退出输入态（字不丢）",
          a._state == SHOWING and a.runtime.hidden == 0
          and a.runtime.exit_calls == 1)

    # 正在打字但输入框是空的 → 直接关（没什么可丢的）
    a = fresh()
    a.runtime.typing = True
    a.runtime.draft = "   "
    a.on_click_outside()
    check("App：输入框空着时，点外面照常关窗",
          a._state == IDLE and a.runtime.hidden == 1)

    # 浮窗不在 → 什么都不做
    a = fresh(state=IDLE)
    a.on_click_outside()
    check("App：浮窗不在时点外面 → 不动", a.runtime.hidden == 0)


def main() -> int:
    test_watcher()
    print()
    test_app_layer()

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
