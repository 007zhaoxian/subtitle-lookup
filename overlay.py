"""极简悬浮窗（PyQt6）。

关键点：
* Frameless + WindowStaysOnTopHint + Tool + WA_ShowWithoutActivating
  —— 显示时不抢视频播放器的焦点。
* ★★【v1.2.1 修掉的老毛病：窗口标志**绝不能动态改**】
  以前「默认不抢焦点 / 点一下要能打字」是靠
  `setWindowFlag(WindowDoesNotAcceptFocus, True/False)` 在两种状态间切。
  实测两个后果（用户都报过）：
    ① **点进浮窗会闪一下** —— Qt 在可见状态下改 flag 会先把窗口
       `hide()` 掉（`isVisible()` 直接变 False），代码里紧接着再 `show()`，
       屏幕上就是"浮窗消失一下又回来"。这就是用户说的"光标点进悬浮框会闪"。
    ② 同一次 setWindowFlag 还会让原生窗口重建，把 pyobjc 设好的
       `level = NSStatusWindowLevel` / collectionBehavior 全部丢掉，浮窗
       掉到普通层级、可能被前台窗口盖住（见 tools/probe_window_layer.py）。
  现在**根本不带这个 flag、也永远不动它**：
    · 弹出时不抢焦点 —— 靠 `WA_ShowWithoutActivating` +
      `_bring_front_without_activating()`（这两条才是真正起作用的东西）；
    · 要打字时 —— 只是激活 App（`_activate_self_window`）+ 给输入框
      `setFocus`，QNSPanel 自然会成为 key window（窗口本来就能当 key）。
  为什么这样做是安全的：窗口能不能成为 key 只决定"键盘事件收不收"，
  **能不能抢到前台取决于 App 有没有被激活**，后者由上面两条保证；
  实测 `canBecomeKeyWindow` 在带/不带这个 flag 时分别是 False / True，
  而"弹出后前台是谁"两种情况完全一样（都不会变成我们）。
* ★【踩过的大坑，别再改回去】**光有上面那些标志是不够的**：
  `QWidget.raise_()` 在 macOS 上会**顺带把整个应用激活**，一旦激活，
  真实的空格键就投递给我们的窗口，播放器再也收不到 —— 用户表现为
  「弹窗后顶部应用变成这个软件，空格失灵，必须先用鼠标点一下播放器」。
  实测（tools/probe_focus_steal.py，逐个变体量的）：
      show()                          → 前台不变 ✅
      show() + raise_()               → 前台变成我们 ❌
      show() + orderFrontRegardless() → 前台不变 ✅
  所以顶层窗口一律走 `_bring_front_without_activating()`（底层是
  NSWindow.orderFrontRegardless），**绝不调用 raise_()**。
  顺带记一笔：给 NSPanel 加 NSWindowStyleMaskNonactivatingPanel 对这件事
  **完全没用**（实测无效），别再往那个方向试。
  （窗口内部子控件的 raise_() 是安全的 —— 那只调层级，不碰应用激活。）
* NSVisualEffectView 毛玻璃（pyobjc 存在时自动挂，失败则退化为半透明卡片）；
* 所有 Qt 调用都收拢在**同一个线程**里，通过命令队列 + QTimer 泵入，
  避免 Qt/菜单栏线程冲突。
"""
from __future__ import annotations

import html as html_mod
import os
import queue
import re
import threading
import time
from typing import Callable

import config
from utils import log

# 主题色：把背景色拉亮一点做层叠卡片色
_r, _g, _b = config.OVERLAY_BG
CARD_BG = f"rgba({_r}, {_g}, {_b}, {config.OVERLAY_OPACITY})"
BORDER = "rgba(255, 255, 255, 0.10)"


def body_font_px() -> int:
    """浮窗正文字号（px）—— 用户在设置面板「悬浮框文字大小」里调的那个值。

    读 settings，读不到就退回 config 里的默认值。**每次渲染都重新读**，
    这样用户在面板上拖动滑块、浮窗里已有的内容立刻就能变。
    """
    try:
        import settings

        return int(settings.get_overlay_font_size())
    except Exception:
        return int(config.OVERLAY_FONT_SIZE)


def ui_sizes(base: int | None = None) -> dict:
    """由正文字号推导出的整套字号。

    为什么不各自写死：用户调的是"文字大小"，如果标题/提示不跟着走，
    调大之后正文顶天、周边还是小字，看起来像坏了。这里统一按正文推导。

    ★ 但**都要封顶**：
      · 顶部那行 chrome 只在 `HEAD_H = 78px` 里排得下，标题放到 30px 会挤爆；
      · 输入框高度写死 28px，字号跟着涨会把字裁掉 —— 所以它固定 13px。
      封顶值 = "大字模式下看着仍然协调"的最大值，不是随便拍的。

    `base` 显式传入时就用它（设置面板的实时预览要"还没落盘的字号"）。
    """
    if base is None:
        base = body_font_px()
    base = int(base)
    return {
        "body": base,
        "chip": max(9, base - 4),               # 「生词」这类小标签
        "hint": max(10, min(base - 4, 14)),     # 顶部操作提示
        "title": max(11, min(base - 3, 20)),    # 卡片标题
        "input": 13,                            # 输入框：**固定**，见上
        "smallbtn": max(10, min(base - 3, 14)),  # 关闭按钮 / 发送按钮
    }


def _chip(text: str, fg: str, bg: str, px: int) -> str:
    """行首那个小标签（「生词」「俚语」…）—— 醒目靠它。"""
    return (f'<span style="color:{fg};background-color:{bg};'
            f'font-size:{max(9, px - 4)}px;font-weight:600;'
            f'padding:1px 6px;">{html_mod.escape(text)}</span>')


def format_lines(body: str, sizes: dict | None = None) -> str:
    """把结果文本渲染成卡片里的 HTML。

    ★ v1.2.1：不再"看到 —— 就加粗"那么随意，而是先走
      `reply_clean.parse_entries()` 把内容解析成结构化条目，
      再按**固定版式**渲染 —— 这样不管模型这次用的是
      「生词：」还是「单词 —— 」，屏幕上都是同一副样子、
      同一套配色、同一个行距（用户要求"格式非常稳定 + 易读醒目"）。
    """
    from reply_clean import parse_entries

    s = sizes or ui_sizes()
    base = s["body"]
    parts: list[str] = []

    # 词条整体缩进一点，和句子/译文区分开
    _KIND = {
        "sentence": ("句子", config.OVERLAY_ACCENT, "rgba(124,196,255,0.16)"),
        "translation": ("译文", "#B7E3C0", "rgba(160,220,175,0.14)"),
        "word": ("生词", config.OVERLAY_ACCENT, "rgba(124,196,255,0.16)"),
        "idiom": ("俚语", "#FFC98B", "rgba(255,201,139,0.16)"),
        # 语法/结构说明：用紫色区分，免得和"译文"（同样是整行中文）混在一起
        "structure": ("结构", "#C8A8FF", "rgba(200,168,255,0.16)"),
    }

    entries = parse_entries(body or "")
    if not entries:
        # 空内容也要有反馈，别给一张白卡片
        return (f'<div style="color:rgba(234,236,239,0.45);font-size:{base}px">'
                "（这次没有解析出内容）</div>")

    for kind, head, tail in entries:
        if kind == "other":
            # 认不出来的整行原样显示 —— 宁可不好看，也不许丢内容
            parts.append(
                f'<div style="margin:4px 0;font-size:{base}px">'
                f'{html_mod.escape(tail)}</div>')
            continue

        label, fg, bg = _KIND.get(kind, ("", config.OVERLAY_FG, "transparent"))
        chip = _chip(label, fg, bg, base)

        if kind == "sentence":
            # 原句最要紧：给最大的字 + 加粗 + 稍微亮一点
            content = (f'<span style="font-size:{base + 1}px;font-weight:600;'
                       f'color:#F2F4F7">{html_mod.escape(tail)}</span>')
        elif kind == "translation":
            content = (f'<span style="color:rgba(234,236,239,0.95)">'
                       f'{html_mod.escape(tail)}</span>')
        elif kind == "structure":
            # 语法说明整段是"讲道理"的文字，压暗一点，别抢生词的视觉重心
            content = (f'<span style="color:rgba(226,220,255,0.82)">'
                       f'{html_mod.escape(tail)}</span>')
        elif kind == "word":
            t = html_mod.escape(tail)
            # 音标（/.../）压暗，读起来更清爽
            t = re.sub(r"(/[^/\s][^/]{0,40}/)",
                       r'<span style="color:rgba(234,236,239,0.55)">\1</span>', t)
            content = (f'<span style="color:{fg};font-weight:700">'
                       f'{html_mod.escape(head)}</span>'
                       + (f' <span>{t}</span>' if t else ""))
        else:  # idiom
            if head:
                content = (f'<span style="color:{fg};font-weight:600">'
                           f'{html_mod.escape(head)}</span>'
                           f'<span style="color:rgba(255,255,255,0.45)"> → </span>'
                           f'<span>{html_mod.escape(tail)}</span>')
            else:
                # 切不出"表达 → 含义"（模型没按规范写）时，
                # 绝不能只画一个孤零零的箭头 —— 那比不好看更糟：看着像内容丢了。
                content = f'<span>{html_mod.escape(tail)}</span>'

        parts.append(
            f'<div style="margin:6px 0;font-size:{base}px">'
            f'{chip}&nbsp;&nbsp;{content}</div>')

    return "".join(parts)


# 自由格式回答（追问）里要处理的轻量 markdown。只做最保守的两件：
#   **加粗** → <b>、`代码` → 高亮底；
# 其余标记一律**原样显示**，宁可看到星号也不许把用户的内容吃掉。
#
# ★ 必须**先分词、再逐段处理**（见 _prose_inline）：早期写法是"先替换成 HTML、
#   再在整串上跑一遍英文强调正则"，结果正则匹配到了刚插进去的标签本身，
#   把 `<b style=...>` 撕成可见乱码（实测渲染出来正是
#   `隐含b style="color:#F2F4F7">我自己拖延b>` 这种鬼样子）。
_MD_TOKEN = re.compile(r"\*\*(.+?)\*\*|`([^`\n]+)`")
# 行内英文：给一点"比正文亮一档"的强调，方便扫读（不改变字号、不加底）
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,4}")
# HTML 实体（`&#x27;` / `&amp;` / `&nbsp;`…）。★ 见 _emph_latin —— 强调必须绕开它。
_HTML_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
# 列表行：`1.` `-` `·` `•` 开头 —— 缩进保留，行首符号加个色
_LIST_HEAD = re.compile(r"^(\s*)(\d+[.、)]|[-*·•])\s+")


