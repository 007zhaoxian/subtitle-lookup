"""player 的两条**不依赖 IPC** 的旁路通道（v1.2.3 新增）。

为什么需要它们
--------------
IINA 进程内可能有**多个 mpv core**，它们共用同一个 `input-ipc-server` 路径。
后创建的 core 会 unlink 旧文件再 bind，把路径抢走 —— 于是 `connect()` 可能连到
一个**空转的 core**：`idle-active=true`、`filename` 读不到，而真正在播的那个
core 的 socket 已被 unlink、从文件路径上完全不可达。

后果不是"少读一个值"那么轻：状态读不到 → 调用方按"在播"处理 → 媒体键又是
**切换**语义 → 实际是暂停时这一下就做了**反向动作**（想暂停，结果开始播了）。

所以补两条旁路：
  · `window_media_title()`   —— 屏幕上可见的 IINA 窗口标题（就是文件名）
  · `iina_playback_active()` —— 系统电源断言（IINA 播放时持有）

这个测试盯四件事：
  1. 窗口标题的**筛选**对：只认 IINA、只认屏幕上可见、只认"像媒体文件"的标题；
  2. **绝不遍历隐藏窗口**（隐私红线）—— 实测完整列表里隐藏窗口也带完整文件名；
  3. 层级最前优先，**不是**按窗口面积挑（用户会切成迷你播放器小窗）；
  4. 电源断言的解析对，且"没断言但 IINA 在跑"要判成**已暂停**而不是"问不到"。

跑法：
    python tools/test_player_sidechannel.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(("  [PASS] " if ok else "  [FAIL] ") + name + f"   实际 {got!r}，期望 {want!r}")
    if not ok:
        FAILED.append(name)


def check_true(name: str, got) -> None:
    check(name, bool(got), True)


# ============================================================ 假 Quartz
# 直接给 player 注入一个假的 Quartz 模块，就能在离线环境里把窗口列表
# 编成任意形状 —— 不用真的开 IINA（也就能测"隐藏窗口"这种真机上难复现的情况）。
class _FakeQuartz:
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListOptionAll = 2
    kCGWindowListExcludeDesktopElements = 4
    kCGNullWindowID = 0

    wins: list = []
    seen_options: list = []

    @classmethod
    def CGWindowListCopyWindowInfo(cls, options, wid):
        cls.seen_options.append(options)
        return cls.wins


def win(owner: str, title: str, onscreen=True, number=1, w=1440.0, h=600.0) -> dict:
    return {
        "kCGWindowOwnerName": owner,
        "kCGWindowOwnerPID": 52908,
        "kCGWindowName": title,
        "kCGWindowIsOnscreen": onscreen,
        "kCGWindowNumber": number,
        "kCGWindowBounds": {"Width": w, "Height": h},
    }


sys.modules["Quartz"] = _FakeQuartz                # type: ignore[assignment]
import player                                       # noqa: E402

MOVIE = "火星救援The.Martian.2015.EC.Bluray.2160p.TrueHD7.1.HDR.x265.10bit-DreamHD.mkv"
SHOW = "老友记.Friends.S01E03.1080p.WEB-DL.mkv"


# ============================================================ 1. 标题筛选
print("\n== 1. _looks_like_media：标题像不像磁盘上的媒体文件 ==")
check_true("mkv", player._looks_like_media(MOVIE))
check_true("mp4", player._looks_like_media("a.mp4"))
check_true("大小写不敏感", player._looks_like_media("A.MKV"))
check("设置窗口标题不是媒体文件", player._looks_like_media("用户界面"), False)
check("OpenSubtitles 窗口不是", player._looks_like_media("Window — OpenSubtitles"), False)
check("User Scripts 窗口不是", player._looks_like_media("User Scripts — User Scripts"), False)
check("空标题不是", player._looks_like_media(""), False)
check("裸后缀也放行（后面会被洗成空串）", player._looks_like_media(".mkv"), True)
# 说明：`.mkv` 这种"只有后缀"的极端值按 True 处理没关系 —— 它变不出
# 什么像样的剧名，后面 show_name_from_filename 会把它洗成空串。


# ============================================================ 2. 窗口挑选
print("\n== 2. window_media_title：挑哪个窗口 ==")
_FakeQuartz.wins = [win("IINA", MOVIE)]
check("只有一个媒体窗口 → 用它", player.window_media_title(), MOVIE)

_FakeQuartz.wins = [
    win("IINA", "用户界面", number=2),
    win("IINA", "Window — OpenSubtitles", number=3),
    win("IINA", MOVIE, number=4),
]
check("夹着设置/脚本窗口也要挑出媒体那个", player.window_media_title(), MOVIE)

_FakeQuartz.wins = [win("Google Chrome", MOVIE), win("IINA", SHOW, number=5)]
check("别的 App 的窗口再像也不认", player.window_media_title(), SHOW)

_FakeQuartz.wins = [win("IINA", MOVIE, onscreen=False), win("IINA", SHOW, number=6)]
check("隐藏（onscreen 非真）的窗口必须跳过", player.window_media_title(), SHOW)

_FakeQuartz.wins = [win("IINA", MOVIE, onscreen=False, number=7)]
check("全是隐藏窗口 → 返回空串（宁可退回 IPC，也不碰私密窗口）",
      player.window_media_title(), "")

_FakeQuartz.wins = [win("IINA", "用户界面"), win("IINA", "", number=8)]
check("只有界面窗口 → 空串", player.window_media_title(), "")

_FakeQuartz.wins = []
check("一个窗口都没有 → 空串", player.window_media_title(), "")

# ---- ★ 取层级最前的，而不是面积最大的 ----
# 用户会切 IINA 的**迷你播放器**：实测同一个 window# 从 1440x600 缩成 285x503，
# 编号都没变，小窗口才是他正在看的那个。按面积挑会挑错。
_FakeQuartz.wins = [
    win("IINA", MOVIE, number=9, w=285.0, h=503.0),    # 迷你播放器（正在看）
    win("IINA", SHOW, number=10, w=1440.0, h=600.0),   # 另一个大窗口
]
check("取层级最前（迷你播放器）而不是面积最大的", player.window_media_title(), MOVIE)

# ---- ★ 隐私：只能用 OnScreenOnly，绝不能枚举全部窗口 ----
_FakeQuartz.seen_options = []
_FakeQuartz.wins = [win("IINA", MOVIE)]
player.window_media_title()
used = _FakeQuartz.seen_options[-1]
check_true("只用 OnScreenOnly（隐藏窗口里的私密标题根本不会被读到）",
           used & _FakeQuartz.kCGWindowListOptionOnScreenOnly)
check("**没有**带 OptionAll", bool(used & _FakeQuartz.kCGWindowListOptionAll), False)
check_true("同时排除了桌面元素",
           used & _FakeQuartz.kCGWindowListExcludeDesktopElements)


# ============================================================ 3. 电源断言
print("\n== 3. _assert_says_playing：从 pmset 输出判断在不在播 ==")
PLAYING_TEXT = """Assertion status system-wide:
   PreventUserIdleDisplaySleep    1
