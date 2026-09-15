"""悬浮框「文字大小」设置 + 示例的回归测试。

盯的是用户 2026-09-15 的第 5 条要求：
「可以让我在设置界面去设置悬浮框的文字大小，放个示例。」

这个测试要钉住三件容易被改坏的事：
  1. **字号真的贯通到渲染**：改了 settings 里的字号，`build_html` 出来的
     HTML 里 font-size 必须跟着变（不是存了但没人读）。
  2. **设置面板真的能存**：拖动滑块 → 落盘 → 读回来还是那个值；
     拖动过程中不会每动一下写一次盘（防抖），但**示例是实时重画的**。
  3. **范围被钳死**：手改 settings.json 写个 -999 或 9999 也不能让浮窗变成
     0 号字或者一行一个字。

offscreen 平台跑，不需要屏幕录制权限、不弹任何窗口。

跑法：
    python3 tools/test_overlay_font.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_font_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                     # noqa: E402
import settings                                                   # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}\n      期望 {want!r}\n      实际 {got!r}")


SAMPLE = ("句子：You're gonna have to face the music sooner or later.\n"
          "译文：你迟早得为自己做的事承担后果。\n"
          "生词：gonna /ˈɡɔːnə/ v. going to 的口语缩略 词根词缀：无（缩略词）\n"
          "生词：sooner or later /ˈsuːnər ɔːr ˈleɪtər/ adv. 迟早，早晚\n"
          "俚语：face the music → 承担（自己行为带来的）后果\n"
          "结构：have to 后面接的是省略了 that 的从句，语气上是被迫的")

# ================================================== 1. settings 存取 + 钳制
print("== 1. settings：字号存得住、范围钳得死 ==")
check("默认值 = config.OVERLAY_FONT_SIZE",
      settings.get_overlay_font_size(), int(config.OVERLAY_FONT_SIZE))
check("写 22 后读回来是 22",
      (settings.set_overlay_font_size(22), settings.get_overlay_font_size()),
      (True, 22))
check("写 -5 被钳到下限",
      (settings.set_overlay_font_size(-5), settings.get_overlay_font_size()),
      (True, int(config.OVERLAY_FONT_MIN)))
check("写 9999 被钳到上限",
      (settings.set_overlay_font_size(9999), settings.get_overlay_font_size()),
      (True, int(config.OVERLAY_FONT_MAX)))
check("写 'abc' 退回默认",
      (settings.set_overlay_font_size("abc"), settings.get_overlay_font_size()),
      (True, int(config.OVERLAY_FONT_SIZE)))
settings.set_overlay_font_size(15)

# ================================================== 2. ui_sizes 推导
print("\n== 2. ui_sizes：整套字号由正文推导，且都封顶 ==")
import overlay                                                    # noqa: E402

s15 = overlay.ui_sizes(15)
check("正文 = 传进去的值", s15["body"], 15)
check("chip 比正文小", s15["chip"] < s15["body"], True)
check("body 显式传入时优先于 settings",
      overlay.ui_sizes(26)["body"], 26)
s_min = overlay.ui_sizes(config.OVERLAY_FONT_MIN)
s_max = overlay.ui_sizes(config.OVERLAY_FONT_MAX)
check("最小字号下也要看得清（chip/hint/title 都有下限）",
      min(s_min["chip"], s_min["hint"], s_min["title"]) >= 9, True)
check("最大字号下标题被封印在 20（否则顶部那行排不下）",
      s_max["title"], 20)
check("最大字号下提示被封印在 14", s_max["hint"], 14)
check("输入框字号固定 13（它的高度写死 28px）", s_max["input"], 13)
check("字号是单调的：22 的正文比 15 大",
      overlay.ui_sizes(22)["body"] > s15["body"], True)

# ================================================== 3. body_font_px 真的读 settings
print("\n== 3. body_font_px：每次渲染都重新读 settings ==")
settings.set_overlay_font_size(21)
check("读到 21", overlay.body_font_px(), 21)
settings.set_overlay_font_size(13)
check("改成 13 立刻读到 13", overlay.body_font_px(), 13)


# 让 settings 抛异常，确认有兜底（不能让浮窗因为读设置失败而崩）
class _Boom:
    def get_overlay_font_size(self):
        raise RuntimeError("设置文件坏了")


_orig_settings_mod = sys.modules.get("settings")
sys.modules["settings"] = _Boom()                                 # type: ignore[assignment]
try:
    check("settings 读不到时退回 config 默认值",
          overlay.body_font_px(), int(config.OVERLAY_FONT_SIZE))
finally:
    if _orig_settings_mod is not None:
        sys.modules["settings"] = _orig_settings_mod
    else:                                                         # pragma: no cover
        del sys.modules["settings"]
settings.set_overlay_font_size(15)

# ================================================== 4. 字号贯通到 HTML
print("\n== 4. 字号真的贯通到浮窗 HTML（不是存了没人读）==")
small = overlay.build_html("字幕生词", SAMPLE, overlay.ui_sizes(12))
big = overlay.build_html("字幕生词", SAMPLE, overlay.ui_sizes(26))
check("小字号 HTML 里有 12px", "font-size:12px" in small, True)
check("大字号 HTML 里有 26px", "font-size:26px" in big, True)
check("两份 HTML 不一样", small != big, True)
check("正文里的字都在", all(x in big for x in
                          ("face the music", "承担（自己行为带来的）后果",
                           "sooner or later")), True)
# 五种条目的彩色标签都要 render 出来 —— 这是"易读醒目"的抓手
for label in ("句子", "译文", "生词", "俚语", "结构"):
    check(f"「{label}」标签带背景色渲染",
          "background-color:rgba(" in big and label in big, True)

# 走真 settings 的路径（不显式传 sizes）也要跟着变
settings.set_overlay_font_size(11)
real_small = overlay.build_html("字幕生词", SAMPLE)
settings.set_overlay_font_size(28)
real_big = overlay.build_html("字幕生词", SAMPLE)
check("不传 sizes 时也用 settings 里的字号（11px）",
      "font-size:11px" in real_small, True)
check("不传 sizes 时也用 settings 里的字号（28px）",
      "font-size:28px" in real_big, True)
settings.set_overlay_font_size(15)

# ================================================== 5. 设置面板：滑块 + 实时示例
print("\n== 5. 设置面板：滑块、示例、落盘 ==")
try:
    from PyQt6.QtWidgets import QApplication
except Exception as exc:                                          # pragma: no cover
    print(f"  ⚠️ 没有 PyQt6，跳过面板部分：{exc}")
else:
    qapp = QApplication.instance() or QApplication(["FontTest"])
    import panel

    class _StubCtrl:
        def __init__(self):
            self.font_calls: list[int | None] = []

        def is_mode_on(self):
            return False

        def is_busy(self):
            return False

        def is_asking(self):
            return False

        def status_text(self):
            return "空闲"

        def prompt_is_customized(self):
            return False

        def prompt_preview(self):
            return "默认"

        def apply_overlay_font(self, px=None):
            self.font_calls.append(px)
            if px is not None:
                settings.set_overlay_font_size(int(px))

        def __getattr__(self, _n):
            return lambda *a, **k: None

    ctrl = _StubCtrl()
    P = panel.make_panel_class()
    p = P(ctrl)

    check("滑块下限 = config.OVERLAY_FONT_MIN",
          p.sld_font.minimum(), int(config.OVERLAY_FONT_MIN))
    check("滑块上限 = config.OVERLAY_FONT_MAX",
          p.sld_font.maximum(), int(config.OVERLAY_FONT_MAX))
    check("滑块初始值 = 当前设置", p.sld_font.value(),
          settings.get_overlay_font_size())
    check("示例卡一开始就有内容", bool(p.lbl_preview.text()), True)
    check("示例渲染出的 HTML 带字号",
          "font-size:" in p.lbl_preview.text(), True)
    check("示例用的是**浮窗那套**渲染（有彩色标签）",
          "background-color:rgba(" in p.lbl_preview.text(), True)
    check("示例里五种条目都在（与提示词的那五种行一一对应）",
          all(x in p.lbl_preview.text()
              for x in ("句子", "译文", "生词", "俚语", "结构")), True)
    check("标签页上的 px 数字显示出来了", p.lbl_font.text(),
          f"{p.sld_font.value()} px")

    # 拖动滑块 → 示例当场变，但**还没落盘**（防抖）
    before_html = p.lbl_preview.text()
    p.sld_font.setValue(24)
    check("拖动后示例立刻重画", p.lbl_preview.text() != before_html, True)
    check("新字号出现在示例 HTML 里",
          "font-size:24px" in p.lbl_preview.text(), True)
    check("px 数字跟着更新", p.lbl_font.text(), "24 px")
    check("防抖期间还没写盘（避免每个像素都 fsync）",
          settings.get_overlay_font_size(), 15)

    # 防抖到期 → 通知 App 落盘 + 让浮窗重画
    p._save_font()
    check("落盘后 settings 里是新值", settings.get_overlay_font_size(), 24)
    check("同时通知了 App 刷新浮窗", ctrl.font_calls[-1], 24)

    # 外部（手改 settings.json）改了字号 → 面板要跟上
    settings.set_overlay_font_size(18)
    p._sync_font()
    check("外部改过之后滑块会同步到 18", p.sld_font.value(), 18)

    # 观看记录开关
    check("观看记录复选框默认勾上（默认开）", p.chk_viewlog.isChecked(), True)
    settings.set_viewlog_enabled(False)
    p._sync_viewlog()
    check("关掉之后复选框跟着取消", p.chk_viewlog.isChecked(), False)
    settings.set_viewlog_enabled(True)

    p.hide()
    settings.set_overlay_font_size(15)

# ================================================== 6. 浮窗 restyle
print("\n== 6. 浮窗已经开着时，改字号要当场重画 ==")
try:
    from PyQt6.QtWidgets import QApplication
except Exception as exc:                                          # pragma: no cover
    print(f"  ⚠️ 没有 PyQt6，跳过：{exc}")
else:
    qapp = QApplication.instance() or QApplication(["FontTest2"])
    from overlay import SharedUiState, _make_widget_class

    cls = _make_widget_class()
    settings.set_overlay_font_size(13)
    ov = cls("字幕生词", SAMPLE, lambda: None,
             on_ask=lambda _t: None, ui_state=SharedUiState(), prev_app=None)
    html13 = ov._base_html
    check("构造时用的是 13px", "font-size:13px" in html13, True)
    settings.set_overlay_font_size(25)
    check("还没 restyle 时 HTML 仍是旧的", ov._base_html, html13)
    ov.restyle()
    check("restyle 之后 HTML 变成 25px",
          "font-size:25px" in ov._base_html, True)
    check("restyle 用的是**当前**设置（25）", overlay.body_font_px(), 25)
    check("restyle 不会改动题目/正文（内容没丢）",
          (ov._title, ov._body), ("字幕生词", SAMPLE))
    check("控件里的富文本也换了",
          "font-size:25px" in ov.browser.toHtml(), True)
    ov.close()
    settings.set_overlay_font_size(15)

print(f"\n共 {PASS + FAIL} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