def _emph_latin(escaped: str) -> str:
    """给**已经 escape 过**的纯文本加"英文更亮一档"的强调。

    两个必须遵守的前置条件（各踩过一次坑）：

    ① **只能喂纯文本，且必须逐段调用**。本函数会往串里插 <span>；
       如果在"已经插过 HTML"的整串上再跑一遍正则，`_LATIN_RUN` 会匹配到
       刚插进去的标签本身（`<b style=...` 里的 b、style…），把标签撕成
       可见乱码（实测渲染出 `隐含b style="color:#F2F4F7">我自己拖延b>` 这种鬼样子）。

    ② **实体要整段绕开**。`html.escape` 会把 `I'll` 变成 `I&#x27;ll`，
       而 `_LATIN_RUN` 眼里 `x27` 也是一串英文字母 —— 直接 sub 会把实体
       从中间劈开：`&#<span>x</span>27;`，于是浮窗里**原样显示** `I&#x27;ll`。
       所以这里先把实体逐个"抄"出来，只对实体之间的普通文本做强调。
    """
    out: list[str] = []
    pos = 0
    for m in _HTML_ENTITY.finditer(escaped):
        out.append(_LATIN_RUN.sub(
            r'<span style="color:#F2F4F7">\g<0></span>', escaped[pos:m.start()]))
        out.append(m.group(0))                # 实体原样穿过，绝不拆开
        pos = m.end()
    out.append(_LATIN_RUN.sub(
        r'<span style="color:#F2F4F7">\g<0></span>', escaped[pos:]))
    return "".join(out)


def _prose_inline(chunk: str) -> str:
    """一行自由文本 → HTML：分词处理 markdown，再逐段加英文强调。"""
    out: list[str] = []
    pos = 0
    for m in _MD_TOKEN.finditer(chunk):
        out.append(_emph_latin(html_mod.escape(chunk[pos:m.start()])))
        if m.group(1) is not None:                  # **加粗**
            out.append('<b style="color:#F2F4F7">'
                       + _emph_latin(html_mod.escape(m.group(1))) + "</b>")
        else:                                       # `行内代码`
            out.append('<span style="background:rgba(255,255,255,0.10);'
                       "padding:0 3px;border-radius:3px\">"
                       + html_mod.escape(m.group(2)) + "</span>")
        pos = m.end()
    out.append(_emph_latin(html_mod.escape(chunk[pos:])))
    return "".join(out)


