"""崩溃修复的回归测试 —— **完全离线**，不需要真按键、不需要权限。

盯的是 2026-09-15 那次真机 SIGTRAP 崩溃（报告：
`~/Library/Logs/DiagnosticReports/SubtitleLookup-2026-09-15-194150.ips`）：

    m_CGEventTapCallBack → Python 帧 → +[NSEvent eventWithCGEvent:]
      → HIToolbox TSM → dispatch_assert_queue 断言失败 → SIGTRAP

根因链（三条，缺一不可）：
  ① 整个环境里只有 `pynput/keyboard/_darwin.py:303` 会调
     `NSEvent.eventWithCGEvent_`；
  ② 它只在 `event_type == NSSystemDefined`（type 14）时被调到；
  ③ pynput 的键盘 tap 掩码里恰好订阅了 type 14。

于是修复=两件事，这个文件就是钉住它们：
  A. 默认配置下**不再创建 pynput 键盘监听**（Esc 改由 keytap 旁听）；
  B. 万一还是要创建（触发键改成非空格 / keytap 不可用），
     必须先把 type 14 那一位从掩码里摘掉。

跑法：
    python3 tools/test_keytap_esc.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                     # noqa: E402
import keytap                                                     # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}\n      期望 {want!r}，实际 {got!r}")


def check_true(name, got):
    check(name, bool(got), True)


# ================================================== 1. Esc 的纯函数判据
print("== 1. esc_should_fire：只旁听、不吞键、只认 keyDown ==")
DOWN, UP = keytap._EV_KEY_DOWN, keytap._EV_KEY_UP
E = keytap.ESC_KEYCODE
S = keytap.SPACE_KEYCODE

check("Esc 按下 → 回调", keytap.esc_should_fire(DOWN, E), True)
check("Esc 抬起 → **不**回调（否则一次 Esc 关两遍）",
      keytap.esc_should_fire(UP, E), False)
check("空格按下 → 不走 Esc 这条路", keytap.esc_should_fire(DOWN, S), False)
check("其它键 → 不走 Esc 这条路", keytap.esc_should_fire(DOWN, 0), False)
check("没接回调时什么都不做", keytap.esc_should_fire(DOWN, E, has_cb=False), False)
check("ESC_KEYCODE 必须是 53（kVK_Escape）", keytap.ESC_KEYCODE, 53)
check("SPACE_KEYCODE 仍然是 49", keytap.SPACE_KEYCODE, 49)

# ================================================== 2. 空格逻辑没被碰坏
print("\n== 2. 空格判定不受 Esc 新分支影响 ==")
d = keytap.decide(DOWN, S, 0, wants=True, down=False)
check("空格该吞、该触发", (d.swallow, d.fire), (True, True))
d = keytap.decide(DOWN, E, 0, wants=True, down=False)
check("Esc 绝不吞键（swallow=False、也不会触发查词）",
      (d.swallow, d.fire), (False, False))
check("should_intercept 只认空格", keytap.should_intercept(E, 0, True), False)

# ================================================== 3. tap 掩码绝不含 type 14
print("\n== 3. 我们自己的 tap 掩码：只有 keyDown/keyUp ==")
m = keytap.tap_mask()
check("掩码 == (1<<10)|(1<<11)", m, (1 << 10) | (1 << 11))
check("掩码里**没有** NSSystemDefined(1<<14) —— 这是崩溃的入口",
      bool(m & (1 << 14)), False)

# ================================================== 4. 默认配置下不建 pynput 监听
print("\n== 4. 默认配置：pynput 键盘监听根本不该被创建 ==")
import hotkey                                                     # noqa: E402

_orig_trig, _orig_close = config.TRIGGER_KEY, config.CLOSE_KEY
config.TRIGGER_KEY, config.CLOSE_KEY = "space", "space"
try:
    w = hotkey.HotkeyWatcher(
        is_enabled=lambda: True,
        on_trigger=lambda: None,
        on_cancel=None,                      # Esc 由 keytap 接管
        is_close_active=None,
        on_close=None,
        watch_trigger=False,
        esc_handled_elsewhere=True,
    )
    w.start()
    check("没有创建 pynput 键盘监听（listener is None）", w._listener, None)

    # 对照：Esc 没人接管时，必须老老实实建一个（否则 Esc 就失灵了）
    w2 = hotkey.HotkeyWatcher(
        is_enabled=lambda: True,
        on_trigger=lambda: None,
        on_cancel=lambda: None,
        watch_trigger=False,
        esc_handled_elsewhere=False,
    )
    print("  …（下面这条会真的起一个 pynput 监听，用于对照）")
    w2.start()
    check_true("Esc 无人接管时仍然会起监听（兜底路径没被误伤）",
               w2._listener is not None)
    w2.stop()
finally:
    config.TRIGGER_KEY, config.CLOSE_KEY = _orig_trig, _orig_close

# ================================================== 5. 掩码补丁真的摘掉了那一位
print("\n== 5. 兜底路径：必须摘掉 NSSystemDefined 订阅 ==")
try:
    from Quartz import CGEventMaskBit, NSSystemDefined
    from pynput.keyboard import _darwin as pkd

    bit = CGEventMaskBit(NSSystemDefined)
    before = pkd.Listener._EVENTS
    hotkey._avoid_pynput_media_key_crash()
    after = pkd.Listener._EVENTS
    check("补丁后掩码里没有 type 14 这一位", bool(after & bit), False)
    check("补丁前后只差这一位", before & ~bit, after)
    check("keyDown/keyUp/flagsChanged 三位都还在（没有误删）",
          after & (CGEventMaskBit(10) | CGEventMaskBit(11) | CGEventMaskBit(12)),
          CGEventMaskBit(10) | CGEventMaskBit(11) | CGEventMaskBit(12))
    # 幂等：再打一次不能出问题
    hotkey._avoid_pynput_media_key_crash()
    check("补丁是幂等的", pkd.Listener._EVENTS, after)
    pkd.Listener._EVENTS = before
except Exception as exc:                                          # noqa: BLE001
    FAIL += 1
    print(f"  ❌ 掩码补丁测试异常: {type(exc).__name__}: {exc}")

print(f"\n共 {PASS + FAIL} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
