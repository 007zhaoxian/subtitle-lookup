"""验证「浮窗显示着的时候按触发键，浮窗会不会关掉」（用户反馈 ③）。

【用户报的现象】浮窗角上写着「再按一次【N】关闭」，可按了就是不关。

【根因】出错时的浮窗以前用 `force_idle_after=True` 把状态机**直接退回 IDLE**：
浮窗明明还在屏幕上，状态机却认为自己空闲了。于是用户按 N 时走的是
「idle → 开始新查词」这条分支，旧浮窗被新浮窗顶掉 —— 看起来就是「按了不关」。
现在所有浮窗（含出错的）统一走 SHOWING：显示着的时候按一次 = 关掉，
关掉之后再按一次才是重试。

本探针不碰豆包、不建真窗口（用假的 runtime 记录 post_show/post_hide），
只验证状态机，秒级完成。

用法: python tools/probe_close_on_key.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_mod  # noqa: E402
import config  # noqa: E402
import screenshot  # noqa: E402
from app import BUSY, IDLE, SHOWING, App  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


class FakeRuntime:
    """只记录调用，不建真窗口 —— 状态机测试不需要真窗口。"""

    def __init__(self) -> None:
        self.shown: list[tuple] = []
        self.hidden = 0
        self.progress: list[str] = []
        self.panels = 0
        # 新增：浮窗追问 / 打字状态（这些也要能被状态机正确读写）
        self.answers: list[tuple] = []
        self.statuses: list[str] = []
        self.typing = False
        self.has_over = False

    def post_show(self, title, body):
        self.shown.append((title, body))
        self.has_over = True

    def post_hide(self):
        self.hidden += 1
        self.has_over = False

    def post_progress(self, text):
        self.progress.append(text)

    def post_panel(self):
        self.panels += 1

    def post_alert(self, *a, **k):
        pass

    def mark_auto_ui(self):
        pass

    def auto_ui_recently(self):
        return False

    def typing_active(self):
        return self.typing

    def has_overlay(self):
        return self.has_over

    def post_overlay_answer(self, text, error=False):
        self.answers.append((text, error))

    def post_overlay_status(self, text):
        self.statuses.append(text)

    def post_esc(self):
        self.post_hide()


def main() -> int:
    # 别真的弹系统通知 / 删截图文件
    app_mod.notify = lambda *a, **k: None
    screenshot.cleanup = lambda _p: None

    app = App()
    rt = FakeRuntime()
    app.runtime = rt
    app._mode_on = True

    print(f"触发键 = {config.TRIGGER_LABEL}")

    # ---- 1) 走真实的 _finish_err 路径（用户看到的那类浮窗）----
    app._state = BUSY
    app._finish_err(RuntimeError("（探针模拟的一次查词失败）"), None)

    check("失败后浮窗被显示出来", len(rt.shown) == 1, f"shows={len(rt.shown)}")
    check("失败浮窗的状态是 SHOWING（而不是退回 IDLE）",
          app._state == SHOWING, f"state={app._state}")

    # ---- 2) 按一次触发键 → 浮窗应关掉 ----
    app.on_trigger()
    check("按第一次触发键 → 浮窗关闭", rt.hidden == 1, f"hidden={rt.hidden}")
    check("关掉之后状态回 IDLE", app._state == IDLE, f"state={app._state}")
    check("关窗时没有又弹一个新浮窗", len(rt.shown) == 1, f"shows={len(rt.shown)}")

    # ---- 3) 再按一次 → 这次才是「开始新查词」----
    # 把真正去截图的那步换掉，免得探针去截屏 + 连豆包
    fired = {"n": 0}
    app._capture_and_ask = lambda: fired.__setitem__("n", fired["n"] + 1)
    app.on_trigger()
    check("再按一次 → 开始新查词（而不是继续关窗）",
          fired["n"] == 1 and rt.hidden == 1,
          f"capture={fired['n']} hidden={rt.hidden}")

    # ---- 4) 结果浮窗（正常答完）也要能被一次按键关掉 ----
    rt2_shows = len(rt.shown)
    app._state = BUSY
    app._finish_ok("sugarcoat —— 动词 / 粉饰，委婉美化。", None)
    app.on_trigger()
    check("正常结果浮窗同样一次按键就关掉",
          app._state == IDLE and rt.hidden == 2 and len(rt.shown) == rt2_shows + 1,
          f"hidden={rt.hidden} state={app._state}")

    # ---- 5) AST 检查：_finish_err 里不再有任何调用传 force_idle_after ----
    # 注意要用 AST 而不是搜字符串 —— 函数里那段「为什么不再用
    # force_idle_after」的注释本身就会命中字符串搜索，造成误判。
    import ast

    src = (ROOT / "app.py").read_text(encoding="utf-8")
    offenders: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "_finish_err":
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and any(
                        kw.arg == "force_idle_after" for kw in call.keywords):
                    offenders.append(ast.unparse(call)[:90])
    check("_finish_err 里没有任何调用传 force_idle_after",
          not offenders, f"offenders={offenders}")

    # ---- 6) 【2026-09-14 新增】空格也能关窗，且浮窗不在时完全不干预 ----
    app._state = BUSY
    app._finish_ok("face the music —— 习语 / 承担后果。", None)
    before = rt.hidden
    app.on_space()
    check(f"浮窗显示中按【{config.CLOSE_KEY_LABEL}】→ 关掉浮窗",
          app._state == IDLE and rt.hidden == before + 1,
          f"state={app._state} hidden={rt.hidden}")
    app.on_space()
    check("浮窗不在时按空格 → 什么都不做（空格归播放器播/停）",
          rt.hidden == before + 1, f"hidden={rt.hidden}")

    # 正在输入框打字时，空格是"打空格"，不许关窗
    app._state = BUSY
    app._finish_ok("blow it —— 搞砸。", None)
    rt.typing = True
    app.on_space()
    check("正在浮窗打字时按空格 → 不关窗", app._state == SHOWING and rt.hidden == before + 1)
    rt.typing = False

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