def format_prose(body: str, sizes: dict | None = None) -> str:
    """自由格式回答（追问）的渲染 —— **不套字段模板、不上彩色标签**。

    为什么要单独一条渲染路径：追问**允许**不照模板答（用户明确要求）。
    这时若还按字段名配标签，一段正常解释会被贴上「译文」「句子」「俚语」，
    又错又难看 —— 实测过：

        能，但语气差一点，得看你想给老板什么感觉：   ← 被贴上「译文」
        get around to 的核心是"一直想做…"            ← 被贴上「句子」
        I'll get right on it. —— 马上办              ← 被贴上「俚语」

    所以这里按"自然段落"渲染：
      · 空行 = 段间距，换行 = 逐行 <div>（保留用户看到的换行）
      · 行首的 `1.` / `·` / `-` 缩进留着，符号染成强调色
      · `**加粗**` 真的加粗、反引号变成浅底
      · 行内英文比正文亮一档（这程序是拿来学英语的，扫读很重要）
      · 其余一律 html 转义后原样输出 —— **内容永不丢失**
    """
    s = sizes or ui_sizes()
    base = s["body"]
    text = (body or "").strip()
    if not text:
        return (f'<div style="color:rgba(234,236,239,0.45);font-size:{base}px">'
                "（这次没有解析出内容）</div>")

    out: list[str] = []
    gap = '<div style="height:10px"></div>'
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            # 空行 → 段间距（连续空行只算一次，免得卡片被撑开）
            if out and out[-1] != gap:
                out.append(gap)
            continue
        m = _LIST_HEAD.match(line)
        if m:
            indent = min(len(m.group(1)) // 2, 6) * 14
            bullet = m.group(2)
            rest = line[m.end():]
            out.append(
                f'<div style="margin:3px 0 3px {indent}px;font-size:{base}px">'
                f'<span style="color:{config.OVERLAY_ACCENT};font-weight:600">'
                f'{html_mod.escape(bullet)}</span>&nbsp;&nbsp;'
                f"{_prose_inline(rest)}</div>")
        else:
            out.append(f'<div style="margin:3px 0;font-size:{base}px">'
                       f"{_prose_inline(line.strip())}</div>")
    while out and out[-1] == gap:
        out.pop()
    return "".join(out)


def build_html(title: str, body: str, sizes: dict | None = None) -> str:
    """把纯文本结果包成小卡片用的完整 HTML。

    `sizes` 可显式传入 —— 设置面板的"示例"就是靠它在字号**还没落盘**时
    也能渲染出正确的预览。
    """
    s = sizes or ui_sizes()
    return (
        f'<div style="color:{config.OVERLAY_FG};font-size:{s["body"]}px;'
        f'line-height:150%;font-family:\'PingFang SC\',\'Helvetica Neue\',sans-serif">'
        + format_lines(body, s)
        + "</div>"
    )


# ======================================================================
# 焦点归还 —— 「关掉浮窗后按空格视频不播放」的解药
# ======================================================================
class SharedUiState:
    """Qt 线程写、监听线程读的几个小状态。

    为什么单独一个对象：pynput 的监听跑在**另一个线程**，它需要知道
    「用户是不是正在浮窗里打字」—— 正在打字时，空格是**打空格**，不能拿它
    去关浮窗；触发键 N 也是**打字母 n**，不能触发查词。
    用一个对象 + 锁把这两个判断收在一起，避免散落的全局变量。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._typing = False
        self._input_exited_at = 0.0
        # 浮窗当前占的屏幕矩形 (x, y, w, h)。
        # 「点击浮窗外面就关闭」的判定要用它；鼠标监听在**另一个线程**里读，
        # Qt 的 widget 不能跨线程碰，所以由 Qt 线程写进来、监听线程只读这个元组。
        self._rect: tuple | None = None
        # 输入框里的草稿文字（跨线程读，用来判断"点了外面会不会丢掉没发出去的话"）
        self._draft = ""

    def set_typing(self, on: bool) -> None:
        with self._lock:
            on = bool(on)
            if self._typing and not on:
                # 记下"刚退出输入态"的时刻：Esc 有可能被输入框先吃掉一次，
                # 如果此时 pynput 那边也收到同一个 Esc，就会把整个浮窗关掉。
                # 用这个时间戳把重复的那次挡掉（见 esc_was_consumed）。
                self._input_exited_at = time.time()
            self._typing = on

    def typing(self) -> bool:
        with self._lock:
            return self._typing

    def esc_was_consumed(self, window: float = 0.8) -> bool:
        with self._lock:
            return (time.time() - self._input_exited_at) < window

    def mark_input_exited(self) -> None:
        with self._lock:
            self._input_exited_at = time.time()

    # ---------------------------------------------------------- 浮窗几何
    def set_overlay_rect(self, rect) -> None:
        with self._lock:
            self._rect = tuple(rect) if rect and len(rect) == 4 else None

    def overlay_rect(self):
        with self._lock:
            return self._rect

    def set_draft(self, text: str) -> None:
        with self._lock:
            self._draft = text or ""

    def draft(self) -> str:
        with self._lock:
            return self._draft


def _frontmost_app():
    """当前最前面的 App（用来记住"看剧的那个播放器"）。"""
    try:
        from AppKit import NSWorkspace

        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception as exc:
        log.debug("读取最前面的 App 失败: %s", exc)
        return None


def _app_is_self(app) -> bool:
    try:
        return bool(app) and app.bundleIdentifier() == config.BUNDLE_ID
    except Exception:
        return False


def _activate_app(app) -> bool:
    """把焦点还给某个 App。"""
    try:
        # NSApplicationActivateIgnoringOtherApps = 1 << 1
        return bool(app.activateWithOptions_(1 << 1))
    except Exception as exc:
        log.debug("激活目标 App 失败: %s", exc)
        return False


def _return_focus_to_player(prev_app) -> None:
    """把键盘焦点还给"上一个前台 App"（也就是你的播放器）。

    【为什么必须做这件事】用户反馈：「关掉浮窗后再按空格，视频也不会播放」。
    根因是焦点：只要浮窗（= 我们的 App）当过 key window / 前台 App，
    空格就会被我们这边收走，播放器再也收不到 —— 于是空格看起来"失灵"了。
    浮窗默认是不抢焦点的（WA_ShowWithoutActivating +
    _bring_front_without_activating），
    只有用户**点击浮窗输入框**时才会真的抢焦点；所以每次结束输入、关闭浮窗，
    都要主动把这个焦点还回去。还回去之后空格立刻恢复由播放器控制。

    只在「确实是我们抢了焦点」时才动手，避免打扰用户正在用的其它 App。
    """
    if prev_app is None:
        return
    if _app_is_self(prev_app):
        return
    cur = _frontmost_app()
    if cur is not None and not _app_is_self(cur):
        # 前台已经是别的 App 了（用户自己切走了），不要抢
        return
    if _activate_app(prev_app):
        log.info("已把键盘焦点还给上一个 App：%s",
                 getattr(prev_app, "localizedName", lambda: "?")())
    else:
        log.debug("焦点归还失败（不影响浮窗本身）")


def _activate_self_window(widget) -> bool:
    """把本 App 拉到前台、并让这个窗口成为 key window（只有用户点了输入框才调）。

    【顺序很关键，别改回去】
    必须**先**把 App 变成 active，再让窗口成为 key window。
    反过来的话，在 App 还没激活时调 activateWindow() 会被系统直接忽略
    （macOS 不会让一个后台 App 的窗口抢到键盘），于是输入框永远收不到字。

    【NSPanel 的坑】
    Qt 的 Tool 窗口在 macOS 上对应 **NSPanel**，而 NSPanel 的
    becomesKeyOnlyIfNeeded 默认是 **YES** —— 意味着它只在系统认为"需要"时
    才肯当 key window，我们的输入框可能被判定为"不需要"。
    所以这里显式关掉它，保证用户一点击就能真的打字。
    """
    ok = False
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        ok = True
    except Exception as exc:
        log.debug("NSApp 激活失败: %s", exc)

    # 【防段错误】winId() 只有在真·Cocoa 平台下才是个 NSView 指针。
    # offscreen / minimal / 部分远程桌面场景里它是假的，交给 pyobjc 会直接
    # SIGSEGV，Python 的 try/except **拦不住**。碰原生对象前必须先查平台。
    if not _can_use_appkit():
        try:
            widget.raise_()
            widget.activateWindow()
        except Exception:
            pass
        return ok
    try:
        import objc
        from ctypes import c_void_p

        win = objc.objc_object(c_void_p=int(widget.winId())).window()
        if win is not None:
            try:
                win.setBecomesKeyOnlyIfNeeded_(False)   # NSPanel 默认 YES，必须关
            except Exception:
                pass
            try:
                win.makeKeyAndOrderFront_(None)
            except Exception:
                pass
    except Exception as exc:
        log.debug("NSWindow 置为 key 失败: %s", exc)

    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.setActiveWindow(widget)
    except Exception:
        pass
    try:
        widget.raise_()
        widget.activateWindow()
    except Exception as exc:
        log.debug("Qt 侧激活窗口失败: %s", exc)
    return ok



def pick_screen(x: int, y: int, screens: list, old):
    """挑目标屏幕。screens = [(x, y, w, h), ...]，**约定主屏排在最前**。

    优先级：① 包含这个点的屏幕 → ② 第一块（= 主屏）。

    【踩过的坑，别改回去】
    这里曾经有第二级「和原来那块一样大的屏幕」。想法是"换分辨率也能对上"，
    但多显示器时**严重误判**：只要另一台显示器分辨率相同（很常见），浮窗就会
    被摆到那台显示器上去。实测抓到过浮窗落在 x=-680 的左边屏 —— 主屏上完全
    看不到，用户以为「按了没反应」。位置不完美可以忍，跑到看不见的屏幕上不行。
    """
    for s in screens:
        if s[0] <= x <= s[0] + s[2] and s[1] <= y <= s[1] + s[3]:
            return s
    _ = old          # 保留形参（调用方仍在传），但不再用它猜屏幕
    return screens[0] if screens else None


def fit_geometry(geo: dict, w: int, h: int, screens: list):
    """把保存的几何换算成**当前屏幕**上的实际 (x, y)；没有记录位置就返回 None。

    两件事（都是被真实场景逼出来的）：
    · 换分辨率 / 插拔外接屏：按「相对屏幕左上角的比例」平移，
      硬用旧绝对坐标会把浮窗甩到屏幕外，用户就再也看不到它了；
    · 无论如何最后都夹紧到屏幕可见区域 —— 位置宁可差一点，绝不能丢。
    """
    if not geo:
        return None
    x, y = geo.get("x"), geo.get("y")
    if x is None or y is None:
        return None
    s = pick_screen(x, y, screens, geo.get("screen"))
    if s is None:
        return None
    sx, sy, sw, sh = s
    old = geo.get("screen")
    if (isinstance(old, dict) and old.get("w") and old.get("h")
            and (old["w"], old["h"]) != (sw, sh)):
        x = sx + int((x - old.get("x", 0)) * sw / max(1, old["w"]))
        y = sy + int((y - old.get("y", 0)) * sh / max(1, old["h"]))
    x = min(max(int(x), sx), max(sx, sx + sw - w))
    y = min(max(int(y), sy), max(sy, sy + sh - h))
    return int(x), int(y)


class Overlay:  # 真正的 QWidget 在 _build 里动态继承，避免无 Qt 环境下 import 报错
    pass


def _make_widget_class():
    from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer
    from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QTextDocument
    from PyQt6.QtWidgets import (
        QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
        QVBoxLayout, QWidget,
    )

    GRIP = config.OVERLAY_GRIP_PX
    CORNER = config.OVERLAY_GRIP_CORNER
    # 头部（标题行 + 卡片上下内边距）占的高度。正文高度 = 窗高 - 它 - 输入行高。
    HEAD_H = 78
    INPUT_H = config.OVERLAY_INPUT_H if config.OVERLAY_INPUT_ENABLED else 0

    def HINT_QSS() -> str:                       # noqa: N802 —— 沿用常量名的调用点
        """顶部那行提示的字号也跟着正文走。

        做成函数而不是常量：常量会在 `make_overlay_class()` 执行时（App 启动
        那一次）就把字号定死，之后用户在面板里改字号就不会生效了。
        """
        return ("color:rgba(255,255,255,0.28);"
                f"font-size:{ui_sizes()['hint']}px;"
                "font-family:'PingFang SC',sans-serif;")
    STATUS_QSS = ("color:rgba(124,196,255,0.92);font-size:11px;font-weight:600;"
                  "font-family:'PingFang SC',sans-serif;")
    # kind -> 光标形状：边=单向，角=斜向
    _CURSORS = {
        "n": Qt.CursorShape.SizeVerCursor,
        "s": Qt.CursorShape.SizeVerCursor,
        "w": Qt.CursorShape.SizeHorCursor,
        "e": Qt.CursorShape.SizeHorCursor,
        "nw": Qt.CursorShape.SizeFDiagCursor,
        "se": Qt.CursorShape.SizeFDiagCursor,
        "ne": Qt.CursorShape.SizeBDiagCursor,
        "sw": Qt.CursorShape.SizeBDiagCursor,
    }

    class _Grip(QWidget):
        """贴在卡片边缘/四角的隐形缩放手柄。

        为什么要做成独立小控件而不是在 _Overlay 上算边缘区域：
        卡片(QFrame)和正文浏览器(QTextBrowser)都是子控件，子控件的鼠标事件
        会被它们自己吃掉（正文里还要能选中文字），父控件的 mousePressEvent
        根本收不到靠边的那一下。做成手柄最可靠 —— 而且能顺手给出正确的
        鼠标指针形状，用户一眼就知道能拖。
        """

        def __init__(self, parent, kind: str) -> None:
            super().__init__(parent)
            self.kind = kind
            self._start = None
            self.setCursor(_CURSORS[kind])
            self.setMouseTracking(True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            if kind == "se":
                self.setToolTip("拖动这里可以放大缩小")

        def paintEvent(self, _ev) -> None:  # 只画右下角那个可见手柄
            if self.kind != "se":
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(255, 255, 255, 58))
            pen.setWidthF(1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            w, h = self.width(), self.height()
            for off in (0, 4, 8):
                p.drawLine(w - 3 - off, h - 3, w - 3, h - 3 - off)
            p.end()

        def mousePressEvent(self, ev) -> None:
            if ev.button() == Qt.MouseButton.LeftButton:
                win = self.window()
                self._start = (ev.globalPosition().toPoint(),
                               win.pos(), win.size())
                ev.accept()
                return
            super().mousePressEvent(ev)

        def mouseMoveEvent(self, ev) -> None:
            if self._start is None or not (ev.buttons() & Qt.MouseButton.LeftButton):
                super().mouseMoveEvent(ev)
                return
            p0, pos0, size0 = self._start
            g = ev.globalPosition().toPoint()
            self.window().apply_resize(self.kind, pos0, size0,
                                       g.x() - p0.x(), g.y() - p0.y())
            ev.accept()

        def mouseReleaseEvent(self, ev) -> None:
            if self._start is not None:
                self._start = None
                self.window().finish_resize()
            ev.accept()

    class _Overlay(QWidget):
        def __init__(self, title: str, body: str, on_closed: Callable[[], None],
                     on_ask: Callable[[str], None] | None = None,
                     ui_state=None, prev_app=None) -> None:
            super().__init__()
            # ★ 记下题目和正文 —— 用户在设置面板里拖字号滑块时，
            #   要拿它们把当前这一屏**按新字号重画一遍**（见 restyle()）。
            self._title = title
            self._body = body
            self._on_closed = on_closed
            self._on_ask = on_ask
            self._ui_state = ui_state
            self._prev_app = prev_app
            self._closing = False
            self._drag_pos = None
            self._was_resized = False
            self._was_moved = False
            self._asking = False
            self._input_active = False
            self._qa_blocks: list[dict] = []
            self.edit = None
            self.btn_send = None

            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool                      # 不进 Dock / 不抢 Cmd-Tab
            )
            # ★★ 这里**故意不带** Qt.WindowType.WindowDoesNotAcceptFocus。
            #   带了它就必须在"点一下要打字"时把它摘掉，而**可见状态下改窗口
            #   flag 会让 Qt 先 hide 再重建原生窗口**：用户看到浮窗闪一下，
            #   而且 pyobjc 设好的窗口层级/collectionBehavior 会一起丢掉。
            #   "弹出不抢焦点"由 WA_ShowWithoutActivating +
            #   _bring_front_without_activating() 保证（见模块头注释）。
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setWindowTitle(config.APP_DISPLAY_NAME)
            if config.OVERLAY_RESIZABLE:
                self.setMinimumSize(config.OVERLAY_MIN_WIDTH, config.OVERLAY_MIN_HEIGHT)

            card = QFrame(self)
            card.setObjectName("card")
            card.setStyleSheet(
                f"#card {{ background-color: {CARD_BG}; border: 1px solid {BORDER};"
                f" border-radius: {config.OVERLAY_RADIUS}px; }}"
            )

            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            title_lbl = QLabel(title)
            # 标题跟着正文字号走（封顶 20px，见 ui_sizes）
            title_lbl.setStyleSheet(
                f"color:{config.OVERLAY_TITLE};font-size:{ui_sizes()['title']}px;"
                "font-family:'PingFang SC',sans-serif;"
            )
            # 两个键撞车时只说一次（否则会变成"按【空格】或【空格】关闭"）
            if config.CLOSE_KEY == config.TRIGGER_KEY:
                hint_text = (f"再按【{config.TRIGGER_LABEL}】或点外面关闭"
                             " · 点这里可提问")
            else:
                hint_text = (f"按【{config.CLOSE_KEY_LABEL}】或【{config.TRIGGER_LABEL}】关闭"
                             " · 点这里可提问")
            self._hint_text = hint_text
            self._status_text = ""
            self.lbl_hint = QLabel(hint_text)
            self.lbl_hint.setStyleSheet(HINT_QSS())
            hint = self.lbl_hint
            btn_close = QPushButton("✕")
            btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_close.setFixedSize(20, 20)
            btn_close.setStyleSheet(
                "QPushButton{color:rgba(255,255,255,0.45);background:transparent;border:none;"
                "font-size:13px}QPushButton:hover{color:#fff}"
            )
            btn_close.clicked.connect(self.close)
            head.addWidget(title_lbl)
            head.addStretch(1)
            head.addWidget(hint)
            head.addSpacing(6)
            head.addWidget(btn_close)

            body_html = build_html(title, body)
            self._base_html = body_html
            # 上次的几何（位置 + 尺寸）。读不到就用默认：默认宽度 + 底部居中。
            saved_geo: dict = {}
            if config.OVERLAY_RESIZABLE or config.OVERLAY_REMEMBER_GEOMETRY:
                try:
                    import settings

                    saved_geo = settings.get_overlay_geo() if config.OVERLAY_REMEMBER_GEOMETRY else {}
                    if not saved_geo and config.OVERLAY_RESIZABLE:
                        size = settings.get_overlay_size()
                        if size:
                            saved_geo = {"w": size[0], "h": size[1]}
                except Exception as exc:
                    log.debug("读取悬浮窗几何失败: %s", exc)
                    saved_geo = {}

            if saved_geo.get("w") and saved_geo.get("h"):
                win_w, win_h = int(saved_geo["w"]), int(saved_geo["h"])
                # 兜底：记下来的尺寸也可能偏小（老记录、手改过 settings.json）。
                # 正文高度算成 0 或负数会让 Qt 把窗口撑得乱七八糟，
                # 这里保证"起码放得下标题栏 + 输入行 + 一行正文"。
                win_h = max(win_h, HEAD_H + INPUT_H + 60)
                content_w, content_h = max(120, win_w - 40), win_h - HEAD_H - INPUT_H
            else:
                win_w = config.OVERLAY_WIDTH
                content_w = win_w - 40
                doc = QTextDocument()
                doc.setDefaultFont(QFont("PingFang SC", body_font_px()))
                doc.setHtml(body_html)
                doc.setTextWidth(content_w)
                max_content_h = config.OVERLAY_MAX_HEIGHT - HEAD_H - INPUT_H
                content_h = int(min(doc.size().height() + 4, max_content_h))
                win_h = content_h + HEAD_H + INPUT_H
            self._saved_geo = saved_geo

            self.browser = QTextBrowser()
            self.browser.setFrameShape(QFrame.Shape.NoFrame)
            self.browser.setOpenExternalLinks(True)
            self.browser.setFixedSize(content_w, content_h)
            self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.browser.setStyleSheet(
                "QTextBrowser{background:transparent;border:none;}"
                "QScrollBar:vertical{background:transparent;width:6px;margin:0}"
                "QScrollBar::handle:vertical{background:rgba(255,255,255,0.22);"
                "border-radius:3px;min-height:24px}"
                "QScrollBar::add-line,QScrollBar::sub-line{height:0}"
            )
            self.browser.setHtml(body_html)
            # 点正文（而不是拖选文字）也算「想提问」→ 用事件过滤器判点击/拖选
            self.browser.installEventFilter(self)

            box = QVBoxLayout(card)
            box.setContentsMargins(20, 14, 20, 16)
            box.setSpacing(8)
            box.addLayout(head)
            box.addWidget(self.browser)

            # ---------- 追问输入行（点一下浮窗就能打字继续问）----------
            if INPUT_H:
                self.edit = QLineEdit()
                self.edit.setPlaceholderText(config.OVERLAY_INPUT_PLACEHOLDER)
                self.edit.setFixedHeight(28)
                self.edit.setStyleSheet(
                    "QLineEdit{background:rgba(255,255,255,0.07);color:"
                    f"{config.OVERLAY_FG};border:1px solid rgba(255,255,255,0.12);"
                    "border-radius:8px;padding:2px 10px;font-size:13px;"
                    "font-family:'PingFang SC','Helvetica Neue',sans-serif;"
                    "selection-background-color:rgba(124,196,255,0.35)}"
                )
                self.edit.returnPressed.connect(self._on_send)
                self.edit.installEventFilter(self)
                # 草稿同步给共享状态：点浮窗外面时用它判断"会不会丢掉没发出去的话"
                self.edit.textChanged.connect(
                    lambda t: self._set_typing_draft(t))
                self.btn_send = QPushButton(config.OVERLAY_INPUT_BTN)
                self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
                self.btn_send.setFixedHeight(28)
                self.btn_send.setEnabled(False)
                self.btn_send.setStyleSheet(
                    "QPushButton{color:#fff;background:rgba(124,196,255,0.20);"
                    "border:1px solid rgba(124,196,255,0.45);border-radius:8px;"
                    "padding:0 14px;font-size:12px;"
                    "font-family:'PingFang SC',sans-serif}"
                    "QPushButton:hover{background:rgba(124,196,255,0.32)}"
                    "QPushButton:disabled{color:rgba(255,255,255,0.30);"
                    "background:rgba(255,255,255,0.05);"
                    "border:1px solid rgba(255,255,255,0.10)}"
                )
                self.btn_send.clicked.connect(self._on_send)
                self.edit.textChanged.connect(
                    lambda t: self.btn_send.setEnabled(bool(t.strip()))
                )
                irow = QHBoxLayout()
                irow.setSpacing(8)
                irow.addWidget(self.edit, 1)
                irow.addWidget(self.btn_send, 0)
                box.addLayout(irow)

            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.addWidget(card)

            # 缩放手柄（贴在卡片边缘/四角，必须在布局之后建，才能压在正文之上）
            self._grips: dict = {}
            if config.OVERLAY_RESIZABLE:
                for kind in ("n", "s", "w", "e", "nw", "ne", "sw", "se"):
                    self._grips[kind] = _Grip(card, kind)

            self.setGeometry(0, 0, win_w, win_h)   # 先定尺寸，摆位置时才知道自己多大
            self._relayout()
            self._restore_geometry(saved_geo)
            self._effect = None
            if config.OVERLAY_VIBRANCY:
                self._effect = _attach_vibrancy(self)
            if config.OVERLAY_AUTO_HIDE_SEC > 0:
                QTimer.singleShot(int(config.OVERLAY_AUTO_HIDE_SEC * 1000), self.close)

        # -------------------------------------------------- 布局
        def _relayout(self) -> None:
            """尺寸变了之后：正文跟着变（文字自动重排）+ 手柄重新贴边。"""
            w, h = max(self.width(), config.OVERLAY_MIN_WIDTH), max(
                self.height(), config.OVERLAY_MIN_HEIGHT
            )
            try:
                self.browser.setFixedSize(max(120, w - 40),
                                          max(40, h - HEAD_H - INPUT_H))
            except Exception as exc:
                log.debug("调整正文尺寸失败: %s", exc)
            if not self._grips:
                return
            g = self._grips
            g["n"].setGeometry(GRIP, 0, max(1, w - 2 * GRIP), GRIP)
            g["s"].setGeometry(GRIP, h - GRIP, max(1, w - 2 * GRIP), GRIP)
            g["w"].setGeometry(0, GRIP, GRIP, max(1, h - 2 * GRIP))
            g["e"].setGeometry(w - GRIP, GRIP, GRIP, max(1, h - 2 * GRIP))
            g["nw"].setGeometry(0, 0, CORNER, CORNER)
            g["ne"].setGeometry(w - CORNER, 0, CORNER, CORNER)
            g["sw"].setGeometry(0, h - CORNER, CORNER, CORNER)
            g["se"].setGeometry(w - CORNER, h - CORNER, CORNER, CORNER)
            for wdg in g.values():
                wdg.raise_()

        def resizeEvent(self, ev):  # noqa: N802
            super().resizeEvent(ev)
            self._relayout()
            # 尺寸变了 → 把新的屏幕矩形同步给共享状态（"点浮窗外面关闭"要用）
            self._report_geometry()

        # -------------------------------------------------- 缩放
        def _max_size(self) -> tuple[int, int]:
            screen = (QGuiApplication.screenAt(self.cursor().pos())
                      or QGuiApplication.primaryScreen())
            geo = screen.availableGeometry()
            return (max(config.OVERLAY_MIN_WIDTH, geo.width() - 40),
                    max(config.OVERLAY_MIN_HEIGHT, geo.height() - 40))

        def apply_resize(self, kind: str, pos0, size0, dx: int, dy: int) -> None:
            """按「被拖的那条边跟着鼠标走」的直觉改尺寸。"""
            max_w, max_h = self._max_size()
            min_w, min_h = config.OVERLAY_MIN_WIDTH, config.OVERLAY_MIN_HEIGHT
            w, h = size0.width(), size0.height()
            x, y = pos0.x(), pos0.y()

            if "e" in kind:
                w = max(min_w, min(max_w, size0.width() + dx))
            elif "w" in kind:
                w = max(min_w, min(max_w, size0.width() - dx))
                x = pos0.x() + size0.width() - w   # 右边缘不动，左边缘跟着走
            if "s" in kind:
                h = max(min_h, min(max_h, size0.height() + dy))
            elif "n" in kind:
                h = max(min_h, min(max_h, size0.height() - dy))
                y = pos0.y() + size0.height() - h

            self._was_resized = True
            self.setGeometry(int(x), int(y), int(w), int(h))

        # -------------------------------------------------- 尺寸记忆
        def finish_resize(self) -> None:
            """缩放松手：把**尺寸和位置一起**记下来，下次还这么大、还在这儿。

            为什么不再分两次写（曾经就是那样，也是"大小记不住"的病灶）：
            `set_overlay_size()` 和 `set_overlay_pos()` 各做一遍"读出来→改→写回去"，
            两次之间只要有一次没带上另一半，**另一半就被静默吞掉**——
            现象正是用户报的「位置记得住、大小记不住」。
            现在一次调用写完整条几何，不存在这个中间态。
            """
            if not self._was_resized:
                return
            self._was_resized = False        # 复位：不然之后随便点一下手柄都会重写一次
            self._save_geometry()
            log.info("悬浮窗尺寸已记住: %dx%d", self.width(), self.height())

        # -------------------------------------------------- 位置：记住 + 恢复
        def _current_screen_dict(self) -> dict:
            """当前浮窗所在屏幕的可用区域（存起来给下次换算位置用）。"""
            center = self.frameGeometry().center()
            screen = (QGuiApplication.screenAt(center)
                      or QGuiApplication.screenAt(self.cursor().pos())
                      or QGuiApplication.primaryScreen())
            if screen is None:
                return {}
            g = screen.availableGeometry()
            return {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

        @staticmethod
        def _screen_rects() -> list:
            """当前所有屏幕的可用区域（纯元组，方便跨平台/单测）。

            **主屏一定排在最前** —— fit_geometry / pick_screen 用它当"找不到
            坐标归属时的兜底屏"，顺序错了浮窗就可能跑到别的显示器上。
            """
            out = []
            primary = QGuiApplication.primaryScreen()
            ordered = []
            if primary is not None:
                ordered.append(primary)
            for sc in QGuiApplication.screens():
                if sc is not primary:
                    ordered.append(sc)
            for sc in ordered:
                try:
                    g = sc.availableGeometry()
                    out.append((g.x(), g.y(), g.width(), g.height()))
                except Exception:
                    continue
            return out

        def _restore_geometry(self, geo: dict) -> None:
            """把浮窗摆回**上次那个位置**（没记录过就底部居中）。

            真正的位置换算在模块级的 fit_geometry 里（纯函数，可单测）：
            换分辨率 / 插拔外接屏按比例平移，最后一定夹紧到可见区域 ——
            宁可位置不完美，也绝不让浮窗消失在屏幕外。
            """
            if not config.OVERLAY_REMEMBER_GEOMETRY:
                self._place()
                return
            pos = fit_geometry(geo or {}, self.width(), self.height(),
                               self._screen_rects())
            if pos is None:
                self._place()
                return
            self.move(QPoint(int(pos[0]), int(pos[1])))
            log.info("悬浮窗恢复到上次的位置: (%d, %d) %dx%d",
                     pos[0], pos[1], self.width(), self.height())

        def _save_geometry(self) -> None:
            """把「当前位置 + 当前尺寸」**一次性**落盘。

            拖动结束、缩放松手、关窗兜底，三处都走这一个出口 ——
            只有一个写入口，就不会出现"某一次只写了半条几何"的情况。
            """
            if not config.OVERLAY_REMEMBER_GEOMETRY:
                return
            try:
                import settings

                p = self.pos()
                settings.set_overlay_geometry(p.x(), p.y(),
                                              self.width(), self.height(),
                                              self._current_screen_dict())
                log.debug("悬浮窗几何已落盘: (%d, %d) %dx%d",
                          p.x(), p.y(), self.width(), self.height())
            except Exception as exc:
                log.debug("保存悬浮窗几何失败: %s", exc)

        def finish_move(self) -> None:
            """拖动结束：位置记下来（尺寸一并带上，绝不顺手丢掉），下次还出现在这儿。"""
            if not self._was_moved:
                return
            self._was_moved = False
            self._save_geometry()
            log.info("悬浮窗位置已记住: (%d, %d) %dx%d",
                     self.x(), self.y(), self.width(), self.height())

        # -------------------------------------------------- 位置：默认
        def _place(self) -> None:
            """默认位置：屏幕底部居中（没记住过位置时用它）。"""
            screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            w, h = self.width(), self.height()
            x = geo.x() + (geo.width() - w) // 2
            y = geo.y() + geo.height() - h - config.OVERLAY_MARGIN_BOTTOM
            self.move(QPoint(int(x), int(max(y, geo.y() + 20))))

        # 兼容旧名字
        def _resize_and_place(self) -> None:
            self._relayout()
            self._place()

        # -------------------------------------------------- 输入（追问）
        def input_active(self) -> bool:
            """浮窗当前是否处于"正在打字"状态（监听线程会读它）。"""
            return bool(self._input_active)

        def _set_typing(self, on: bool) -> None:
            if self._ui_state is not None:
                self._ui_state.set_typing(on)

        def _set_typing_draft(self, text: str) -> None:
            """输入框内容同步到共享状态（跨线程只读，用于"点外面"的判定）。"""
            if self._ui_state is not None:
                self._ui_state.set_draft(text)

        # -------------------------------------------------- 几何上报
        def _report_geometry(self) -> None:
            """把当前占的屏幕矩形写进共享状态。

            「点击浮窗外面即关闭」要用它；鼠标监听在另一个线程，不能直接碰
            QWidget（会崩），所以这里在 Qt 线程里把结果写成一个纯元组。
            """
            if self._ui_state is None:
                return
            try:
                g = self.frameGeometry()
                self._ui_state.set_overlay_rect((g.x(), g.y(), g.width(), g.height()))
            except Exception:
                pass

        def showEvent(self, ev):  # noqa: N802
            super().showEvent(ev)
            self._report_geometry()

        def moveEvent(self, ev):  # noqa: N802
            super().moveEvent(ev)
            self._report_geometry()
        # 注意：resizeEvent **不在这里重写** —— 上面已经有一个（要负责缩放手柄的
        # 重新布局），重复定义会把那个覆盖掉，导致拖动边角后手柄错位。
        # 几何上报已合并进那一个 resizeEvent 里。

        def set_status(self, text: str) -> None:
            """顶部那行小字：平时显示操作提示，追问时显示"AI 正在回答…"。"""
            self._status_text = text or ""
            lbl = getattr(self, "lbl_hint", None)
            if lbl is None:
                return
            if self._status_text:
                lbl.setText(self._status_text)
                lbl.setStyleSheet(STATUS_QSS)
            else:
                lbl.setText(self._hint_text)
                lbl.setStyleSheet(HINT_QSS())

        def enter_input(self) -> None:
            """点一下浮窗 → 激活输入框（**只有这时才抢键盘**）。

            抢键盘是有代价的：空格会被我们收走，播放器就暂停不了了。所以
            用户一发送 / 一按 Esc，就立刻 exit_input() 把键盘还回去。

            ★ v1.2.1：这里**不再动窗口标志**。
              以前是 `setWindowFlag(WindowDoesNotAcceptFocus, False)` + `show()`
              —— 而 Qt 在可见状态下改 flag 会先 hide 再重建原生窗口，
              用户看到的就是"点进浮窗闪一下"，顺带还丢掉窗口层级。
              现在只做两件不碰窗口结构的事：激活 App、把焦点给输入框。
              窗口能当 key window 是它本来就有的能力（我们建窗时就没禁）。
            """
            if self.edit is None:
                return
            if self._input_active:
                self.edit.setFocus(Qt.FocusReason.OtherFocusReason)
                return
            self._input_active = True
            self._set_typing(True)
            _activate_self_window(self)       # 先让 App 成为 active，再要键盘
            self.edit.setFocus(Qt.FocusReason.MouseFocusReason)
            log.info("浮窗进入输入状态（键盘暂归浮窗，空格此刻是打空格）")

        def exit_input(self) -> None:
            """退出输入状态 → **把键盘焦点还给播放器**。

            ★ 同样不碰窗口标志（见 enter_input）。焦点还回去靠
              `_return_focus_to_player()`，它才是"关窗后空格又能控制播放器"
              的关键；把窗口标志改回去从来不是必需品。
            """
            if not self._input_active:
                return
            self._input_active = False
            self._set_typing(False)
            if self.edit is not None:
                self.edit.clearFocus()
            _return_focus_to_player(self._prev_app)
            log.info("浮窗退出输入状态（键盘已还给播放器）")

        def _on_send(self) -> None:
            """用户按回车 / 点发送：把问题交给 App 去问 AI。"""
            if self.edit is None or self._asking:
                return
            text = self.edit.text().strip()
            if not text:
                return
            self.edit.clear()
            self._asking = True
            if self.btn_send is not None:
                self.btn_send.setEnabled(False)
            self._qa_blocks.append({"q": text, "a": "", "pending": True, "err": False})
            self._render_body()
            self.set_status("AI 正在回答…")
            # 先退出输入态：焦点归还播放器，用户在等待期间照样能用空格控制播放
            self.exit_input()
            if self._on_ask is not None:
                try:
                    self._on_ask(text)
                except Exception:
                    log.exception("追问回调异常")
                    self.deliver_answer("发起追问失败，请稍后重试。", error=True)

        def deliver_answer(self, text: str, error: bool = False) -> None:
            """AI 的回答到了（或失败了）：填进最后那个"回答中"的位置。"""
            self._asking = False
            if self.btn_send is not None:
                self.btn_send.setEnabled(False)
            blk = None
            for b in reversed(self._qa_blocks):
                if b.get("pending"):
                    blk = b
                    break
            if blk is None:
                blk = {"q": "", "a": "", "pending": False, "err": False}
                self._qa_blocks.append(blk)
            blk["pending"] = False
            blk["err"] = bool(error)
            blk["a"] = (text or "").strip() or "（这次没有返回内容）"
            self.set_status("追问失败，可再试一次" if error else "")
            self._render_body()

        def _qa_html(self) -> str:
            if not self._qa_blocks:
                return ""
            from reply_clean import is_structured

            out = []
            for b in self._qa_blocks:
                q = html_mod.escape(b.get("q") or "")
                if b.get("pending"):
                    body = ('<span style="color:rgba(234,236,239,0.45)">'
                            "AI 正在回答…</span>")
                else:
                    a = b.get("a") or ""
                    if b.get("err"):
                        body = (f'<span style="color:#ff8a8a">'
                                f"{html_mod.escape(a)}</span>")
                    elif is_structured(a):
                        # 它自己写成字段模板了 → 照样上彩色标签（用户喜欢这套版式）
                        body = format_lines(a)
                    else:
                        # ★ 自由格式（追问的常态）：**不上标签**，按自然段落排。
                        #   理由见 format_prose 的注释 —— 硬套标签会把
                        #   "get around to 的核心是…" 标成「句子」、把
                        #   "I'll get right on it." 标成「俚语」。
                        body = format_prose(a)
                out.append(
                    '<div style="margin-top:12px;border-top:1px solid rgba(255,255,255,0.10);'
                    'padding-top:9px">'
                    f'<div style="color:{config.OVERLAY_ACCENT};font-weight:600">💬 {q}</div>'
                    f'<div style="margin-top:4px">{body}</div>'
                    "</div>"
                )
            return "".join(out)

        def _render_body(self) -> None:
            try:
                self.browser.setHtml(self._base_html + self._qa_html())
                sb = self.browser.verticalScrollBar()
                sb.setValue(sb.maximum())      # 追问的回答在下面 → 自动滚到底
            except Exception as exc:
                log.debug("刷新浮窗正文失败: %s", exc)

        def restyle(self) -> None:
            """按**当前**字号把这一屏重画一遍（设置面板拖动滑块时调）。

            刻意不做的事：不重算窗口高度。因为用户可能已经手动拖过大小，
            重算会把人家摆好的尺寸冲掉。字号变大后内容超出就在框内滚动 ——
            这是能接受的小代价，比"改个字号窗口自己蹦一下"友好得多。
            高度自适应在**下一次**查词时自然生效。
            """
            try:
                self._base_html = build_html(self._title, self._body)
                self._render_body()
            except Exception as exc:
                log.debug("按新字号重画浮窗失败: %s", exc)

        # -------------------------------------------------- 事件
        def eventFilter(self, obj, ev):  # noqa: N802
            """三件事：

            ① **点输入框本身**要进输入态 —— 这条最要紧，也最容易漏。
               用户看到"点这里打字"，点的就是输入框；而 QLineEdit 会自己吃掉
               鼠标事件（不会冒泡到 mouseReleaseEvent），如果这里不管，
               点击就只剩一次 setFocus —— 而 App 还不是 active 时这个 focus
               落不实（macOS 不让后台 App 的窗口拿键盘），表现就是
               「点了没反应、也打不了字」。所以必须显式走 enter_input()。 
            ② 点正文（不是拖选）也算"想提问"；
            ③ 输入框里 Esc 退出输入。
            """
            try:
                t = ev.type()
                if obj is self.edit:
                    if t == QEvent.Type.MouseButtonPress:
                        self.enter_input()
                    elif t == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                        self.exit_input()
                        return True
                elif obj is self.browser:
                    if t == QEvent.Type.MouseButtonPress:
                        self._txt_press = ev.globalPosition().toPoint()
                        self._txt_moved = False
                    elif t == QEvent.Type.MouseMove and self._txt_press is not None:
                        if (ev.globalPosition().toPoint()
                                - self._txt_press).manhattanLength() > 4:
                            self._txt_moved = True
                    elif t == QEvent.Type.MouseButtonRelease:
                        moved = getattr(self, "_txt_moved", False)
                        self._txt_press = None
                        if not moved:
                            self.enter_input()
                            return True
            except Exception:
                pass
            return super().eventFilter(obj, ev)

        # -------------------------------------------------- 拖动 / 关闭
        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

        def mouseMoveEvent(self, ev):
            if self._drag_pos is not None and ev.buttons() & Qt.MouseButton.LeftButton:
                self.move(ev.globalPosition().toPoint() - self._drag_pos)
                self._was_moved = True

        def mouseReleaseEvent(self, ev):
            # 拖动过 → 记位置；只是"点了一下" → 用户想打字
            if self._was_moved:
                self.finish_move()
            elif ev.button() == Qt.MouseButton.LeftButton:
                self.enter_input()
            self._drag_pos = None

        def keyPressEvent(self, ev):  # 万一拿到了键盘事件（有些场景会）
            if ev.key() == Qt.Key.Key_Escape:
                if self._input_active:
                    self.exit_input()
                else:
                    self.close()
                return
            super().keyPressEvent(ev)

        def closeEvent(self, ev):
            # 兜底：记几何不能靠"那次松手事件一定送得到"。
            # 拖到窗口外松手、被系统吞掉事件、松手时刚好在关窗……都会让
            # finish_move/finish_resize 永远不触发，用户就白拖了一次。
            # 关窗前发现还是"脏"的，就补存一次（同一个原子写入口，重复写无害）。
            if self._was_resized or self._was_moved:
                self._was_resized = False
                self._was_moved = False
                self._save_geometry()
                log.info("关窗前补存悬浮窗几何（松手事件没到）")
            # 关窗之前先把键盘还给播放器 —— 不然「关掉浮窗后按空格没反应」
            if self._input_active:
                self.exit_input()
            elif self._ui_state is not None:
                self._ui_state.mark_input_exited()
            if not self._closing:
                self._closing = True
                try:
                    self._on_closed()
                except Exception:
                    log.exception("悬浮窗关闭回调异常")
            self._effect = None
            super().closeEvent(ev)

    return _Overlay


def _make_alert_class():
    """一个**可以接收点击**的提示窗（与悬浮窗不同：它需要用户操作）。

    用于首次启动引导、权限缺失提示、登录引导等小白场景 ——
    比命令行提示友好得多，也避免依赖 osascript。
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import (
        QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
    )

    class _Alert(QWidget):
        def __init__(self, title: str, body: str, actions, on_closed) -> None:
            super().__init__()
            self._on_closed = on_closed
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowTitle(title)

            card = QFrame(self)
            card.setObjectName("card")
            card.setStyleSheet(
                f"#card {{ background-color: {CARD_BG}; border: 1px solid {BORDER};"
                f" border-radius: {config.OVERLAY_RADIUS}px; }}"
            )

            lbl_title = QLabel(title)
            lbl_title.setStyleSheet(
                f"color:{config.OVERLAY_FG};font-size:16px;font-weight:600;"
                "font-family:'PingFang SC',sans-serif;"
            )
            lbl_body = QLabel(body)
            lbl_body.setWordWrap(True)
            lbl_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl_body.setStyleSheet(
                f"color:rgba(234,236,239,0.86);font-size:{config.OVERLAY_FONT_SIZE}px;"
                "line-height:150%;font-family:'PingFang SC',sans-serif;"
            )
            lbl_body.setFixedWidth(460)
            lbl_body.setMinimumHeight(40)

            row = QHBoxLayout()
            row.addStretch(1)
            for label, cb in actions:
                btn = QPushButton(label)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    "QPushButton{color:#fff;background:rgba(124,196,255,0.22);"
                    "border:1px solid rgba(124,196,255,0.45);border-radius:8px;"
                    "padding:7px 16px;font-size:13px;font-family:'PingFang SC',sans-serif}"
                    "QPushButton:hover{background:rgba(124,196,255,0.34)}"
                )
                btn.clicked.connect(lambda _=False, f=cb: (self.close(), f and f()))
                row.addWidget(btn)

            box = QVBoxLayout(card)
            box.setContentsMargins(24, 20, 24, 18)
            box.setSpacing(12)
            box.addWidget(lbl_title)
            box.addWidget(lbl_body)
            box.addLayout(row)

            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.addWidget(card)

            self.adjustSize()
            screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry()
            self.move(int(geo.x() + (geo.width() - self.width()) / 2),
                      int(geo.y() + (geo.height() - self.height()) / 3))

        def keyPressEvent(self, ev):
            if ev.key() == Qt.Key.Key_Escape:
                self.close()
            super().keyPressEvent(ev)

        def closeEvent(self, ev):
            try:
                self._on_closed()
            except Exception:
                pass
            super().closeEvent(ev)

    return _Alert


