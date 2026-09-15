"""桌面「观看记录」 —— 把看剧过程中每一次 AI 输出落成一份可翻看的流水账。

用户原话
--------
「形成一个观看记录，名称为我正在看的剧名+日期，内容就是我目前看的所有的 AI
  输出的内容，加上编号和时间。直接放在桌面就行。」

于是这个模块负责三件事
----------------------
① **认剧名** —— 先读**屏幕上可见的 IINA 窗口标题**（它就是磁盘上的原始文件名），
   读不到再退回 mpv IPC 的 `filename`。拿到后从
   `老友记.Friends.S01E03.1080p.WEB-DL.mkv` 里剥出 `老友记 Friends`。
   有没有季集号、有没有分辨率/压制组后缀都能剥。
   （为什么优先窗口标题：IINA 内部可能有**多个 mpv core 抢同一个 socket**，
   IPC 会连到**空转的 core**，`filename` 直接读不到 —— 详见 player.py 里的说明。）
② **定文件** —— `<记录目录>/<剧名> <日期>.md`。目录可在设置面板里改，默认桌面。
   跨零点换日期、中途换剧，都会**自动开一份新文件**，两份记录不串；
   但**同一部剧当天已经有文件就直接复用**（按归一化剧名比对，不怕文件名抖动），
   不会因为"老友记"和"老友记 Friends"这种差异又开一本。
③ **编号 + 时间** —— 每条 `## 001 · 20:14:32`。
   新建的本子一律从 001 起；同一本里接着往下排（重开 App 也接着排，
   因为编号是从已有文件里解析出来的）。被人工删改过的本子可以一键重新编号
   （`renumber()`，面板上有按钮）。

为什么写 .md 而不是 .docx / .rtf
--------------------------------
这是一份**边看边追加**的流水账，每次查词追加一段。
Word 那类格式必须"读出整份 → 改 → 整份重写"，写坏一次整本记录就废了；
.md 是纯文本，追加是原子的，最坏只丢最后一次。
想转成别的格式随时可以，但记录本身不该靠重写来维持。

为什么放进后台线程
------------------
认剧名要连 IINA 的 socket，`PLAYER_CMD_TIMEOUT` 最长能卡 1 秒。
如果放在查词回调里同步做，用户就会觉得"浮窗怎么慢了一拍才出来"。
所以 `record()` 只往队列里丢一条，真正的 IPC + 写文件由守护线程做，
**绝不给显示路径加延迟**。

线程模型
--------
· `record()`     —— 任意线程可调，只入队，不阻塞。
· `_loop()`      —— 唯一的写线程，串行处理，所以不需要复杂的并发控制。
· `append_now()` —— 同步写一条（供测试和"退出前兜底"用）。
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
from pathlib import Path

import config
from utils import log

# ---------------------------------------------------------------- 剧名解析
# 季集标记的几种写法：S01E03 / s1e3 / 01x03 / EP03 / Episode 3
_SEASON_EP = re.compile(r"(?i)\bS\s?\d{1,2}\s?E\s?\d{1,2}\b")
_ALT_EP = re.compile(r"(?i)\b\d{1,2}\s?x\s?\d{1,2}\b")
_EP_WORD = re.compile(r"(?i)\b(EP|EPISODE|第)\s*\d{1,4}\s*(集|话)?\b")
# 压制参数 —— 见到这些就知道"剧名已经说完了"
_QUALITY = re.compile(
    r"(?i)\b(480p|576p|720p|1080[pi]|2160p|4k|8k|"
    r"web[\s._-]?dl|webrip|web-?rip|bluray|blu-?ray|bdrip|hdrip|dvdrip|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc|10bit|8bit|"
    r"aac|ac3|eac3|dts(-hd)?|truehd|atmos|ddp?5[\s.]?1|flac|"
    r"remux|hdr10?|dolby|proper|repack)\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# 【...】/(...)/[...] 里通常是发布组信息，一律丢掉
_BRACKETS = re.compile(r"[\[\(\{【（][^\]\)\}】）]{0,40}?[\]\)\}】）]")

# 扩展名要**按白名单**剥，不能用 `\.[A-Za-z]{2,5}$` 那种通配。
# 踩过的坑：通配写法会把剧名本身当扩展名吃掉 ——
#   `The.Bear.mkv` 先剥 `.mkv` 得 `The.Bear`，第二轮又拿 `\.[A-Za-z]{2,5}$`
#   匹配上 `.Bear`，于是剧名变成 `The`，桌面上的记录文件就叫「The 2026-09-15.md」。
_MEDIA_EXT = re.compile(
    r"(?i)\.(mkv|mp4|m4v|avi|mov|flv|wmv|webm|rmvb|rm|mpg|mpeg|m2ts|mts|ts|"
    r"vob|iso|srt|ass|ssa|sub|vtt|idx)$")
# 字幕常见的语言后缀：xxx.S01E01.zh.ass（剥掉 .ass 后还剩 .zh）
_LANG_EXT = re.compile(
    r"(?i)\.(zh|chs|cht|cn|sc|tc|hans|hant|eng|en|jp|jpn|kor|kr|fr|de|es)$")

_SEQ_RE = re.compile(r"^##\s+(\d{1,4})\b", re.M)


def show_name_from_filename(raw: str) -> str:
    """从播放文件名里剥出剧名。**纯函数，离线可测。**

    >>> show_name_from_filename("老友记.Friends.S01E03.1080p.WEB-DL.mkv")
    '老友记 Friends'
    >>> show_name_from_filename("D:\\剧\\The.Bear.S03E04.2160p.mkv")
    'The Bear'

    剥不掉就返回尽量干净的名字；实在什么都没有就返回空串
    （调用方会退回"未知剧名"或继续沿用上一份记录，见 `_pick_file`）。
    """
    if not raw:
        return ""
    s = str(raw).strip().strip('"').strip("'")

    # basename（Windows 的反斜杠路径也要切）
    s = re.split(r"[\\/]", s)[-1]
    # 剥扩展名：先剥媒体/字幕后缀，再剥语言后缀，各最多两轮
    # （`xxx.S01E01.zh.ass` → 剥 .ass → 剥 .zh）。**白名单**匹配，见 _MEDIA_EXT 注释。
    for _ in range(2):
        new = _MEDIA_EXT.sub("", s)
        if new == s:
            break
        s = new
    for _ in range(2):
        new = _LANG_EXT.sub("", s)
        if new == s:
            break
        s = new

    # 遇到第一个"剧名说完了"的信号就截断
    for pat in (_SEASON_EP, _ALT_EP, _EP_WORD, _QUALITY, _YEAR):
        m = pat.search(s)
        if m and m.start() > 0:
            s = s[: m.start()]

    s = _BRACKETS.sub(" ", s)
    s = s.replace(".", " ").replace("_", " ")
    # ★ 中↔英交界补一个空格。实测坑：`火星救援The.Martian` 去掉小数点之后是
    #   `火星救援The Martian` —— 中文名和英文名粘成一坨（应为 `火星救援 The Martian`）。
    s = re.sub(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])", " ", s)
    s = re.sub(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", s)
    s = re.sub(r"\s*[-–—]\s*$", "", s)          # 尾部孤零零的连字符
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" -–—_·.")


def normalize_show_key(show: str) -> str:
    """把剧名归一化成"同一部剧就该相等"的 key。**纯函数，离线可测。**

    只保留字母/数字/汉字并统一小写，于是
    `老友记 Friends` / `老友记.Friends` / `老友记_Friends` 都会变成同一个 key。
    """
    s = (show or "").strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def same_show(a: str, b: str) -> bool:
    """两个剧名是不是同一部剧。**纯函数，离线可测。**

    除了归一化后完全相等，**包含关系也算同一部** —— 文件名抖动经常只差一个
    英文名（`老友记` vs `老友记 Friends`），不认的话同一部剧会开出两本记录。
    要求较短的那一方至少 2 个字符，免得单个字母把什么都吞掉。
    """
    ka, kb = normalize_show_key(a), normalize_show_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return len(short) >= 2 and short in long_


# 最近一次成功认到的**原始文件名**。认不出时沿用它 ——
# 一次读取抖动不该把一晚上的记录切成两本。
_last_raw: str = ""


def _remember_raw(raw: str) -> None:
    global _last_raw
    if raw:
        _last_raw = raw


def last_show() -> str:
    """最近一次成功认到的剧名（清洗后的）。认不到时返回空串。"""
    return show_name_from_filename(_last_raw) if _last_raw else ""


def _media_filename() -> str:
    """现在放的是哪个文件。**多通道依次尝试**，都拿不到才返回空串。

    通道顺序（可靠 → 兜底）：
      ① **屏幕上可见的 IINA 窗口标题** —— 直接对应用户正在看的那个窗口，
         不受 IINA 内部"多 core 抢 socket"影响（实测最可靠）。
      ② IPC 的 `filename` / `path` / `media-title`。
      ③ 最近一次成功认到的原始文件名（`_last_raw`）。

    ★ 为什么优先 `filename` 而不是 `media-title`：`media-title` 会被文件内嵌的
      标题元数据覆盖（有的版本把单集名写成 "Pilot" 塞进去），那样整季的记录
      就会被拆成一堆 "Pilot.md"。`filename` 永远是磁盘上的真实文件名。

    ★ 这里**故意不再先看 `idle-active`**：连到"空转的 core"时它也会报
      `idle-active=true`，先看它就直接 return 了，连 filename 都没机会读 ——
      那正是老版本"明明在放片子却记成未知剧名"的成因之一。

    ★ 隐私：① 只读**屏幕上可见**的窗口，绝不枚举隐藏窗口；
      而且**只记来源、不记内容**（标题就是文件名，不进日志）。
    """
    try:
        import player
    except Exception as exc:                       # pragma: no cover
        log.debug("观看记录：播放器模块不可用（%s）", exc)
        player = None

    if player is not None:
        try:
            title = player.window_media_title()
        except Exception as exc:                   # pragma: no cover
            log.debug("观看记录：窗口标题通道失败（%s）", exc)
            title = ""
        if title:
            log.debug("观看记录：剧名取自窗口标题（内容不入日志）")
            _remember_raw(title)
            return title

        def _get(prop: str):
            resp = player._iina_cmd(["get_property", prop], timeout=0.6)
            if not resp or resp.get("error") not in (None, "success"):
                return None
            return resp.get("data")

        for prop in ("filename", "path", "media-title"):
            val = _get(prop)
            if isinstance(val, str) and val.strip():
                _remember_raw(val.strip())
                return val.strip()

    return _last_raw


# ---------------------------------------------------------------- 文件名
def safe_filename(name: str, max_len: int = 60) -> str:
    """把剧名洗成能当文件名的样子。**纯函数，离线可测。**

    要洗掉：路径分隔符、Windows 保留字符、换行、前后空白。
    另外截到 max_len —— 有些"剧名"其实是一长串发布组信息，
    不截的话文件名会长到 Finder 里显示不下。
    """
    s = (name or "").strip()
    s = s.replace("\n", " ").replace("\r", " ")
    for ch in config.VIEWLOG_BAD_CHARS:
        s = s.replace(ch, "-")
    s = re.sub(r"\s{2,}", " ", s).strip(" .-")
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .-")
    return s


def target_filename(show: str, date: str) -> str:
    """`剧名 日期.md`。剧名认不出来时退化成一个中性名字。"""
    name = safe_filename(show) or "未知剧名"
    return f"{name} {date}.md"


# ---------------------------------------------------------------- 记录本体
_q: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
_io_lock = threading.Lock()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()

# 当前正在写的那份记录：剧名 / 日期 / 路径 / 已用到的编号
_state: dict = {"show": "", "date": "", "path": None, "seq": 0}

_HEADER = (
    "# 观看记录 · {show}\n"
    "\n"
    "- 日期：{date}\n"
    "- 来源：看剧查词-美剧（自动生成，设置面板里可以关掉）\n"
    "\n"
    "> 编号按写入顺序排列；时间是你按空格查词的那一刻。\n"
    "\n"
    "---\n"
    "\n"
)


def _entry_text(seq: int, clock: str, kind: str, ask: str, body: str) -> str:
    """一条记录的正文（含分隔线）。"""
    head = f"## {seq:03d} · {clock}"
    if kind and kind != "查词":
        head += f" · {kind}"
    lines = [head, ""]
    if ask:
        # 追问记下"问了什么"才看得懂后面的回答，但标成引用，
        # 免得以后分不清哪句是 AI 说的、哪句是自己问的。
        lines += [f"> 问：{ask}", ""]
    lines += [body.strip(), "", "---", ""]
    return "\n".join(lines)


def _scan_max_seq(path: Path) -> int:
    """已有文件里最大的编号。文件不存在/读不动就当 0。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 0
    except Exception as exc:
        log.warning("观看记录：读不出已有编号（%s），编号从 1 重来", exc)
        return 0
    nums = [int(m.group(1)) for m in _SEQ_RE.finditer(text)]
    return max(nums) if nums else 0


