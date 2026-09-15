"""追问回答的渲染与处理策略 —— **完全离线**（Qt offscreen，不连网络）。

盯的是用户 2026-09-15 的这条反馈：
  「如果我继续去追问他一些问题的话，他直接就拒绝回答了。」
  ＋ 二次补充：「可以不用按照我刚才的模版回答我。」

这事分三层，任何一层漏掉都会重演"追问体验很差"：

  ① **提示词层**（config._FOLLOWUP_RULE）：声明纯文字追问不受字段模板约束。
     —— 由 tools/test_prompt_editable.py 守。
  ② **App 层**（app._finish_answer_ok）：自由格式的回答**不许**过
     `normalize_answer()` —— 那一步会给散文硬套字段名。
  ③ **渲染层**（overlay.format_prose vs format_lines）：散文不上彩色标签，
     模板式回答照旧上标签；撇号/&/< 不被 HTML 实体破坏；`**加粗**` 真的加粗。

本文件守 ② 和 ③。真机效果的视觉验收：tools/render_v122_preview.py。

跑法：
    QT_QPA_PLATFORM=offscreen python tools/test_followup_answer.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_followup_")
os.environ["DL_VIEWLOG_DIR"] = tempfile.mkdtemp(prefix="dl_test_followup_vl_")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


def check_in(name: str, needle: str, hay: str) -> None:
    if needle in hay:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: 在结果里找不到 {needle!r}\n      {hay[:300]!r}")
        FAILED.append(name)


def check_not_in(name: str, needle: str, hay: str) -> None:
    if needle not in hay:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: 不该出现 {needle!r}\n      {hay[:300]!r}")
        FAILED.append(name)


# 自由散文 —— 用户要的就是"它别照模板答"时那种回答
PROSE = (
    "能，但语气差一点，得看你想给老板什么感觉：\n"
    "\n"
    "1. get around to 的核心是“一直想做、拖到现在才做”，隐含**我自己拖延**。\n"
    "   对老板说 I'll get around to it，等于承认“我早该做了但没做”。\n"
    "2. 想表达“尽快处理”且不背拖延的锅，用下面这些更稳：\n"
    "   · I'll get right on it. —— 马上办，最贴合你的意思。\n"
    "   · I'll take care of it today. —— 给一个明确时间，老板最爱听。"
)

# 字段模板 —— 追问也能是"再给我一遍生词表"这种，这时该照旧上彩色标签
TEMPLATE = (
    "句子：You're gonna have to face the music.\n"
    "译文：你迟早得承担后果。\n"
    "生词：gonna /ˈɡɔːnə/ v. going to 的口语缩略"
)


def _plain(html: str) -> str:
    """把 HTML 喂给 QTextBrowser 取回**用户实际看到的文字**。"""
    from PyQt6.QtWidgets import QTextBrowser

    b = QTextBrowser()
    b.setHtml(html)
    return b.toPlainText()


def test_render_layer() -> None:
    import overlay

    print("== 1. 渲染层：散文不上标签，模板才上标签 ==")
    prose_html = overlay.format_prose(PROSE)
    text = _plain(prose_html)
    # 用户实际看到的内容 = 原文（星号/反引号这些标记不算内容，见下面单独测）
    check("散文：英文原样保留", "get around to" in text, True)
    check("散文：撇号没有被实体破坏", "I'll get right on it." in text, True)
    # ★ 核心：不许给它贴"译文/句子/俚语"这些字段标签
    check_not_in("散文：没有「译文」标签", "译文", prose_html)
    check_not_in("散文：没有「句子」标签", "句子", prose_html)
    check_not_in("散文：没有「俚语」标签", "俚语", prose_html)
    check_not_in("散文：不出现字面星号", "**", text)
    check_in("散文：加粗真的加粗了", "<b ", prose_html)
    check_in("散文：列表符号染了强调色", "font-weight:600", prose_html)
    check("散文：空行只算一次段间距", prose_html.count('height:10px'), 1)
    check("散文：末尾不留空段", prose_html.endswith("</div>"), True)
    # 行内代码与加粗：标记**不该**被当成内容显示出来
    check("散文：markdown 标记被消化成样式",
          _plain(overlay.format_prose("**重点** 与 `code`")).strip(), "重点 与 code")

    print("\n== 2. 渲染层：模板式回答照旧走彩色标签 ==")
    tmpl_html = overlay.format_lines(TEMPLATE)
    for label in ("句子", "译文", "生词"):
        check_in(f"模板：有「{label}」标签", label, tmpl_html)
    check_in("模板：生词的词头高亮加粗", "font-weight:700", tmpl_html)

    print("\n== 3. 渲染层：转义安全（内容永不丢失，标签永不被撕） ==")
    for raw in ("A & B", "3 < 5 > 1", "I'll get right on it.",
                "a &amp; b"):        # 模型自己写了实体也要原样显示，不能二次解码
        got = _plain(overlay.format_prose(raw))
        check(f"原样显示：{raw!r}", got.strip(), raw)
    check_not_in("不把 <b> 之类的标签当文本渲染",
                 "&lt;b style", _plain(overlay.format_prose("<b>伪标签</b>")))
    # 撇号必须靠"实体整段绕过强调正则"这条规则保住：
    # `&#x27;` 里的 x27 也是英文字母，正则若把它劈开，浮窗就原样显示 &#x27;。
    apostrophe = overlay.format_prose("I'll get right on it.")
    check_in("撇号实体没有被强调正则劈开", "&#x27;", apostrophe)
    check_not_in("  实体中间没插 span", "&#<span", apostrophe)

    print("\n== 4. 渲染层：空内容有兜底提示，不是白卡片 ==")
    check_in("空 → 提示语", "没有解析出内容", overlay.format_prose(""))
    check_in("空 → 提示语", "没有解析出内容", overlay.format_prose("   \n  "))


def test_app_layer() -> None:
    """② App 层：自由格式的追问回答不被 normalize_answer 改写。"""
    import app as app_mod
    from app import SHOWING

    app_mod.notify = lambda *a, **k: None

    class FakeRuntime:
        def __init__(self) -> None:
            self.answers: list[tuple[str, bool]] = []
            self.overlay = True

        def has_overlay(self) -> bool:
            return self.overlay

        def post_overlay_answer(self, text, error=False) -> None:
            self.answers.append((text, error))

        def typing_active(self) -> bool:
            return False

        def mark_auto_ui(self) -> None:
            pass

        def post_overlay_status(self, _t) -> None:
            pass

    def fresh():
        a = app_mod.App()
        a.runtime = FakeRuntime()
        a._mode_on = True
        a._state = SHOWING
        a._last_question = "get around to 能对老板这么说吗？"
        return a

    print("\n== 5. App 层：自由散文原样送到浮窗 ==")
    a = fresh()
    a._finish_answer_ok(PROSE)
    check("送了一条回答", len(a.runtime.answers), 1)
    body = a.runtime.answers[0][0]
    check("  不是错误", a.runtime.answers[0][1], False)
    check("★ 一个字都没改动（原样）", body, PROSE.strip())
    check_not_in("  没有被套上「译文：」（这正是修复点）", "译文：", body)
    check_not_in("  没有被套上「句子：」", "句子：", body)
    check("  列表符号还在", "· I'll get right on it" in body, True)
    check("状态回到 showing", a._state, SHOWING)

    print("\n== 6. App 层：字段模板仍然归一化（保持版式稳定）==")
    b = fresh()
    b._finish_answer_ok("1. 句子：You're gonna have to face the music.\n"
                        "2. 译文：你迟早得承担后果。")
    got = b.runtime.answers[0][0]
    check("编号被剥掉、字段名统一", got,
          "句子：You're gonna have to face the music.\n译文：你迟早得承担后果。")

    print("\n== 7. App 层：空回答有兜底，不会是一片空白 ==")
    c = fresh()
    c._finish_answer_ok("   ")
    check_in("空回答 → 兜底提示", "没有返回内容", c.runtime.answers[0][0])

    print("\n== 8. App 层：断网/报错走错误分支（不受本次改动影响）==")
    d = fresh()
    d._finish_answer_err(RuntimeError("连接超时"))
    text, is_err = d.runtime.answers[0]
    check("标记为错误", is_err, True)
    check_in("错误信息在前面", "连接超时", text)
    check("状态回到 showing（浮窗还在）", d._state, SHOWING)

    print("\n== 9. 观看记录：追问那一条带上了「我问了什么」 ==")
    import viewlog

    e = fresh()
    e._last_question = "face the music 是什么语气？"
    e._finish_answer_ok(PROSE)
    viewlog.flush(timeout=5)
    path = viewlog.current_path()
    if path is None:
        check("观看记录文件已创建", False, True)
    else:
        txt = Path(path).read_text(encoding="utf-8")
        check_in("记录里有追问的问题", "face the music 是什么语气？", txt)
        check_in("记录里有回答原文", "get around to 的核心是", txt)
        check("记录里没有被改写成字段模板", "译文：能，但语气差一点" in txt, False)


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    assert app is not None
    globals()["_APP"] = app
    import config

    print(f"设置文件（临时）: {config.SETTINGS_FILE}")
    print(f"观看记录目录（临时）: {config.VIEWLOG_DIR}\n")

    test_render_layer()
    print()
    test_app_layer()
    print()

    print("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