def _attach_vibrancy(widget):
    """给无边框窗口挂一层 NSVisualEffectView（真·毛玻璃）。失败就返回 None。

    ⚠️ 危险区（已实测踩坑，别再往这个方向改）：
    macOS 上往 Qt 的窗口里插 NSVisualEffectView，**文字会整体消失**，
    屏幕上只剩一个空的圆角框（Qt 自己 grab() 出来是有字的，所以只看截图
    工具很容易误判成"渲染正常"）。三种挂法全部实测为空白：
      · 作为 contentView 的子视图（子视图永远画在父视图 drawRect 之上）
      · 把 effect 提成 window.contentView、Qt 视图挂到它下面
      · 在 theme frame 里把 effect 挂成 contentView 的兄弟层（NSWindowBelow）
    因此默认由 config.OVERLAY_VIBRANCY = False 关掉。
    """
    if not config.OVERLAY_VIBRANCY:
        return None
    if not _can_use_appkit():
        log.debug("当前 Qt 平台不是 cocoa，跳过毛玻璃")
        return None
    try:
        import objc
        from AppKit import (
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectStateActive,
            NSVisualEffectView,
        )

        view = objc.objc_object(c_void_p=int(widget.winId()))
        effect = NSVisualEffectView.alloc().initWithFrame_(view.bounds())
        effect.setAutoresizingMask_(18)  # NSViewWidthSizable | NSViewHeightSizable
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        layer = effect.layer()
        layer.setCornerRadius_(float(config.OVERLAY_RADIUS))
        layer.setMasksToBounds_(True)
        view.addSubview_positioned_relativeTo_(effect, -1, None)  # NSWindowBelow
        return effect
    except Exception as exc:
        log.debug("毛玻璃不可用，使用半透明卡片: %s", exc)
        return None


