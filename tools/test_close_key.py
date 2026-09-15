"""「浮窗显示时按空格关掉它」的回归测试 —— 也是**不吞键**的守卫测试。

用户诉求原文：
  ③ 当出现悬浮窗的时候，设置成按空格，就是关闭这个悬浮窗。
  ④ ...我希望摁空格关闭悬浮窗后我再摁空格，直接就可以播放。

所以这里要钉死的是**边界**，不是"能关掉"这么简单：
  · 浮窗显示着    → 空格关掉它；          ← ③
  · 浮窗不在屏幕上 → 空格**完全不管**，原样交给播放器；  ← ④ 的前提
  · 正在输入框打字 → 空格就是"打一个空格"，不许关窗；
  · 带着 ⌘/⌃/⌥   → 不是我们的（Cmd+空格 是输入法切换）；
  · 关窗之后状态必须变回 idle，否则长按连发会反复触发。

用法: python tools/test_close_key.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_close_")
# 追问链路会写观看记录 → 重定向，别在用户桌面上留文件
os.environ["DL_VIEWLOG_DIR"] = tempfile.mkdtemp(prefix="dl_test_close_vl_")
# ★ v1.2.2：网页版豆包已删除，查词只剩直连 API 一条通道。本文件测的是
#   **状态机与关窗逻辑**（和"怎么把图问出去"无关），所以这里把 api_client.chat
#   换成一个"卡住不返回"的假函数（见 test_app_state 里的 _blocking_chat）：
#   追问线程会停在上面不动，状态就稳稳停在 ASKING，断言不会和后台线程赛跑。

import config  # noqa: E402
import hotkey as hotkey_mod  # noqa: E402
from hotkey import HotkeyWatcher, _key_id, _resolve_key  # noqa: E402
from pynput import keyboard  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


# ======================================================================
# ① 按键监听层
# ======================================================================
def make_watcher(enabled=True, close_active=True):
    """造一个没起监听线程、但和 start() 解析逻辑一致的 watcher。"""
    fired = {"trigger": 0, "close": 0}
    state = {"active": close_active, "enabled": enabled}

    w = HotkeyWatcher(
        is_enabled=lambda: state["enabled"],
        on_trigger=lambda: fired.__setitem__("trigger", fired["trigger"] + 1),
        on_cancel=lambda: None,
        is_close_active=lambda: state["active"],
        on_close=lambda: fired.__setitem__("close", fired["close"] + 1),
    )
    w._key = _resolve_key()
    w._key_vk = _key_id(w._key)
    if (config.CLOSE_KEY_ENABLED and config.CLOSE_KEY
            and config.CLOSE_KEY != config.TRIGGER_KEY):
        w._close = _resolve_key(config.CLOSE_KEY)
        w._close_vk = _key_id(w._close)
    return w, fired, state


def space_key():
    return keyboard.Key.space


def test_watcher() -> None:
    print(f"触发键 = {config.TRIGGER_KEY!r}（{config.TRIGGER_LABEL}）"
          f" · 关闭键 = {config.CLOSE_KEY!r}（{config.CLOSE_KEY_LABEL}）\n")

    if config.CLOSE_KEY != "space":
        check("关闭键不是空格（跳过空格相关用例）", True)

    # 【2026-09-15 起】触发键 = 关闭键 = 空格。
    # watcher 层不再区分"关窗 / 查词"（一个键两种含义会乱），统一交给
    # **App 状态机**判断：浮窗在 → 关；不在 → 查词（见下面的 test_app_state）。
    # 所以这里钉死的是：watcher 必须如实把空格转发出去，且不重复装关闭键。
    same_key = (config.CLOSE_KEY == config.TRIGGER_KEY)

    if same_key:
        w, fired, state = make_watcher(close_active=True)
        check("触发键与关闭键相同时，不另装一个关闭键（避免一键两义）",
              w._close is None)
        w._on_press(space_key())
        w._on_release(space_key())
        check("空格如实转发给 App 状态机（由它决定关窗还是查词）",
              fired["trigger"] == 1 and fired["close"] == 0)
    else:
        # ③ 浮窗显示着 → 空格关掉它
        w, fired, state = make_watcher(close_active=True)
        w._on_press(space_key())
        w._on_release(space_key())
        check("浮窗显示中，按空格 → 关闭回调被调用", fired["close"] == 1)
        check("关窗不算「触发查词」", fired["trigger"] == 0)

        # ④ 浮窗不在 → 空格完全不干预
        w, fired, state = make_watcher(close_active=False)
        for _ in range(3):
            w._on_press(space_key())
            w._on_release(space_key())
        check("浮窗不在时，空格什么都不做（归播放器）",
              fired["close"] == 0 and fired["trigger"] == 0)

    # 看剧模式关闭 → 空格也不管
    w, fired, state = make_watcher(enabled=False, close_active=True)
    w._on_press(space_key())
    check("看剧模式关闭时，空格不管",
          fired["close"] == 0 and fired["trigger"] == 0)

    # 带修饰键 → 不是我们的（Cmd+空格 是输入法）
    w, fired, state = make_watcher(close_active=True)
    w._on_press(keyboard.Key.cmd)
    w._on_press(space_key())
    check("Cmd+空格 不响应（那是输入法切换）",
          fired["close"] == 0 and fired["trigger"] == 0)
    w._on_release(keyboard.Key.cmd)
    w._on_release(space_key())

    # 长按连发：去抖窗口内只算一次（不管是关窗还是查词）
    w, fired, state = make_watcher(close_active=True)
    for _ in range(6):
        w._on_press(space_key())
    check("长按空格只响应一次（去抖生效）",
          fired["close"] + fired["trigger"] == 1)

    # 触发键本身不受影响
    w, fired, state = make_watcher(close_active=False)
    w._on_press(_resolve_key())
    w._on_release(_resolve_key())
    check("浮窗不在时，触发键照常触发查词", fired["trigger"] == 1)

    # 关闭键和触发键撞车时不装关闭键（一个键两种含义必乱）
    w = HotkeyWatcher(is_enabled=lambda: True, on_trigger=lambda: None,
                      is_close_active=lambda: True, on_close=lambda: None)
    w._key = _resolve_key()
    w._key_vk = _key_id(w._key)
    w._close = None
    check("关闭键未配置时不报错、不误触发", w._close is None)


# ======================================================================
# ② App 状态机层（用假 runtime / 假 worker，不碰浏览器）
# ======================================================================
class FakeRuntime:
    def __init__(self) -> None:
        self.shown = []
        self.hidden = 0
        self.answers = []
        self.statuses = []
        self.typing = False
        self.overlay = True

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
        self.answers.append((text, error))

    def post_overlay_status(self, text) -> None:
        self.statuses.append(text)

    def post_esc(self) -> None:
        self.post_hide()


def test_app_state() -> None:
    import app as app_mod

    app_mod.notify = lambda *a, **k: None          # 测试里别真弹通知
    # ★ 追问会真的开一个后台线程去调 api_client.chat。这里换成"卡住不返回"的
    #   假函数：线程停住 → 状态稳稳停在 ASKING，断言不会跟后台线程抢时序。
    #   测试结束统一 GATE.set() 放它们走（daemon 线程，不放也不影响退出）。
    import threading as _thr

    GATE = _thr.Event()

    def _blocking_chat(messages, cfg=None, timeout=None, max_tokens=None):
        GATE.wait(timeout=8)
        return "（测试占位回答）"

    app_mod.api_client.chat = _blocking_chat
    app_mod.api_client.encode_image = lambda _p: "data:image/png;base64,FAKE"
    # 别真的去截屏（单元测试里没有屏幕录制权限，也不该依赖它）
    app_mod.screenshot = type("_FakeShot", (), {
        "capture_fullscreen": staticmethod(lambda: Path("/tmp/_fake_shot.png")),
        "cleanup": staticmethod(lambda _p: None),
    })
    # 【2026-09-15】空格现在会**主动控制播放器**（player.py）。单元测试里必须
    # 换成假的 —— 真 controller 在只有系统媒体键可用时会真的合成一个媒体键
    # 事件，把用户正在听的音乐切掉。
    class _FakeCtl:
        def __init__(self):
            self.writes = []

        def status(self):
            return {"backend": "iina", "playing": True, "exact": True,
                    "detail": "假播放器"}

        def set_paused(self, paused):
            self.writes.append(bool(paused))
            return {"ok": True, "backend": "iina", "exact": True, "detail": "假"}

    class _FakePlayer:
        def __init__(self):
            self.ctl = _FakeCtl()
            self.frontmost = ""

        def controller(self):
            return self.ctl

        def frontmost_backend(self):
            return self.frontmost

    app_mod.player = _FakePlayer()
    from app import ASKING, BUSY, IDLE, SHOWING, App

    def fresh():
        a = App()
        a.runtime = FakeRuntime()
        a._mode_on = True                          # 直接置位，绕开通知/隐藏浮窗
        a._capture_and_ask = lambda: a.__setattr__(  # 别真截图
            "_asked", getattr(a, "_asked", 0) + 1)
        # 假的播放器控制器是共享的一份，每例开头清掉流水，断言才不会被前面几例污染
        app_mod.player.ctl.writes.clear()
        return a

    # ③ 浮窗显示中按空格 → 关闭
    a = fresh()
    a._state = SHOWING
    a.on_space()
    check("App：浮窗显示中按空格 → 状态回到 idle 且隐藏浮窗",
          a._state == IDLE and a.runtime.hidden == 1)

    # 【2026-09-15 新语义】浮窗不在时的空格不再"什么都不做"了：
    # 视频在播 → 暂停 + 查词（详见 tools/test_space_state.py）。
    a = fresh()
    a._state = IDLE
    a.on_space()
    check("App：浮窗不在时按空格 → 暂停画面并开始查词（三态交互的 ①）",
          a._state == BUSY and a.runtime.hidden == 0
          and app_mod.player.ctl.writes == [True])

    # 正在输入框打字时：空格是打空格，不许关窗；触发键也不许查词
    a = fresh()
    a._state = SHOWING
    a.runtime.typing = True
    a.on_space()
    a.on_trigger()
    check("App：正在浮窗打字时，空格不关窗、触发键不查词",
          a._state == SHOWING and a.runtime.hidden == 0)

    # 【2026-09-15 新语义】空格既是触发键又是关闭键，靠状态机分流：
    #   浮窗在 → 关掉它；浮窗不在 → 开始查词。
    a = fresh()
    a._state = SHOWING
    a.on_trigger()
    check("App：浮窗显示中按【空格】（走触发路径）→ 关窗",
          a._state == IDLE and a.runtime.hidden == 1)

    a = fresh()
    a._state = IDLE
    a.on_trigger()
    check("App：浮窗不在时按【空格】→ 开始查词（进入 busy）", a._state == BUSY)

    # 追问进行中：触发键忽略（避免把浮窗顶掉）
    a = fresh()
    a._state = ASKING
    a.on_trigger()
    check("App：追问回答中忽略触发键", a._state == ASKING)

    # 追问进行中按空格：也忽略（回答还没到，别把浮窗关了）
    a = fresh()
    a._state = ASKING
    a.on_space()
    check("App：追问回答中忽略空格", a._state == ASKING and a.runtime.hidden == 0)

    # ⑤ 追问链路：submit_question → ASKING → 答案回来 → SHOWING + 追加显示
    #    ★ v1.2.2 改法：不再去翻"worker 收到了什么"，而是直接调回答回调。
    #      理由 —— 真实链路是"后台线程拿到回答 → 调 _finish_answer_ok"，
    #      本文件关心的正是**回调之后状态机怎么走**（这是纯逻辑，可确定性断言）；
    #      至于线程有没有真的把历史接上，由 test_lookup_api.py 那套 mock 网络守。
    a = fresh()
    a._state = SHOWING
    a.submit_question("这句里的 face the music 是什么语气？")
    check("App：追问 → 状态推到 asking", a._state == ASKING)
    check("App：记下了这一问（要写进观看记录）",
          a._last_question.startswith("这句里"), True)
    a._finish_answer_ok("这里的 face the music 是「接受后果」的口语说法。")
    check("App：回答回来后状态回到 showing", a._state == SHOWING, a._state)
    check("App：回答已追加到浮窗",
          a.runtime.answers and "face the music" in a.runtime.answers[0][0])

    # 空问题直接被忽略（连状态都不动）
    a = fresh()
    a.submit_question("   ")
    check("App：空问题被忽略", a._state == IDLE and not a.runtime.answers)

    # 浮窗在回答回来之前被关掉 → 只写日志，状态回 idle
    a = fresh()
    a._state = SHOWING
    a.submit_question("再讲一下 blow it")
    a.runtime.overlay = False
    a._finish_answer_ok("blow it = 搞砸了。")
    check("App：浮窗已关时回答只写日志、状态回 idle",
          a._state == IDLE and not a.runtime.answers)

    # 追问失败 → 错误信息追加到浮窗
    a = fresh()
    a._state = SHOWING
    a.submit_question("随便问点啥")
    a._finish_answer_err(RuntimeError("消息没能发出去"))
    check("App：追问失败 → 浮窗里给出错误提示",
          a.runtime.answers and a.runtime.answers[0][1] is True)

    GATE.set()          # 放掉前面那些卡住的假请求线程

    # 消息发出去之后：状态是 showing，下一次按空格就该关掉它（并继续播）
    a = fresh()
    a._state = SHOWING
    a.runtime.overlay = True
    app_mod.player.ctl.writes.clear()
    a.on_space()
    check("App：查词结果浮窗 → 按空格关掉 → 状态回 idle 且视频继续播",
          a._state == IDLE and a.runtime.hidden == 1
          and app_mod.player.ctl.writes == [False])
    # 关窗那一下之后，用户马上又按一次空格 = 想接着看，不该再弹浮窗
    # （这条在 tools/test_space_state.py 里按"已停→继续播"完整覆盖）


def test_shared_state() -> None:
    """打字状态是跨线程读的，必须真的能同步 —— 否则空格含义会错。"""
    from overlay import SharedUiState

    st = SharedUiState()
    check("初始：不在打字", st.typing() is False)
    st.set_typing(True)
    check("进入打字 → 另一个线程读得到", st.typing() is True)
    check("打字期间 Esc 不算被吃掉", st.esc_was_consumed() is False)
    st.set_typing(False)
    check("退出打字 → 读得到", st.typing() is False)
    check("刚退出打字时，重复的 Esc 被挡掉（输入框已经吃过一次）",
          st.esc_was_consumed() is True)
    check("同一条规则让 Esc 只生效一次", st.esc_was_consumed(window=0.0) is False)


def main() -> int:
    test_watcher()
    print()
    test_app_state()
    print()
    test_shared_state()

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
