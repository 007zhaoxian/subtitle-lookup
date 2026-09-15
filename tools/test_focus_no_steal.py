"""回归测试：浮窗显示时**绝不能**把前台 App 抢走。

为什么值得单独写一个测试
------------------------
这个 bug 反复出现过好几次，而且表现为「按空格播放器没反应，得先用鼠标点一下
播放器」—— 用户很难描述、开发者很难复现，每次都要重新排查一遍。

根因已定位（见 tools/probe_focus_steal.py）：Qt 的 `QWidget.raise_()` 在 macOS 上
会**顺带把整个应用激活**。App 一被激活，真实的空格键就不再送给播放器了。

所以本测试分三段：
  A. 静态：源码里不许对顶层浮窗调 raise_()（防手滑改回去）—— **默认只跑这段**
  B. 实测：真调用我们的 _bring_front_without_activating()，前台必须不变
  C. 反证：show() + raise_() 必须**能**被检出抢焦点
          —— 没这一段，A/B 全绿也可能是测试本身没有鉴别力

⚠️ 关于 B/C 段（务必知情）
--------------------------
B/C 段必须在真 cocoa 平台建**真窗口**才测得出焦点行为，而这会带来两个副作用：
  ① 屏幕上会闪出一个无边框浮窗；
  ② 反证那一步会故意 raise_() 抢走当前前台 App 的焦点。
2026-09-15 就因为这个，用户在正看视频时被反复闪黑框、被反复抢焦点。
所以现在 **默认不跑 B/C**（CI / 日常回归都只跑 A，静默、无副作用）。
确实需要端到端验证时，显式开一次：

    DL_TEST_FOCUS_LIVE=1 python tools/test_focus_no_steal.py

即便如此，实测窗口也是 **1×1 全透明、贴在屏幕左上角**（见 _make_card），
肉眼不可见；但抢焦点那一下仍会发生（无法避免，macOS 不允许事后还原前台）。
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# B/C 实测段的总开关：默认关。见文件头「关于 B/C 段」。
LIVE = (os.environ.get("DL_TEST_FOCUS_LIVE") or "").strip() == "1"

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: 实际 {got!r}，期望 {want!r}")
        FAILED.append(name)


# ============================================================ A. 静态检查
print("== A. 静态：顶层浮窗不许调 raise_() ==")

src = (ROOT / "overlay.py").read_text(encoding="utf-8")

# 只看 _show / _progress 这两个"显示浮窗"的函数体
def _body(text: str, func: str) -> str:
    """抠出某个函数/方法的函数体（**自动适配缩进**）。

    踩过的坑：最初写死了 4 空格缩进（`\\n    def X`），结果模块级函数
    （0 缩进）永远抠不出来，断言就变成了"实际 ''，期望 True"的假失败。
    现在从 def 那一行读出真实缩进，再找到下一个**同缩进或更浅**的
    def/class/装饰器作为结束位置。
    """
    m = re.search(rf"\n([ \t]*)def {re.escape(func)}\(", text)
    if not m:
        return ""
    indent = m.group(1)
    rest = text[m.start() + 1:]
    for mm in re.finditer(r"\n([ \t]*)(?:def |class |@)", rest):
        if len(mm.group(1)) <= len(indent):
            return rest[: mm.start()]
    return rest


for fn in ("_show", "_progress"):
    body = _body(src, fn)
    check(f"{fn} 函数体找得到（测试本身有效）", bool(body.strip()), True)
    # 允许 raise_ 出现在注释里（我们在注释里解释这件事），所以先剥掉注释
    code_only = "\n".join(ln.split("#")[0] for ln in body.splitlines())
    check(f"{fn} 里没有 self._overlay.raise_()",
          "self._overlay.raise_()" in code_only, False)
    check(f"{fn} 里没有 self._pill.raise_()",
          "self._pill.raise_()" in code_only, False)

check("_show 用了 _float_over_everything（等效且不抢焦点的置前）",
      "_float_over_everything(self._overlay)" in src, True)
check("_progress 用了 _bring_front_without_activating",
      "_bring_front_without_activating(self._pill)" in src, True)
check("_bring_front_without_activating 底层是 orderFrontRegardless",
      "orderFrontRegardless()" in _body(src, "_bring_front_without_activating"), True)

# 窗口内部**子控件**的 raise_() 是安全的（只调层级，不碰应用激活），必须保留
check("缩放手柄的子控件 raise_() 仍在（那是安全的，别误删）",
      "wdg.raise_()" in src, True)


# ==================================================== B / C. 真机实测
print("\n== B/C. 实测（需要真 cocoa 平台）==")

os.environ.pop("QT_QPA_PLATFORM", None)

from PyQt6.QtCore import Qt                       # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import overlay  # noqa: E402

PLATFORM = (app.platformName() or "").lower()
print(f"     当前 Qt 平台: {PLATFORM}")

if not LIVE:
    print("SKIP  默认不跑实测段（它会在你屏幕上建真窗口，还会抢一下前台焦点）。")
    print("      要端到端验证时再显式开：DL_TEST_FOCUS_LIVE=1 python "
          "tools/test_focus_no_steal.py")
    print("      日常回归靠静态段 A —— 那才是真正要守的回归点。")
elif PLATFORM != "cocoa":
    print("SKIP  非 cocoa 平台（离屏/无 GUI），跳过实测段 —— 静态段仍然有效")
else:
    from AppKit import NSWorkspace

    def frontmost() -> str:
        a = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(a.localizedName()) if a else "?"

    def pump(sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(0.02)

    def frontmost_settled(tries: int = 5, gap: float = 0.15) -> str:
        """多采几次前台，取众数 —— 单次采样会被系统瞬时的焦点切换带偏。"""
        seen: list[str] = []
        for _ in range(max(1, tries)):
            pump(gap)
            seen.append(frontmost())
        return max(set(seen), key=seen.count)

    def is_us(name: str) -> bool:
        return str(name).lower().startswith("python")

    def make_card() -> QWidget:
        """造一个**用户看不见**的浮窗来做焦点实测。

        教训（2026-09-15）：早先这里是个 240×70 的普通 QWidget，又不挪位置，
        于是跑测试时用户屏幕上反复闪出一个黑方块，还被反复抢走前台焦点 ——
        用户当时正在看视频，直接问"屏幕中间那个黑框是什么"。
        现在的做法：1×1 + 全透明 + 贴左上角。窗口真实存在（焦点语义不变，
        鉴别力不丢），但肉眼绝对看不见。

        别再用"挪到屏幕外(-4000,-4000)"这种写法：实测 Qt 会警告
        "outside any known screen, using primary screen"，可能被挪回主屏。
        """
        w = QWidget()
        w.setWindowFlags(Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        w.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        w.resize(1, 1)
        w.move(0, 0)
        return w

    def show_path(use_fix: bool) -> str:
        """弹出浮窗（按修复前/后两种置前方式），返回弹完之后的前台 App。

        踩过的坑：**不要**试图用 NSWorkspace 把别的 App 拉到前台来当靶子。
        macOS 26 已禁止后台进程抢前台：activateWithOptions_ 会返回 True 却
        什么也不做（实测连续 3 秒前台纹丝不动）。那条路只会把测试搞成随机红。
        好在"自激活"是被允许的 —— raise_() 能让我们自己抢到前台，所以
        「旧写法会抢焦点」这个反证可以在本进程内稳定复现。
        """
        w = make_card()
        w.show()
        if use_fix:
            overlay._bring_front_without_activating(w)
        else:
            w.raise_()                      # 故意重现旧写法
        pump(0.7)
        after = frontmost_settled()
        w.close()
        w.deleteLater()
        pump(0.5)
        return after

    # 起点必须是"别的 App"在前台：本进程此时还没有可见窗口，正常情况下成立。
    before = frontmost_settled()
    print(f"     起点前台: {before}")
    if is_us(before):
        print("SKIP  起点就是本进程在前台（环境异常），实测段无意义")
    else:
        # ① 修复后：orderFrontRegardless 置前，前台**绝不能**变成我们自己。
        #    这是本测试唯一要守的硬保证。
        fine_now = show_path(use_fix=True)
        print(f"     修复后（show+orderFrontRegardless）：前台 → {fine_now}")
        check("B. 置前后前台没变成我们自己（没抢焦点）", is_us(fine_now), False)
        if fine_now != before:
            # 系统自身也会挪焦点（实测：起点 WorkBuddy/ToDesk，随后自己跳到
            # Chrome）。这不是我们的锅，所以只报告、不断言。
            print(f"     提示：起点 {before} → 现在 {fine_now}，是系统自身换的前台，"
                  f"与浮窗无关（只作参考，不作为失败）")

        # ② 反证：旧写法 raise_() 应当把自己抢成前台 —— 用来证明本测试有鉴别力。
        #    这一步会真的抢走前台，所以放在最后。
        #    注意：macOS 26 是否放行"自激活"取决于进程环境（实测时好时坏），
        #    放行不了就只报告不复现，别把它算成失败。
        stolen_now = show_path(use_fix=False)
        print(f"     反证（旧写法 show+raise_）：前台 → {stolen_now}")
        if is_us(stolen_now):
            print("     ✅ 反证复现成功：旧写法确实会抢焦点，说明 B 项有鉴别力")
        else:
            print("     说明：本环境未放行自激活，反证无法复现。此时 B 项的"
                  "保证仍然成立，但鉴别力弱于正常环境；静态段 A 不受影响。")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项: {FAILED}")
    sys.exit(1)
print("全部通过：浮窗显示不会抢走播放器的前台焦点")
sys.exit(0)