def _can_use_appkit() -> bool:
    """当前 Qt 平台插件是不是真·Cocoa。

    这个判断是**防崩溃**用的，不是讲究：`objc.objc_object(c_void_p=winId())`
    拿到的指针只有在 cocoa 平台上才真的是个 NSView。换到别的平台插件
    （offscreen / minimal / 甚至某些远程桌面场景）那个指针是假的，
    交给 pyobjc 就是**直接段错误**——Python 层 try/except 根本拦不住。
    所以在碰任何 AppKit 原生对象之前，先确认平台。
    """
    try:
        from PyQt6.QtGui import QGuiApplication

        name = (QGuiApplication.platformName() or "").lower()
        return name == "cocoa"
    except Exception:
        return False


def _bring_front_without_activating(widget) -> bool:
    """把顶层窗口提到最前，但**绝不激活本 App**。

    ★ 顶层窗口只能用这个，不能用 Qt 的 raise_()。
    实测（tools/probe_focus_steal.py，每个变体都先把 Finder 拉到前台再测）：
        show()                          → 前台仍是访达          ✅
        show() + raise_()               → 前台变成我们的进程    ❌
        show() + orderFrontRegardless() → 前台仍是访达          ✅

    为什么这件事这么重要：App 一旦被激活，**真实的空格键就不会再送给播放器**，
    而是投递给我们自己的窗口。用户侧的表现是「浮窗弹出后按空格播放器没反应，
    得先用鼠标点一下播放器」—— 正是那个被抱怨了很久的问题。
    根因不在窗口标志（WindowDoesNotAcceptFocus / WA_ShowWithoutActivating
    都已经设了），而在 raise_() 本身。

    非 cocoa 平台（离屏渲染、单测）没有"激活 App"这回事，
    退回 raise_() 即可 —— 那时它只是调层级，无害。
    """
    if not _can_use_appkit():
        try:
            widget.raise_()
        except Exception:
            pass
        return False
    try:
        import objc
        from ctypes import c_void_p

        view = objc.objc_object(c_void_p=int(widget.winId()))
        win = view.window() if view is not None else None
        if win is not None:
            win.orderFrontRegardless()
            return True
    except Exception as exc:
        log.debug("置前失败（不改用 raise_，宁可不置前也不抢焦点）: %s", exc)
    return False