Listed by owning process:
   pid 74022(ToDesk): [0x1] 04:12:04 PreventUserIdleDisplaySleep named: "ToDesk Disable Idle System Sleep"
   pid 52908(IINA): [0x2] 00:01:03 PreventUserIdleDisplaySleep named: "IINA playback is in progress"
   pid 184(coreaudiod): [0x3] 00:01:03 PreventUserIdleSystemSleep named: "com.apple.audio..."
"""
PAUSED_TEXT = """Assertion status system-wide:
Listed by owning process:
   pid 74022(ToDesk): [0x1] 04:12:04 PreventUserIdleDisplaySleep named: "ToDesk Disable Idle System Sleep"
   pid 112(powerd): [0x4] 04:25:01 PreventUserIdleSystemSleep named: "Powerd - Prevent sleep while display is on"
"""
check("有 IINA 播放断言 → 在播", player._assert_says_playing(PLAYING_TEXT), True)
check("没有 → 不在播", player._assert_says_playing(PAUSED_TEXT), False)
check("乱七八糟的输入不炸", player._assert_says_playing(""), False)
check("None 也不炸", player._assert_says_playing(None), False)
# 关键：断言名是 IINA 自己写的，得允许它以后换措辞 —— 松匹配只要同行出现
# (IINA) 和 playback is in progress
check("换了个前缀也认",
      player._assert_says_playing('pid 1(IINA): [0x9] 00:00:01 Foo named: "IINA playback is in progress"'),
      True)
check("别的 App 的 playback 断言不算",
      player._assert_says_playing('pid 2(VLC): [0x9] 00:00:01 Foo named: "playback is in progress"'),
      False)


# ============================================================ 4. 状态合成
print("\n== 4. iina_playback_active：断言 + 进程存在性 ==")
_orig_run = player.subprocess.run if hasattr(player, "subprocess") else None


class _FakeCompleted:
    def __init__(self, out="", rc=0):
        self.stdout = out
        self.returncode = rc


def _patch(monkey_run, running: bool):
    """把 subprocess.run / _iina_running 打桩，返回还原函数。"""
    import subprocess as real_sub

    orig_run = real_sub.run
    orig_running = player._iina_running
    real_sub.run = monkey_run                                  # type: ignore[assignment]
    player._iina_running = lambda: running                     # type: ignore[assignment]
    return lambda: (setattr(real_sub, "run", orig_run),
                    setattr(player, "_iina_running", orig_running))


def _run_returning(text):
    def _f(args, **kw):
        if isinstance(args, (list, tuple)) and args and args[0] == "pmset":
            return _FakeCompleted(out=text, rc=0)
        return _FakeCompleted(out="", rc=0)
    return _f


try:
    player._assert_cache.update(at=0.0, active=None)
    undo = _patch(_run_returning(PLAYING_TEXT), running=True)
    try:
        check("有播放断言 → True", player.iina_playback_active(force=True), True)
    finally:
        undo()

    player._assert_cache.update(at=0.0, active=None)
    undo = _patch(_run_returning(PAUSED_TEXT), running=True)
    try:
        check("没断言 + IINA 在跑 → False（已暂停）",
              player.iina_playback_active(force=True), False)
    finally:
        undo()

    player._assert_cache.update(at=0.0, active=None)
    undo = _patch(_run_returning(PAUSED_TEXT), running=False)
    try:
        check("没断言 + IINA 没开 → None（问不到，别说成已暂停）",
              player.iina_playback_active(force=True), None)
    finally:
        undo()

    # 缓存：0.5 秒内不重复 fork pmset（面板 600ms 轮询一次状态）
    player._assert_cache.update(at=0.0, active=None)
    calls = {"n": 0}

    def _counting(args, **kw):
        if isinstance(args, (list, tuple)) and args and args[0] == "pmset":
            calls["n"] += 1
        return _FakeCompleted(out=PLAYING_TEXT, rc=0)

    undo = _patch(_counting, running=True)
    try:
        player.iina_playback_active(force=True)
        player.iina_playback_active()
        player.iina_playback_active()
        check("force 之后紧接着的非 force 调用吃缓存（只 fork 过一次）", calls["n"], 1)
    finally:
        undo()
finally:
    player._assert_cache.update(at=0.0, active=None)

# 恢复成"没有 Quartz 假模块"的正常世界，免得影响同进程后续代码
del sys.modules["Quartz"]

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过")
