"""空格「三态交互」的回归测试 —— 全离线，**绝不碰用户真的播放器**。

用户诉求原文（2026-09-15）：
    当我看视频时…① 摁一次空格：播放 ② 再摁 1 次空格：暂停，查询、显示浮框
    ③ 再摁 1 次空格：关闭浮框并继续播放
以及那条把这件事逼出来的 bug：
    "弹窗之后，顶部应用就是我写的这个软件了，再摁空格也不会回到播放器自动播放，
     必须得鼠标点击播放器后空格键才能生效。"

所以这里要钉死的是四层，缺一层用户就还是得碰鼠标：
  A) keytap.decide()     —— 这一下键吞不吞、动作一次还是多次（纯函数）
  B) keytap 的守卫        —— Cmd/Ctrl/Option+空格 绝不抢
  C) App.space_should_intercept() —— 什么时候才轮到我们管空格
  D) App._three_state()  —— 三种含义各自的动作，以及对播放器的读写

⚠️ 本测试会把 app.player 换成假的。**不能**让它跑真的 player ——
   `set_paused` 在只有媒体键可用时会真的合成系统媒体键，
   那会把用户正在听的音乐切掉。

用法: python tools/test_space_state.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_space_")
os.environ["DL_BACKEND"] = "web"          # 状态机与后端无关，固定住免得跑偏

import config                                       # noqa: E402
import keytap                                       # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def eq(name: str, got, want) -> None:
    check(name, got == want, f"实际 {got!r}，期望 {want!r}")


SPACE = keytap.SPACE_KEYCODE
KD = keytap._EV_KEY_DOWN
KU = keytap._EV_KEY_UP
CMD = keytap._FLAG_COMMAND
CTRL = keytap._FLAG_CONTROL
OPT = keytap._FLAG_ALTERNATE


# ======================================================================
# A/B) keytap 的决策函数
# ======================================================================
def test_decide() -> None:
    print("== A. keytap.decide：吞不吞 / 动作几次 ==")

    # 该管的时候：吞 + 动作 + 标记 down
    d = keytap.decide(KD, SPACE, 0, True, False)
    eq("空格按下（该管）→ 吞掉并触发动作", (d.swallow, d.fire, d.down), (True, True, True))

    # ★ 长按连发：系统会连着发 keyDown，只能动作一次
    d2 = keytap.decide(KD, SPACE, 0, True, d.down)
    eq("长按连发的第 2 个 keyDown → 仍然吞，但不再动作",
       (d2.swallow, d2.fire, d2.down), (True, False, True))

    # keyUp 必须跟着一起吞，否则会漏一个孤儿抬起事件给播放器
    du = keytap.decide(KU, SPACE, 0, True, d2.down)
    eq("keyUp → 一起吞掉、不动作、复位", (du.swallow, du.fire, du.down),
       (True, False, False))

    # 抬起之后再来一次，必须重新能动作
    d3 = keytap.decide(KD, SPACE, 0, True, du.down)
    eq("抬起后再按 → 又能动作一次", (d3.swallow, d3.fire, d3.down),
       (True, True, True))

    # ★ 不该管的时候：原样放行，并且必须把 down 复位
    dn = keytap.decide(KD, SPACE, 0, False, True)
    eq("不该管时 → 放行，并把连发标记复位（否则下次按下会被误吞掉）",
       (dn.swallow, dn.fire, dn.down), (False, False, False))

    # 其它键一律不碰
    dk = keytap.decide(KD, 0, 0, True, False)
    eq("非空格键 → 一律放行", (dk.swallow, dk.fire, dk.down), (False, False, False))

    print("\n== B. 修饰键守卫（绝不能抢系统快捷键）==")
    for nm, fl in (("Cmd+空格（Spotlight）", CMD),
                   ("Ctrl+空格（输入法切换）", CTRL),
                   ("Option+空格（特殊空格）", OPT),
                   ("Cmd+Ctrl+空格", CMD | CTRL)):
        d = keytap.decide(KD, SPACE, fl, True, False)
        check(f"{nm} 绝不抢", not d.swallow and not d.fire)
    check("clean 空格才归我们", keytap.decide(KD, SPACE, 0, True, False).swallow)


# ======================================================================
# C/D) App 层：用假 runtime / 假 player
# ======================================================================
class FakeRuntime:
    def __init__(self) -> None:
        self.hidden = 0
        self.typing = False
        self.shown = []
        self.overlay = False

    def mark_auto_ui(self) -> None:
        pass

    def typing_active(self) -> bool:
        return self.typing

    def has_overlay(self) -> bool:
        return self.overlay

    def post_show(self, title, body) -> None:
        self.shown.append((title, body))
        self.overlay = True

    def post_hide(self) -> None:
        self.hidden += 1
        self.overlay = False

    def post_overlay_answer(self, text, error=False) -> None:
        pass

    def post_overlay_status(self, text) -> None:
        pass

    def post_esc(self) -> None:
        self.post_hide()


class FakeController:
    """假的播放器控制器：只记流水，绝不真的动播放器。"""

    def __init__(self, playing=None, exact=True, backend="iina"):
        self.playing = playing          # None=读不到 / True / False
        self.exact = exact
        self.backend = backend          # "iina" / "mediakey"
        self.writes: list[bool] = []    # 每次 set_paused 的目标
        self.status_calls = 0

    def status(self) -> dict:
        self.status_calls += 1
        return {"backend": self.backend, "playing": self.playing,
                "exact": self.exact, "detail": "假播放器"}

    def set_paused(self, paused: bool) -> dict:
        self.writes.append(bool(paused))
        self.playing = not paused       # 假设成功
        return {"ok": True, "backend": self.backend, "exact": self.exact,
                "detail": "假播放器"}


class FakePlayerModule:
    """冒充 player 模块（app.py 里是 `import player` + `player.xxx`）。"""

    def __init__(self, controller: FakeController, frontmost: str = "") -> None:
        self._ctl = controller
        self.frontmost = frontmost

    def controller(self) -> FakeController:
        return self._ctl

    def frontmost_backend(self) -> str:
        return self.frontmost


def build(playing=None, frontmost="", exact=True, backend="iina"):
    """造一个 App + 全假依赖。

    ★ v1.2.0：浏览器不再是"已知播放器"，所以 frontmost 只会有
      ""（非播放器）、"iina"、"generic" 三种取值。
    """
    import app as app_mod

    app_mod.notify = lambda *a, **k: None
    app_mod.screenshot = type("_FakeShot", (), {
        "capture_fullscreen": staticmethod(lambda: Path("/tmp/_fake_shot.png")),
        "cleanup": staticmethod(lambda _p: None),
    })
    ctl = FakeController(playing=playing, exact=exact, backend=backend)
    fake_player = FakePlayerModule(ctl, frontmost=frontmost)

    a = app_mod.App()
    a.runtime = FakeRuntime()
    a._mode_on = True
    # 接管 app 模块引用到的 player
    app_mod.player = fake_player
    # 查词链路整体替换掉，只记录"有没有去查"
    asked = {"n": 0}

    def _fake_capture_and_ask():
        asked["n"] += 1

    a._capture_and_ask = _fake_capture_and_ask
    return a, ctl, asked


def test_intercept() -> None:
    print("\n== C. space_should_intercept：什么时候轮到我们管 ==")

    a, _ctl, _ = build(playing=True, frontmost="com.colliderli.iina")
    a._state = "idle"
    check("idle + 前台是 IINA → 接管", a.space_should_intercept())

    a, _ctl, _ = build(playing=True, frontmost="")
    a._state = "idle"
    check("idle + 前台不是已知播放器（在 Word / 浏览器 / Slack 里）→ **不接管**"
          "（否则打不出空格）",
          not a.space_should_intercept())

    a, _ctl, _ = build(playing=True, frontmost="")
    a._state = "showing"
    check("showing 状态 → 接管（这一下空格是「关窗并继续播」）",
          a.space_should_intercept())

    # ★ v1.2.3 行为变更：识别/追问进行中**不再吞键**。
    #   以前这两个状态也吞，而 _three_state() 进去又什么都不做 ——
    #   用户体感就是"空格彻底失灵"（既不查词、也不暂停），比不接管糟得多。
    #   放行之后，这几秒里空格至少还能原生控制播放器。
    for st in ("busy", "asking"):
        a, _ctl, _ = build(playing=True, frontmost="")
        a._state = st
        check(f"{st} 状态 → **不接管**（吞了却什么都不做 = 空格失灵）",
              not a.space_should_intercept())

    a, _ctl, _ = build(playing=True, frontmost="")
    a._state = "showing"
    a.runtime.typing = True
    check("浮窗里正在打字 → 不接管（空格就是空格）",
          not a.space_should_intercept())

    a, _ctl, _ = build(playing=True, frontmost="com.colliderli.iina")
    a._state = "idle"
    a._mode_on = False
    check("看剧模式关着 → 完全不接管", not a.space_should_intercept())

    # 总开关关掉时也不接管
    a, _ctl, _ = build(playing=True, frontmost="com.colliderli.iina")
    a._state = "idle"
    old = config.CLOSE_KEY_ENABLED
    config.CLOSE_KEY_ENABLED = False
    try:
        check("DL 总开关关掉 → 不接管", not a.space_should_intercept())
    finally:
        config.CLOSE_KEY_ENABLED = old


def test_three_state() -> None:
    print("\n== D. _three_state：三种含义的动作 ==")
    old_settle = config.PLAYER_PAUSE_SETTLE_SEC
    config.PLAYER_PAUSE_SETTLE_SEC = 0.0        # 测试里不用真等
    try:
        # ① 在播 → 暂停 + 查词
        a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
        a.on_space()
        eq("① 在播时按空格 → 先暂停画面", ctl.writes, [True])
        eq("① 并且真的去查词了", asked["n"], 1)
        eq("① 状态机进入 busy", a._state, "busy")

        # ③ 已停 → 继续播，不查词
        a, ctl, asked = build(playing=False, frontmost="com.colliderli.iina")
        a.on_space()
        eq("③ 已停时按空格 → 继续播放", ctl.writes, [False])
        eq("③ 不查词", asked["n"], 0)
        eq("③ 状态仍是 idle", a._state, "idle")

        # 读不到状态（只有媒体键）→ 按"在播"处理
        a, ctl, asked = build(playing=None, frontmost="")
        a.on_space()
        eq("状态读不到时按'在播'处理 → 暂停 + 查词",
           (ctl.writes, asked["n"]), ([True], 1))

        # ② 浮窗显示 → 关窗 + 继续播
        a, ctl, asked = build(playing=False, frontmost="")
        a._state = "showing"
        a.runtime.overlay = True
        a.on_space()
        eq("② 浮窗显示时按空格 → 收掉浮窗", a.runtime.hidden, 1)
        eq("② 状态回到 idle", a._state, "idle")
        eq("② 并让视频继续播", ctl.writes, [False])
        eq("② 不去查词", asked["n"], 0)

        # 识别中 / 追问中 → 不打断、也不碰播放器
        for st in ("busy", "asking"):
            a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
            a._state = st
            a.on_space()
            eq(f"{st} 时按空格 → 不查词", asked["n"], 0)
            eq(f"{st} 时按空格 → 不碰播放器（会把暂停的视频点开）", ctl.writes, [])
            eq(f"{st} 时状态不变", a._state, st)

        # 正在浮窗里打字 → 空格是字符
        a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
        a.runtime.typing = True
        a.on_space()
        eq("打字时按空格 → 什么都不做",
           (asked["n"], ctl.writes, a._state), (0, [], "idle"))

        # 看剧模式关着 → 什么都不做
        a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
        a._mode_on = False
        a.on_space()
        eq("看剧模式关着 → 什么都不做",
           (asked["n"], ctl.writes), (0, []))

        # 先暂停再截图：顺序不能反（否则截到的是下一帧）
        a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
        order = []
        a._capture_and_ask = lambda: order.append("capture")
        real_set = ctl.set_paused

        def spy_set(paused):
            order.append("pause")
            return real_set(paused)

        ctl.set_paused = spy_set
        a.on_space()
        eq("必须先暂停画面、再截图（反了就截到下一帧）", order, ["pause", "capture"])
    finally:
        config.PLAYER_PAUSE_SETTLE_SEC = old_settle


def test_click_outside_keeps_playback() -> None:
    print("\n== E. 「点浮窗外面」不碰播放状态 ==")
    a, ctl, asked = build(playing=False, frontmost="")
    a._state = "showing"
    a.runtime.overlay = True
    a.on_click_outside()
    eq("点外面 → 浮窗收掉", a.runtime.hidden, 1)
    eq("点外面 → 状态回 idle", a._state, "idle")
    eq("★ 点外面 → **完全不动播放器**（用户想慢慢看结果）", ctl.writes, [])

    # 有草稿时只退出输入态、不关窗
    a, ctl, asked = build(playing=False, frontmost="")
    a._state = "showing"
    a.runtime.overlay = True
    a.runtime.typing = True
    a.runtime.draft_text = lambda: "打了一半的问题"
    a.runtime.exit_input = lambda: None
    a.on_click_outside()
    eq("有未发送的草稿 → 不关窗", (a.runtime.hidden, a._state), (0, "showing"))
    eq("有草稿时也不碰播放器", ctl.writes, [])


def test_player_control_off() -> None:
    print("\n== F. 播放器控制总开关关掉时 ==")
    a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
    old = config.PLAYER_CONTROL
    config.PLAYER_CONTROL = False
    try:
        a._state = "showing"
        a.runtime.overlay = True
        a.on_space()
        eq("关掉后，关窗不再去动播放器", ctl.writes, [])
        eq("但浮窗照常收掉", a.runtime.hidden, 1)
    finally:
        config.PLAYER_CONTROL = old


def test_paused_memory() -> None:
    """只有媒体键可用（读不到状态）时，靠"上次是不是我们把它停住的"来分 ①/③。

    这条记忆解决的是一个非常真实的场景：
      看着剧 → 按空格（暂停 + 查词，视频停住了）→ **点浮窗外面**关掉它
      （这条路不碰播放器，所以视频还停着）→ 用户再按空格，本意是"接着看"。
    没有记忆的话，媒体键通道会把它当成"在播"，于是又暂停+查一次 ——
    用户看到的就是"空格按下去浮窗又弹出来了"，非常莫名。
    """
    print("\n== G. 读不到状态时的「上次是我们停的」记忆 ==")

    a, ctl, asked = build(playing=None, frontmost="")
    a.on_space()
    eq("① 读不到状态 → 按'在播'处理：暂停 + 查词",
       (ctl.writes, asked["n"]), ([True], 1))

    a._state = "showing"
    a.runtime.overlay = True
    a.on_click_outside()
    eq("点外面关窗 → 不碰播放器（视频还停着）", ctl.writes, [True])

    a.on_space()
    eq("★ 再按空格 → 恢复播放，而不是又弹一次浮窗",
       (ctl.writes, asked["n"]), ([True, False], 1))
    eq("状态仍是 idle（没进 busy）", a._state, "idle")

    # 反方向：按空格关窗（会顺便恢复播放）之后，再按空格就该是查词了
    a, ctl, asked = build(playing=None, frontmost="")
    a.on_space()
    a._state = "showing"
    a.runtime.overlay = True
    a.on_space()
    eq("按空格关窗 → 恢复播放", ctl.writes, [True, False])
    a.on_space()
    eq("之后按空格 → 回到'暂停 + 查词'（这正是用户要的）",
       (ctl.writes, asked["n"]), ([True, False, True], 2))

    # 读得到真实状态时，陈旧记忆必须被真实状态纠正
    a, ctl, asked = build(playing=True, frontmost="com.colliderli.iina")
    a._video_paused_by_us = True          # 故意留一条过时的记忆
    a.on_space()
    eq("读到「在播」→ 以真实状态为准（暂停 + 查词），不信陈旧记忆",
       (ctl.writes, asked["n"]), ([True], 1))

    a, ctl, asked = build(playing=False, frontmost="com.colliderli.iina")
    a._video_paused_by_us = False         # 同样过时
    a.on_space()
    eq("读到「已停」→ 以真实状态为准（继续播），也不信陈旧记忆",
       (ctl.writes, asked["n"]), ([False], 0))

    # ★ v1.2.0：浏览器通道（网页视频桥）已整体删除，所以"桥报的已停不能全信"
    #   那一段用例随之移除 —— IINA 是唯一的精确通道，它读到的状态就是可信的。
    #   这里把结论钉住：IINA 读到「已停」时，即使记忆是"不是我们停的"，
    #   也照旧按"用户想接着看"处理（不查词）。
    a, ctl, asked = build(playing=False, frontmost="com.colliderli.iina",
                          backend="iina")
    a._video_paused_by_us = False
    a.on_space()
    eq("IINA 读到的「已停」可信 → 继续播放（不查词）",
       (ctl.writes, asked["n"]), ([False], 0))


def test_tap_mask() -> None:
    """E) CGEventTap 的监听掩码 —— 这条是**真机跑出来**的坑。

    原写法把"被系统禁用"的事件**编号**（0xFFFFFFFE / 0xFFFFFFFF）当位号去移位，
    等于要构造 2^4294967294 这个天文数字：
      · 启动时白卡 30 秒（两次移位共约 1GB 内存）；
      · 掩码远超 64 位 → CGEventTapCreate 抛
        `ValueError: depythonifying 'unsigned long long'`，
        日志里只有一行 ERROR，实际后果是**空格三态交互整体不工作**。
    所以这里把"掩码只能是 keyDown|keyUp 两位"钉死。
    """
    print("== E. 事件掩码（只监 keyDown/keyUp，别把事件编号当位号）==")
    m = keytap.tap_mask()
    eq("掩码 = keyDown|keyUp", m, (1 << KD) | (1 << KU))
    eq("掩码正好是 0xC00", m, 0xC00)
    check("掩码塞得进 64 位（CGEventTapCreate 才收得下）",
          m < (1 << 64), f"实际 {m}（{m.bit_length()} 位）")
    check("掩码小得可以忽略（不是天文数字）",
          m.bit_length() <= 32, f"{m.bit_length()} 位")
    for name, val in (("kCGEventTapDisabledByTimeout",
                       keytap._EV_TAP_DISABLED_TIMEOUT),
                      ("kCGEventTapDisabledByUserInput",
                       keytap._EV_TAP_DISABLED_USERINPUT)):
        check(f"{name} 没有被塞进掩码",
              not (m >> val) & 1 if val < m.bit_length() else True)

    # 再钉一层：源码里不该再出现"把事件编号左移"这种写法。
    # 用 AST 看而不是搜文本 —— tap_mask() 的注释里**故意**写了那个错误写法
    # 来解释坑，搜文本会把这个解释性注释误判成违规。
    import ast

    src = (ROOT / "keytap.py").read_text(encoding="utf-8")
    bad = [ast.unparse(n)
           for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.BinOp) and isinstance(n.op, ast.LShift)
           and "_EV_TAP_DISABLED" in ast.unparse(n)]
    check("keytap.py 里没有把『被禁用』事件编号当位号左移的写法",
          not bad, str(bad))


def main() -> int:
    test_decide()
    print()
    test_intercept()
    test_three_state()
    test_click_outside_keeps_playback()
    test_player_control_off()
    test_paused_memory()
    print()
    test_tap_mask()

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