def _float_over_everything(widget) -> bool:
    """让窗口能浮在**别的 App 的全屏窗口之上**、且在所有桌面（Space）都可见。

    这是"看剧时悬浮窗不出现"的真正解药。原因：
    macOS 上一个全屏视频播放器会独占一个独立 Space，普通窗口默认只存在于
    自己所在的 Space —— 于是 Qt 的 WindowStaysOnTopHint 再高也没用，用户
    在全屏看剧时**根本看不到悬浮窗**。

    解法（NSWindow 层）：
      · collectionBehavior 加 CanJoinAllSpaces（跟随用户到任何桌面）
        + FullScreenAuxiliary（允许出现在别人的全屏 Space 里）
        + Stationary（切 Space 时不跟着动画）；
      · level 提到 NSStatusWindowLevel（比普通窗口高，但低于系统弹窗）；
      · hidesOnDeactivate=False，别因为我们不是前台 App 就自动隐藏；
      · orderFrontRegardless()：**不激活 App** 也强制显示到最前。
    """
    if not _can_use_appkit():
        log.debug("当前 Qt 平台不是 cocoa，跳过 NSWindow 置顶设置")
        return False
    try:
        import objc
        from AppKit import (
            NSStatusWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
        )

        view = objc.objc_object(c_void_p=int(widget.winId()))
        win = view.window() if view is not None else None
        if win is None:
            return False

        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        win.setLevel_(NSStatusWindowLevel)
        try:
            win.setHidesOnDeactivate_(False)
            win.setCanHide_(False)
        except Exception:
            pass
        win.orderFrontRegardless()
        return True
    except Exception as exc:
        log.debug("设置「浮在全屏之上」失败（不影响窗口本身显示）: %s", exc)
        return False


