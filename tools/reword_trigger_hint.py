"""一次性把用户可见文案里的「空格」统一改成配置里的触发键说法。

只改**字符串字面量里的中文文案**，不动注释里的技术描述（比如
「pynput 不吞键」这类说明保留原样也可以，但这里一并说明清楚）。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (文件, 原文, 新文)
PAIRS = [
    # ---------------- app.py
    ("app.py",
     '"已显示结果（再按【空格】关闭）"',
     'f"已显示结果（再按【{KEY}】关闭）"'),
    ("app.py",
     '"按【空格】没反应的话，检查一下菜单里『开启看剧模式』有没有勾上。"',
     'f"按【{KEY}】没反应的话，检查一下菜单里『开启看剧模式』有没有勾上。"'),
    ("app.py",
     'lines.append("① 辅助功能 —— 让它能“听见”你按空格键")',
     'lines.append(f"① 辅助功能 —— 让它能“听见”你按{KEY}键")'),
    # ---------------- doubao.py
    ("doubao.py",
     '"再按一次【空格】重试通常就好了（已保存页面快照）。"',
     'f"再按一次【{config.TRIGGER_LABEL}】重试通常就好了（已保存页面快照）。"'),
    ("doubao.py",
     '"常见原因：① 豆包这次响应异常 → 再按一次【空格】重试；"',
     'f"常见原因：① 豆包这次响应异常 → 再按一次【{config.TRIGGER_LABEL}】重试；"'),
    # ---------------- utils.py
    ("utils.py",
     '"  1) 辅助功能 (Accessibility)  —— pynput 监听空格键\\n"',
     '"  1) 辅助功能 (Accessibility)  —— 监听触发键（默认 M）\\n"'),
    # ---------------- panel.py
    ("panel.py",
     '"美剧沉浸式 AI 查词 · 开启看剧模式后，按【空格】暂停并查词"',
     'f"美剧沉浸式 AI 查词 · 开启看剧模式后，按【{KEY}】识别当前字幕"'),
    ("panel.py",
     '"辅助功能（听空格键）、屏幕录制（截图）"',
     'f"辅助功能（听{KEY}键）、屏幕录制（截图）"'),
    ("panel.py",
     '"不用按空格，直接截屏一次并查词 —— 用来验证整条链路"',
     f'"不用按键，直接截屏一次并查词 —— 用来验证整条链路"'),
    ("panel.py",
     '"关掉这个窗口不会退出程序 —— 它继续在菜单栏驻留，随时按【空格】查词；"',
     'f"关掉这个窗口不会退出程序 —— 它继续在菜单栏驻留，随时按【{KEY}】查词；"'),
    ("panel.py",
     'else "开启看剧模式　（遇到生词按【空格】）"',
     'else f"开启看剧模式　（遇到生词按【{KEY}】）"'),
    ("panel.py",
     '"看剧模式：" + ("✅ 已开启，正在监听空格键" if on else "⏸ 未开启")',
     'f"看剧模式：" + (f"✅ 已开启，正在监听{KEY}键" if on else "⏸ 未开启")'),
    ("panel.py",
     '"提示：按【空格】后屏幕上会先冒出一个「正在识别…」小条，"',
     'f"提示：按【{KEY}】后屏幕上会先冒出一个「正在识别…」小条，"'),
]


def main() -> int:
    changed = 0
    missing: list[str] = []
    for rel, old, new in PAIRS:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        if old not in text:
            missing.append(f"{rel}: {old[:40]}")
            continue
        p.write_text(text.replace(old, new), encoding="utf-8")
        changed += 1
        print(f"[ok] {rel}: {old[:44]}")
    print(f"\n共替换 {changed} 处，未命中 {len(missing)} 处")
    for m in missing:
        print("  [miss]", m)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