# ------------------------------------------------------------ 记录目录
def _viewlog_dir() -> Path:
    """真正生效的记录目录（环境变量 > 面板里设的 > 桌面），并确保它存在。"""
    try:
        import settings
        return settings.ensure_viewlog_dir()
    except Exception as exc:                       # pragma: no cover
        log.debug("观看记录：读记录目录失败（%s），退回默认", exc)
        return Path(config.VIEWLOG_DIR)


def _scan_day_files(day: str) -> list[Path]:
    """当天已经存在的记录文件（形如 `* YYYY-MM-DD.md`）。"""
    try:
        return sorted(p for p in _viewlog_dir().glob(f"* {day}.md") if p.is_file())
    except Exception as exc:                       # pragma: no cover
        log.debug("观看记录：扫描当天文件失败（%s）", exc)
        return []


def _show_of_file(path: Path) -> str:
    """从记录文件名反推剧名：`老友记 Friends 2026-09-15.md` → `老友记 Friends`。"""
    m = re.match(r"^(.*)\s+\d{4}-\d{2}-\d{2}$", path.stem)
    return (m.group(1) if m else path.stem).strip()


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def _heal_file(path: Path, show: str, date: str) -> None:
    """文件被人工动过（抬头没了）就把抬头补回去。

    **只补抬头、不重排编号** —— 重排会改用户已经写好的内容，属于破坏性操作，
    必须由用户自己点面板上的「重新编号」才做（见 `renumber()`）。
    写回走"临时文件 + 原子替换"，最坏也不会把原文件写坏。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    if text.lstrip().startswith("# 观看记录"):
        return
    header = _HEADER.format(show=safe_filename(show) or "未知剧名", date=date)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(header + text.lstrip("\n"), encoding="utf-8")
        os.replace(tmp, path)
        log.info("观看记录：%s 缺抬头，已补回", path.name)
    except Exception as exc:                       # pragma: no cover
        log.warning("观看记录：补抬头失败（%s）", exc)


def renumber(path: Path | None = None) -> tuple[int, Path | None]:
    """把一本记录里的编号按出现顺序重排成 001、002…（**会改写文件**）。

    对应用户要的「序号从这个本子开始记」：被人工删改过的本子（例如前 53 条
    被删掉、剩下的从 054 开始）一键恢复成 001 起。
    先落一份 `<名字>.bak` 备份，再用原子替换写回。
    返回 `(重排条数, 路径)`；没得改就返回 `(0, None)`。
    """
    p = Path(path) if path is not None else current_path()
    if p is None or not p.exists():
        return 0, None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:                       # pragma: no cover
        log.warning("观看记录：重新编号读文件失败（%s）", exc)
        return 0, None

    counter = {"n": 0}

    def _sub(m: re.Match) -> str:
        counter["n"] += 1
        return f"## {counter['n']:03d}"

    # 只替换 `## 编号` 这个前缀，后面的 ` · 时间 · 追问` 原样保留
    new_text = re.sub(r"^##\s+\d{1,4}", _sub, text, flags=re.M)
    n = counter["n"]
    if n == 0 or new_text == text:
        return n, p
    try:
        try:
            p.with_suffix(p.suffix + ".bak").write_text(text, encoding="utf-8")
        except Exception:                          # pragma: no cover
            pass
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, p)
        _state["seq"] = n
        log.info("观看记录：%s 已重新编号（%d 条，原文件已备份为 .bak）", p.name, n)
    except Exception as exc:                       # pragma: no cover
        log.warning("观看记录：重新编号写回失败（%s）", exc)
        return 0, p
    return n, p


def _open_file(show: str, date: str) -> Path:
    """确定这次要写到哪个文件，并把编号基线准备好。

    开文件前先扫一遍记录目录里**当天**的 .md，按归一化剧名比对 ——
    命中就**直接复用那个文件**（连它原有的文件名一起沿用）。
    这就是用户要的「重名就写同一本」，顺手也治了"同一部剧两个本子"。
    """
    day_files = _scan_day_files(date)

    if show:
        same = [p for p in day_files if same_show(show, _show_of_file(p))]
        if same:
            if len(same) > 1:
                # 历史上真开出过好几本 → 统一写**内容最多**的那本（绝不删用户文件）
                same.sort(key=lambda q: (-_file_size(q), q.name))
                log.warning("观看记录：同一部剧当天有多本（%s），统一写进 %s",
                            "、".join(q.name for q in same), same[0].name)
            chosen = same[0]
            _state.update(show=show, date=date, path=chosen)
            _state["seq"] = _scan_max_seq(chosen)
            _heal_file(chosen, show, date)
            log.debug("观看记录：复用已有文件 %s", chosen.name)
            return chosen

    path = _viewlog_dir() / target_filename(show, date)
    _state.update(show=show, date=date, path=path)
    _state["seq"] = _scan_max_seq(path)

    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL：两个线程同时判定"文件不存在"时只有一个能建成，
            # 另一个拿到 FileExistsError → 当成"已存在"，编号从文件里扫。
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_HEADER.format(
                    show=safe_filename(show) or "未知剧名", date=date))
            log.info("观看记录：新建 %s", path)
        except FileExistsError:
            _state["seq"] = _scan_max_seq(path)
            log.debug("观看记录：文件已被另一个线程建好，复用 %s", path.name)
        except Exception as exc:
            log.warning("观看记录：新建文件失败 %s（%s）", path, exc)
    return path


def _pick_file(show: str, date: str) -> Path:
    """按「剧名 + 日期」决定这次写哪个文件。

    要开新文件：换日期、换剧、还没有文件。
    两种**故意不换**：
      · 这次没认出剧名（IINA 没开 / 读不到）→ 沿用手上这份记录往下写，
        否则每次读取抖一下都会开出一份「未知剧名 xxx.md」，一晚上被切得七零八落；
      · 同一部剧当天已经有文件 → 复用它（扫描逻辑在 `_open_file` 里）。
    """
    cur: Path | None = _state.get("path")
    same_day = _state.get("date") == date

    if cur is not None and same_day:
        if not show:
            return cur                                   # 认不出 → 沿用
        if same_show(show, _state.get("show") or ""):
            return cur                                   # 同一部剧 → 沿用
    return _open_file(show, date)


def append_now(body: str, ask: str = "", kind: str = "查词",
               which: str | None = None) -> Path | None:
    """**同步**写一条记录，返回写到了哪个文件。失败返回 None。"""
    text = (body or "").strip()
    if not text:
        return None
    if len(text) > config.VIEWLOG_MAX_BODY:
        # 截断而不是拒收 —— 记录的价值在"有这么一条"，不在长度。
        text = text[: config.VIEWLOG_MAX_BODY] + "\n…（内容过长，已截断）"

    now = time.localtime()
    date = time.strftime("%Y-%m-%d", now)
    clock = time.strftime("%H:%M:%S", now)

    with _io_lock:
        if which is None:
            show = show_name_from_filename(_media_filename())
        else:
            show = which
        try:
            path = _pick_file(show, date)
            seq = int(_state.get("seq") or 0) + 1
            _state["seq"] = seq
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_entry_text(seq, clock, kind, ask, text))
                fh.flush()
            log.info("观看记录 [%03d] → %s", seq, path.name)
            return path
        except Exception as exc:
            log.warning("观看记录：写入失败（%s）", exc)
            return None


def record(body: str, ask: str = "", kind: str = "查词") -> None:
    """**异步**记一条（界面路径就该用这个）。

    只做两件极快的事：判断开关、入队。IPC 与写盘都在写线程里，
    所以查词结果的显示永远不会被这份记录拖慢。
    """
    if not (body or "").strip():
        return
    try:
        import settings

        if not settings.get_viewlog_enabled():
            return
    except Exception:
        pass                                        # 开关读不到就照记不误
    _ensure_worker()
    _q.put((kind, (ask or "").strip(), body))


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="viewlog", daemon=True)
            _worker.start()


def _loop() -> None:
    while True:
        kind, ask, body = _q.get()
        try:
            append_now(body, ask=ask, kind=kind)
        except Exception:                           # pragma: no cover
            log.exception("观看记录：写入线程异常")
        finally:
            _q.task_done()


def flush(timeout: float = 5.0) -> bool:
    """等队列排空（测试用；退出前也可以调一次兜底）。"""
    if _worker is None:
        return True
    done = threading.Event()

    def _wait() -> None:
        try:
            _q.join()
        finally:
            done.set()

    threading.Thread(target=_wait, daemon=True).start()
    return done.wait(timeout)


def current_path() -> Path | None:
    """当前正在写的那份记录（面板上要显示路径，方便用户去找）。"""
    return _state.get("path")


def reset_for_test() -> None:
    """把模块状态清空（仅测试用）。"""
    global _worker, _last_raw
    with _io_lock:
        _state.update(show="", date="", path=None, seq=0)
    _last_raw = ""
    _worker = None


def reset_current() -> None:
    """忘掉"当前正在写的那本记录"。

    用户在面板里**换了记录目录**时调它：不清的话，下一次写入还会往旧目录
    那份文件里追加。只清内存状态，**一个文件都不动**；下次写入会重新扫目录
    决定该写哪本。
    """
    with _io_lock:
        _state.update(show="", date="", path=None, seq=0)


def find_for(show: str, date: str) -> Path:
    """给定剧名与日期，算出记录文件应该在哪（面板上"打开记录"用）。

    先看当天目录里有没有"同一部剧"的现成文件 —— 有就返回它，
    免得面板指着一个其实不存在的路径。
    """
    for p in _scan_day_files(date):
        if same_show(show, _show_of_file(p)):
            return p
    return _viewlog_dir() / target_filename(show, date)


def open_in_finder(path: Path | None = None) -> bool:
    """在 Finder 里选中这份记录；没有的话就打开记录目录。"""
    import subprocess

    p = path or current_path()
    try:
        if p is not None and Path(p).exists():
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["open", str(_viewlog_dir())])
        return True
    except Exception as exc:                        # pragma: no cover
        log.warning("打开观看记录失败: %s", exc)
        return False