def _make_pill_class():
    """「正在进行中」的小胶囊窗：按键后立刻出现，替换掉静默无反馈。

    以前按键到出结果之间有 5~20 秒完全是黑箱，用户以为程序没响应。
    现在这段时间会浮一个「🔍 正在识别截图… 3s」的小条。
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

    class _Pill(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setWindowTitle(config.APP_DISPLAY_NAME)

            card = QFrame(self)
            card.setObjectName("pill")
            card.setStyleSheet(
                "#pill { background-color: rgba(22,24,30,0.82);"
                " border: 1px solid rgba(255,255,255,0.10);"
                " border-radius: 16px; }"
            )
            self.lbl = QLabel("正在识别截图…")
            self.lbl.setStyleSheet(
                f"color:{config.OVERLAY_FG};font-size:13px;font-weight:600;"
                "font-family:'PingFang SC',sans-serif;"
            )
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color:{config.OVERLAY_ACCENT};font-size:11px;"
            )
            row = QHBoxLayout(card)
            row.setContentsMargins(18, 9, 20, 9)
            row.setSpacing(10)
            row.addWidget(dot)
            row.addWidget(self.lbl)

            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.addWidget(card)
            self._place()
            self._floated = False

        def update_text(self, text: str) -> None:
            self.lbl.setText(text)
            self._place()

        def _place(self) -> None:
            self.adjustSize()
            screen = (QGuiApplication.screenAt(self.cursor().pos())
                      or QGuiApplication.primaryScreen())
            geo = screen.availableGeometry()
            self.move(int(geo.x() + (geo.width() - self.width()) / 2),
                      int(geo.y() + geo.height() - self.height()
                          - config.OVERLAY_MARGIN_BOTTOM + 40))

        def showEvent(self, ev):  # noqa: N802
            super().showEvent(ev)
            if not self._floated:
                self._floated = True
                _float_over_everything(self)

    return _Pill


def _install_reactivate_filter(app, runtime) -> None:
    """点 Dock 图标 → 把控制面板叫回来。

    macOS 上「用户点 Dock 图标」会表现为应用被激活（ApplicationActivate）。
    如果此时控制面板是关着的，就重新打开它 —— 否则用户关掉面板后
    会找不到任何入口（菜单栏图标不一定显眼）。

    ⚠️ 但这个事件**不只是** Dock 点击会触发：查词过程中任何原因让本 App 被
    激活（创建浮窗、截图、浏览器进程起来……）都会触发一次。早期版本没有过滤，
    结果用户每次查词都会**连带弹出一个控制面板**，很吵（2026-09-14 反馈）。
    现在加两道闸：
      1) 只有「最近 X 秒没有任何自动动作」（不在查词、刚关闭浮窗之后）才认；
      2) 必须在 App 里没有任何可见窗口时。
    这两条一挡，正常的 Dock 点击依然有效，查词时的误触发则完全消失。
    """
    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtWidgets import QApplication

    class _Filter(QObject):
        def eventFilter(self, _obj, ev):
            try:
                if ev.type() == QEvent.Type.ApplicationActivate:
                    if runtime.auto_ui_recently():
                        log.debug("应用被激活，但刚做过自动动作（查词/浮窗）→ 不弹面板")
                        return False
                    if QApplication.topLevelWidgets():
                        visible = [w for w in QApplication.topLevelWidgets()
                                   if w.isVisible() and w is not runtime._tray]
                        if visible:
                            return False
                    p = runtime._panel
                    if p is None or not p.isVisible():
                        log.info("应用被用户激活（点 Dock 图标）→ 打开控制面板")
                        runtime.post_panel()
            except Exception:
                pass
            return False

    filt = _Filter(app)
    app.installEventFilter(filt)
    app._dl_reactivate_filter = filt  # 防 GC


class QtRuntime:
    """Qt 运行时。可以在主线程阻塞运行，也可以塞进后台线程。

    外部（监听器/菜单）只通过 `post_show` / `post_hide` 投递命令，
    由 Qt 线程内的 QTimer 取出执行，天然线程安全。
    """

    def __init__(self, on_overlay_closed: Callable[[], None],
                 on_overlay_ask: Callable[[str], None] | None = None) -> None:
        self._on_closed = on_overlay_closed
        self._on_ask = on_overlay_ask
        self._q: queue.Queue = queue.Queue()
        self._app = None
        self._overlay = None
        self._pill = None
        self._alert_win = None
        self._tray = None
        self._panel = None
        self._ctrl = None
        self._ready = threading.Event()
        # Qt 线程写、监听线程读的小状态（是否正在打字 → 空格/N 的含义会变）
        self.ui_state = SharedUiState()
        # 最近一次「自动弹 UI」（进度胶囊 / 悬浮窗）的时间戳。
        # 用来识别「这次应用被激活是我自己搞出来的，不是用户点的」——
        # 见 _install_reactivate_filter。
        self._auto_ui_at = 0.0

    # -------------------------------------------------- 打字状态（跨线程）
    def typing_active(self) -> bool:
        """用户是否正在浮窗输入框里打字（监听线程会读）。"""
        return self.ui_state.typing()

    def draft_text(self) -> str:
        """输入框里还没发出去的话（跨线程只读）。"""
        return self.ui_state.draft()

    def overlay_geometry(self):
        """浮窗当前占的屏幕矩形 (x, y, w, h)；没在显示就返回 None。

        「点击浮窗外面即关闭」用它做命中判定。返回值是**纯元组**，
        因为调用方在鼠标监听线程里，不能碰 QWidget。
        """
        if self._overlay is None:
            return None
        return self.ui_state.overlay_rect()

    def exit_input(self) -> None:
        """让浮窗退出输入态（点浮窗外面时用：先把键盘还给播放器）。"""
        if self._overlay is not None:
            try:
                self._overlay.exit_input()
            except Exception:
                log.debug("退出输入态失败（可忽略）")

    def has_overlay(self) -> bool:
        """浮窗此刻是否挂在屏幕上（App 用它决定追问回答往哪儿送）。

        【为什么必须有这个方法】踩过坑，记下来：
        app._overlay_visible() 一直调 self.runtime.has_overlay()，但 QtRuntime
        上**从来就没定义过它** —— 只有测试里的假运行时（FakeRuntime）有。
        于是真机每次都抛 AttributeError：
          · 追问的回答回不到浮窗（用户视角 = 「输入问题后什么都没发生」）；
          · 判「浮窗还在不在」的逻辑全走不到。
        假对象有、真对象没有，是这类 bug 最隐蔽的形态 —— 所以除了补上方法，
        还加了 tools/test_runtime_contract.py 做接口契约校验。

        跨线程读一个属性即可（_hide 会把 _overlay 置回 None，所以「对象存在」
        就等于「正挂在屏幕上」）。
        """
        return self._overlay is not None

    # -------------------------------------------------- 自动动作时间戳
    def mark_auto_ui(self) -> None:
        self._auto_ui_at = time.time()

    def auto_ui_recently(self, window: float = 8.0) -> bool:
        return (time.time() - self._auto_ui_at) < window

    # -------------------------------------------------- 对外命令
    def post_show(self, title: str, body: str) -> None:
        self._q.put(("show", (title, body)))

    def post_hide(self) -> None:
        self._q.put(("hide", None))

    def post_progress(self, text: str) -> None:
        """显示/更新「正在进行中」的小胶囊（按键后立刻有反馈）。"""
        self._q.put(("progress", text))

    def post_panel(self) -> None:
        """显示（或前置）控制面板窗口。"""
        self._q.put(("panel", None))

    def post_alert(self, title: str, body: str, actions=None) -> None:
        """弹一个需要用户点击的提示窗（会抢焦点，仅用于引导/报错）。"""
        self._q.put(("alert", (title, body, actions or [("知道了", None)])))

    def post_call(self, fn: Callable[[], None]) -> None:
        self._q.put(("call", fn))

    def refresh_overlay_style(self) -> None:
        """设置面板改了字号 → 让**当前这一屏**立刻按新字号重画。

        为什么值得做：用户拖动滑块时，浮窗如果正好开着，能当场看到
        "哦，变大了"；不然得等下一次查词才知道设置有没有生效 ——
        这种"设置了但看不出变化"最容易让人以为功能是坏的。
        浮窗没开就什么都不做（再正常不过的情况）。
        """
        def _run() -> None:
            ov = self._overlay
            if ov is None:
                return
            try:
                ov.restyle()
            except Exception:
                log.debug("按新字号重画浮窗失败（可忽略）")

        self.post_call(_run)

    def post_esc(self) -> None:
        """Esc：正在打字就退出输入态，否则关掉浮窗。"""
        self._q.put(("esc", None))

    def post_overlay_answer(self, text: str, error: bool = False) -> None:
        """追问的回答到了 → 追加显示在浮窗里（浮窗已关就只写日志）。"""
        self._q.put(("overlay_answer", (text, error)))

    def post_overlay_status(self, text: str) -> None:
        """更新浮窗顶部那行状态小字（例如「AI 正在回答…」）。"""
        self._q.put(("overlay_status", text))

    def post_demo_input(self, text: str) -> None:
        """仅供 --demo-ask 自证使用：模拟用户「点一下输入框 → 打字 → 点发送」。

        刻意走**和真人点击完全相同的控件**（enter_input / edit.setText / _on_send），
        所以它验证的确实是用户会用到的那条路径，而不是另开一条旁路。
        """
        def _run() -> None:
            ov = self._overlay
            if ov is None:
                log.warning("demo 追问：浮窗不在，跳过")
                return
            try:
                ov.enter_input()                 # 等价于用户点了一下输入框
                ov.edit.setText(text)            # 等价于用户把字打进去
                ov._on_send()                    # 等价于用户点了「发送」
                log.info("demo 追问：已模拟点击输入框并发送")
            except Exception:
                log.exception("demo 追问：模拟输入失败")

        self.post_call(_run)

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)

    # -------------------------------------------------- 运行
    def run_blocking(
        self,
        menubar_builder: Callable | None = None,
        ctrl=None,
        show_panel_first: bool = False,
    ) -> None:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(["DoubaoLookup"])
        app.setApplicationName(config.APP_DISPLAY_NAME)
        app.setQuitOnLastWindowClosed(False)
        self._app = app
        self._ctrl = ctrl

        if menubar_builder is not None:
            self._tray = menubar_builder()  # 在 Qt 线程内建菜单栏

        # 通知改走 Qt 原生（署名正确、不起子进程）
        if self._tray is not None and hasattr(self._tray, "showMessage"):
            from utils import set_notifier

            def _qt_notify(title: str, text: str) -> None:
                from PyQt6.QtWidgets import QSystemTrayIcon

                self._tray.showMessage(
                    title, text, QSystemTrayIcon.MessageIcon.Information, 4000
                )

            set_notifier(_qt_notify)

        # Dock 图标点一下就把控制面板叫回来（否则用户没有恢复入口）
        if ctrl is not None:
            _install_reactivate_filter(app, self)

        timer = QTimer()
        timer.setInterval(80)
        timer.timeout.connect(self._pump)
        timer.start()
        self._ready.set()

        if show_panel_first and ctrl is not None:
            QTimer.singleShot(400, self._show_panel)

        app.exec()

    def _pump(self) -> None:
        while True:
            try:
                cmd, payload = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd == "show":
                    self._show(*payload)
                elif cmd == "hide":
                    self._hide()
                elif cmd == "progress":
                    self._progress(*payload)
                elif cmd == "alert":
                    self._alert(*payload)
                elif cmd == "panel":
                    self._show_panel()
                elif cmd == "esc":
                    self._esc()
                elif cmd == "overlay_answer":
                    self._overlay_answer(*payload)
                elif cmd == "overlay_status":
                    self._overlay_status(payload)
                elif cmd == "call":
                    payload()
            except Exception:
                log.exception("Qt 命令 %s 执行失败", cmd)

    def _progress(self, text: str) -> None:
        """显示/更新那个「正在进行中」的小胶囊。

        为什么要它：按键到出结果之间有 5~20 秒。以前这段时间屏幕上**什么
        都没有**，用户以为程序坏了或者按键没生效；现在会浮一个小条并显示
        进度与已耗时秒数。
        """
        self.mark_auto_ui()
        try:
            if self._pill is None:
                cls = _make_pill_class()
                self._pill = cls()
                self._pill.show()
            self._pill.update_text(text)
            # ★ 用 orderFrontRegardless，**不要 raise_()** —— 见模块开头的大坑说明
            _bring_front_without_activating(self._pill)
        except Exception as exc:
            log.debug("进度胶囊显示失败: %s", exc)

    def _hide_pill(self) -> None:
        if self._pill is not None:
            pill, self._pill = self._pill, None
            try:
                pill.close()
                pill.deleteLater()
            except Exception:
                pass

    def _show(self, title: str, body: str) -> None:
        self.mark_auto_ui()
        self._hide(notify=False)      # 换一张卡：旧卡不要回调 on_closed
        # 兜底：宁可显示一句人话，也绝不弹一个空白框（空白框没法排查）
        if not (body or "").strip():
            body = (f"这次没拿到内容。\n可以再按一次【{config.TRIGGER_LABEL}】重试，"
                    "或到菜单栏打开日志看看。")
            log.warning("结果为空，已用占位文案代替，避免出现空白浮窗")
        # 记下"浮窗出现之前谁在前面" —— 就是你的播放器。用户在浮窗里打完字、
        # 或者关掉浮窗时，要把键盘焦点还给它（否则空格会被我们收走）。
        prev_app = _frontmost_app()
        if _app_is_self(prev_app):
            prev_app = None
        cls = _make_widget_class()
        self._overlay = cls(title, body, self._on_closed,
                            on_ask=self._on_ask,
                            ui_state=self.ui_state,
                            prev_app=prev_app)
        self._overlay.show()          # 不 activateWindow()：绝不抢焦点
        # ★ 这里**不能** raise_()：Qt 的 raise_() 会顺带激活整个 App，
        #   导致之后真实的空格键投递到我们的窗口、播放器收不到。
        #   _float_over_everything 内部用的是 orderFrontRegardless()，等效置前
        #   但**不激活应用** —— 实测过，见模块开头的说明。
        # 关键：让它能浮在全屏视频之上、且在所有桌面可见
        _float_over_everything(self._overlay)
        log.info("悬浮窗已显示（%d 字）", len(body or ""))

    # -------------------------------------------------- 浮窗的追问/状态/取消
    def _esc(self) -> None:
        """Esc：正在打字就只退出输入态（窗口留着）；否则关掉浮窗。"""
        if self._overlay is not None and self._overlay.input_active():
            self._overlay.exit_input()
            return
        self._hide()

    def _overlay_answer(self, text: str, error: bool = False) -> None:
        if self._overlay is None:
            log.warning("追问的回答到了，但浮窗已经关掉了（内容只写进日志）")
            log.info("追问回答：%s", (text or "")[:200])
            return
        try:
            self._overlay.deliver_answer(text, error=error)
        except Exception:
            log.exception("把追问回答填进浮窗失败")

    def _overlay_status(self, text: str) -> None:
        if self._overlay is None:
            return
        try:
            self._overlay.set_status(text)
        except Exception:
            log.debug("更新浮窗状态失败: %s", text)

    def _alert(self, title: str, body: str, actions) -> None:
        from utils import set_dock_icon_visible

        # 提示窗需要用户点击 → 临时把 Dock 图标放出来并激活，关掉后再收回去
        set_dock_icon_visible(True)
        cls = _make_alert_class()
        self._alert_win = cls(title, body, actions,
                              lambda: set_dock_icon_visible(False))
        self._alert_win.show()
        self._alert_win.activateWindow()
        self._alert_win.raise_()

    def _hide(self, notify: bool = True) -> None:
        self._hide_pill()
        if self._overlay is not None:
            ov, self._overlay = self._overlay, None
            if not notify:
                # 这是"换一张卡"，不是"用户关窗"：别回调 on_closed，
                # 否则状态机会被退回 IDLE，新卡片显示着却以为自己是空闲的。
                ov._closing = True
                try:
                    ov.exit_input()
                except Exception:
                    pass
            ov.close()
            ov.deleteLater()

    # -------------------------------------------------- 控制面板
    def _show_panel(self) -> None:
        """显示控制面板。它会抢焦点 —— 这是故意的，用户需要能点按钮。"""
        if self._ctrl is None:
            log.warning("控制面板需要 ctrl 引用，未注入")
            return
        from utils import set_dock_icon_visible

        set_dock_icon_visible(True)   # 面板可见时 Dock 图标也露出来，方便找回来
        if self._panel is None:
            from panel import make_panel_class

            self._panel = make_panel_class()(self._ctrl)
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()

    def quit(self) -> None:
        """退出应用（菜单「退出」和 demo 的自动退出都走这里）。

        ★★ 绝对不能在这里直接调 `self._app.quit()`。
        实测（macOS + PyQt6，offscreen 与真机一致）：
          · 主线程调 `app.quit()` / `app.exit(0)` → 立刻返回，`exec()` 正常退出；
          · **非主线程**调 `app.quit()` → **把整个 Cocoa 事件循环锁死** ——
            调用不返回、`exec()` 不返回，连主线程上的 QTimer 都不再触发
            （连"10 秒后兜底"的定时器都跑不到，是最阴的一种挂法）。
        而退出请求的来源（菜单 action、`threading.Timer`、后台线程）都不是主线程，
        所以只能**把 `app.quit` 塞进队列，交给主线程的 `_pump` 去执行**。

        在此之上再加一层 watchdog：`_pump` 那条路万一因为别的原因没生效
        （比如动作是在 QMenu 的嵌套事件循环里触发的，quit 只结束了嵌套循环），
        到点直接 `os._exit(0)`。watchdog 是纯 Python 线程，不受 Qt 死锁影响。
        """
        app = self._app
        log.info("收到退出请求（app=%s）", "已就绪" if app is not None else "未就绪")
        if app is None:
            return
        self._q.put(("call", app.quit))          # 交给主线程，别无他法

        # 看门狗时长要**大于** worker.stop() 的 12 秒上限（web 后端关无头浏览器
        # 时要让 Chrome 把 cookie 落盘，硬中断会导致下次又要重新扫码登录）。
        # 正常路径 1 秒内就退干净了，走不到这里。
        delay = float(os.environ.get("DL_QUIT_WATCHDOG_SEC", "20") or 20)

        def _watchdog() -> None:
            time.sleep(delay)
            log.warning("退出请求 %.0f 秒后进程仍未结束，强制退出", delay)
            os._exit(0)

        threading.Thread(target=_watchdog, name="quit-watchdog", daemon=True).start()


class QtThread(threading.Thread):
    """把 QtRuntime 跑在后台线程（rumps 占主线程时用这个）。"""

    def __init__(self, on_overlay_closed: Callable[[], None],
                 on_overlay_ask: Callable[[str], None] | None = None) -> None:
        super().__init__(name="qt-runtime", daemon=True)
        self.runtime = QtRuntime(on_overlay_closed, on_overlay_ask)

    def run(self) -> None:  # pragma: no cover
        try:
            self.runtime.run_blocking()
        except Exception:
            log.exception("Qt 线程退出")
