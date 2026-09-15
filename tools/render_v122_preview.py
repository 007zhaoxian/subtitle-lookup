"""把 v1.2.2 的几件东西离屏渲染成 PNG，用来"用眼睛"验收。

产出的图分别对应这次改动的点：
    format_15.png    —— 浮窗正文的**稳定版式** + 一条追问的自由格式回答
    prompt_edit.png  —— 「编辑提示词」窗口（现在只剩主提示词一节，已无 guard 一节）
    viewlog_file.md  —— 桌面「观看记录」长什么样（编号 + 时间 + 追问）

为什么要离屏渲染：offscreen 平台下 `QWidget.grab()` 不需要屏幕录制权限，
拿到的就是真实排版结果（浮窗正文用 QTextBrowser，纯 Qt，不是 WebEngine，
所以离屏渲染和真机完全一致）。

★ 面板那两张（panel.png / panel_display.png）由 tools/render_panel.py 出，
  这里不重复渲染。

跑法：
    <venv>/bin/python tools/render_v122_preview.py [输出目录]
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DL_SETTINGS_DIR", tempfile.mkdtemp(prefix="v122_render_"))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, "tools", "_render")

# 一份"刚好把五种行都用上"的答案——和设置面板里的示例同源，
# 这样用户拿面板里的预览和图里对得上。
BODY = (
    "句子：I've been meaning to tell you, but I never got around to it.\n"
    "译文：我一直想告诉你，可就是一直没找到机会说。\n"
    "生词：mean to /ˈmiːnɪŋ tuː/ phr. 打算、有意（做某事） 词根词缀：mean(意欲)+ing(动名词)\n"
    "生词：get around to /ɡet əˈraʊnd tuː/ phr. 抽出时间去做（拖了很久的事）\n"
    "俚语：face the music → 承担（自己行为带来的）后果\n"
    "结构：but 连接两个并列分句；后半句省略了宾语 it，指代前半句那件事"
)

# 追问：**故意写成自由格式**（分点 + 例子），
# 因为 v1.2.2 的修复点就是"追问不必再套那五行模板"。
FOLLOWUP_Q = "get around to 如果我是在跟老板说“这事我尽快处理”，能这么用吗？"
FOLLOWUP_A = (
    "能，但语气差一点，得看你想给老板什么感觉：\n"
    "\n"
    "1. get around to 的核心是“一直想做、拖到现在才做”，隐含**我自己拖延**。\n"
    "   对老板说 I'll get around to it，等于承认“我早该做了但没做”。\n"
    "2. 想表达“尽快处理”且不背拖延的锅，用下面这些更稳：\n"
    "   · I'll get right on it. —— 马上办，最贴合你的意思。\n"
    "   · I'll take care of it today. —— 给一个明确时间，老板最爱听。\n"
    "   · I'll look into it. —— 需要先查一下再动手时用。\n"
    "3. 反过来，如果你是想**委婉承认延误**：\n"
    "   Sorry, I've been meaning to get around to it. —— 这时候用它才对味。"
)

VIEWLOG = """# 观看记录 · The Bear

- 日期：2026-09-15
- 来源：看剧查词-美剧（自动生成，设置面板里可以关掉）

> 编号按写入顺序排列；时间是你按空格查词的那一刻。

---

## 001 · 21:04:11

句子：I've been meaning to tell you, but I never got around to it.

译文：我一直想告诉你，可就是一直没找到机会说。

生词：get around to /ɡet əˈraʊnd tuː/ phr. 抽出时间去做（拖了很久的事）

俚语：face the music → 承担（自己行为带来的）后果

---
## 002 · 21:07:38 · 追问

> 问：face the music 这里是什么语气？

偏口语、略带自嘲，意思是"该来的躲不掉"。这里不像威胁，更像自嘲认命。
"""


def _pump(app, n: int = 4) -> None:
    for _ in range(n):
        if app is not None:
            app.processEvents()


def _grab(widget, path: str, app) -> None:
    widget.show()
    _pump(app)
    pm = widget.grab()
    ok = pm.save(path)
    print(f"{'OK ' if ok else '失败'} {os.path.basename(path)}  {pm.width()}x{pm.height()}")


def _overlay_title() -> str:
    """和 app._overlay_title() 同一套规则：取服务商名 + 「 · 字幕生词」。"""
    import config
    import settings

    try:
        label = str(settings.provider_config().get("label") or "")
    except Exception:                              # noqa: BLE001
        return "字幕生词"
    short = label.split("（")[0].split("(")[0].strip()
    assert config is not None
    return f"{short} · 字幕生词" if short else "字幕生词"


def _render_overlay(px: int, path: str, *, with_followup: bool, app) -> None:
    import overlay
    import settings

    settings.set_overlay_font_size(px)
    cls = overlay._make_widget_class()              # noqa: SLF001 —— 渲染脚本，够用
    ov = cls(_overlay_title(), BODY, lambda: None, None)
    if with_followup:
        # 直接往追问块里塞一条"已回答"，等价于真机里 deliver_answer() 之后的样子
        ov._qa_blocks.append({"q": FOLLOWUP_Q, "a": FOLLOWUP_A,       # noqa: SLF001
                              "pending": False, "err": False})
        ov._render_body()                          # noqa: SLF001
    ov.resize(620, 760 if with_followup else 430)
    _grab(ov, path, app)
    ov.close()


def _render_prompt_editor(path: str, app) -> None:
    import prompt_edit

    class _StubCtrl:
        _prompt_editor = None

        def __getattr__(self, _name):
            return lambda *a, **k: None

    cls = prompt_edit.make_editor_class()
    win = cls(_StubCtrl())
    win.resize(760, 780)
    _grab(win, path, app)
    win.close()


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    os.makedirs(OUT_DIR, exist_ok=True)
    # ★ 必须留一个强引用：写成 `QApplication.instance() or QApplication(...)`
    #   的话，临时对象当场被 GC，后面所有 QWidget 都会报
    #   "Must construct a QApplication before a QWidget"。
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    globals()["_APP"] = app

    _render_overlay(15, os.path.join(OUT_DIR, "format_15.png"),
                    with_followup=True, app=app)
    _render_overlay(15, os.path.join(OUT_DIR, "format_only.png"),
                    with_followup=False, app=app)
    _render_prompt_editor(os.path.join(OUT_DIR, "prompt_edit.png"), app)

    path = os.path.join(OUT_DIR, "viewlog_file.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(VIEWLOG)
    print("OK  viewlog_file.md（观看记录样例文本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
