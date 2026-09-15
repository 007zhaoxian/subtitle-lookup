"""验证触发键的识别逻辑 —— 纯逻辑测试，不用真按键。

覆盖：
  1. 按配置的触发键                 → 触发
  2. 同类大写形式（Shift / Caps）    → 也要触发（比的是 char 小写形式）
  3. 按别的键（常见的 a/n/m/空格）  → 不触发
  4. Cmd+触发键 / Ctrl+触发键       → 不触发（那是系统/窗口快捷键）
  5. 看剧模式关掉时                 → 不触发
  6. 连按（去抖窗口内）             → 只触发一次；窗口过后能再触发

【2026-09-14 改】以前这里把 'm' 写死在每一行，结果触发键一改成 N 就
9 项错 6 项 —— 测试失败其实是被测对象好好的、只有测试自己过期了。
现在全部跟随 config.TRIGGER_KEY 自动适配，换键不用改测试。

用法: python tools/test_hotkey_match.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from hotkey import HotkeyWatcher, _key_id, _resolve_key  # noqa: E402
from pynput import keyboard  # noqa: E402

results: list[tuple[str, bool]] = []

# 永远不该触发触发逻辑的键（用来验证「不是随便哪个键都行」）
_NOISE_CHARS = [c for c in ("a", "n", "m", "j", "k") if c != config.TRIGGER_KEY]


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def key_of(ch: str):
    """把配置里的键名变成 pynput 的按键对象。"""
    if hasattr(keyboard.Key, ch):
        return getattr(keyboard.Key, ch)
    return keyboard.KeyCode.from_char(ch)


def make(enabled=True):
    fired = {"n": 0}

    def on_trigger():
        fired["n"] += 1

    w = HotkeyWatcher(is_enabled=lambda: enabled, on_trigger=on_trigger,
                      on_cancel=lambda: None)
    # 复用 start() 里的解析逻辑，但不真的起监听线程
    w._key = _resolve_key()
    w._key_vk = _key_id(w._key)
    return w, fired


def press(w, key) -> None:
    w._on_press(key)
    w._on_release(key)


def main() -> int:
    name = config.TRIGGER_LABEL
    raw = config.TRIGGER_KEY
    print(f"当前触发键配置: {raw!r} → 显示名 {name!r}")

    # 触发键本身、（若是单字符）它的大写形式
    hit = key_of(raw)
    hit_upper = (keyboard.KeyCode.from_char(raw.upper())
                 if len(raw) == 1 else hit)

    w, fired = make()
    press(w, hit)
    check(f"按【{name}】触发", fired["n"] == 1)

    w, fired = make()
    press(w, hit_upper)          # 模拟 Shift+N / Caps Lock 打开
    check(f"按【{name}】的大写形式也触发", fired["n"] == 1)

    w, fired = make()
    for ch in _NOISE_CHARS:
        press(w, keyboard.KeyCode.from_char(ch))
    # 空格只有在**不是**触发键时才算干扰键（2026-09-15 起触发键就是空格了）
    extra = ""
    if raw != "space":
        press(w, keyboard.Key.space)
        extra = "+空格"
    check(f"只有【{name}】才触发（{'/'.join(_NOISE_CHARS)}{extra}都不触发）",
          fired["n"] == 0)

    w, fired = make()
    w._on_press(keyboard.Key.cmd)                    # 按住 ⌘
    press(w, hit)
    check(f"Cmd+{name} 不触发", fired["n"] == 0)
    w._on_release(keyboard.Key.cmd)
    press(w, hit)
    check(f"松开 ⌘ 后按【{name}】仍能触发", fired["n"] == 1)

    w, fired = make()
    w._on_press(keyboard.Key.ctrl)
    press(w, hit)
    check(f"Ctrl+{name} 不触发", fired["n"] == 0)

    w, fired = make(enabled=False)
    press(w, hit)
    check("看剧模式关闭时不触发", fired["n"] == 0)

    w, fired = make()
    for _ in range(6):                               # 模拟长按连发
        w._on_press(hit)
    check("长按连发只算一次（去抖）", fired["n"] == 1)
    time.sleep(config.DEBOUNCE_SEC + 0.05)
    w._on_release(hit)
    press(w, hit)
    check("去抖窗口过后可以再次触发", fired["n"] == 2)

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
