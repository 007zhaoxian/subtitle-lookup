"""模型回复的清洗与**格式归一化**。

为什么要有"归一化"这一步
------------------------
用户 2026-09-15 的反馈：「目前的提示词输出不稳定，有时候这个格式有时候那个格式」。
光把提示词写死是不够的 —— 模型（尤其是换 provider / 换模型之后）总会有
一定概率"跑偏"：把 `生词：` 写成 `单词：`、把 `：` 写成 `:`、
把两条并成一行、加个编号、或者干脆回到旧版那种 `word —— 释义` 的写法。

所以这里做两件事：
  ① `clean_reply()`  —— 去掉客套前缀/结尾追问/markdown 记号（老逻辑）；
  ② `parse_entries()` —— 把**不管什么格式**的回复统一解析成结构化条目，
     再由 `to_plain()` 输出唯一的规范文本。

于是下游（浮窗渲染、观看记录、日志）永远只面对**一种**文本形状，
"这次格式又变了"这类问题就从根上没了。

条目类型：sentence 句子 / translation 译文 / word 生词 / idiom 俚语 /
         structure 结构（语法句法分析）/ other 其它。
"""
from __future__ import annotations

import re

import config

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_LIST = re.compile(r"^\s*[-*+]\s+", re.M)
_MD_QUOTE = re.compile(r"^\s*>\s?", re.M)
_MD_TICK = re.compile(r"`{1,3}")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")

# 行首的行内/有序标记：`- `、`· `、`1. `、`1、`、`一、`、`①`、`> `
_LINE_MARK = re.compile(
    r"^\s*(?:[-*+•·>]\s*|\(?\d{1,2}[.)、]\s*|[一二三四五六七八九十]{1,2}[、.)]\s*"
    r"|[①-⑳]\s*)+"
)

# 字段名 → 条目类型。**中英标点都收**，模型用错标点也不至于解析不出来。
#
# ★ 顺序有讲究：`_LABEL_RE` 把这里的 key 按**长度倒序**排成正则的候选分支，
#   所以「句子结构」一定能先于「句子」被匹配到 —— 否则带结构分析的那一行
#   会被当成句子，句子就重复了两遍。
_LABELS: dict[str, str] = {
    "句子": "sentence", "原句": "sentence", "原文": "sentence",
    "台词": "sentence", "字幕": "sentence",
    "译文": "translation", "翻译": "translation", "中文": "translation",
    "句意": "translation", "释义": "translation",
    "生词": "word", "单词": "word", "词汇": "word", "词": "word",
    "俚语": "idiom", "固定搭配": "idiom", "习语": "idiom",
    "短语": "idiom", "口语": "idiom", "表达": "idiom",
    # 语法/句法分析（用户明确要求：句子复杂时讲一下结构）
    "结构": "structure", "句子结构": "structure", "语法": "structure",
    "句式": "structure", "句法": "structure", "语法分析": "structure",
}
_LABEL_RE = re.compile(
    r"^\s*(" + "|".join(sorted(_LABELS, key=len, reverse=True)) + r")\s*[:：]\s*"
)

# 词条内部的分隔符（模型可能用任何一种）
_SEPS = ("——", "→", "->", "—", "--", "－", "=")

# 规范文本里用的字段名（渲染端按它做版式）
PLAIN_LABEL = {
    "sentence": "句子",
    "translation": "译文",
    "word": "生词",
    "idiom": "俚语",
    "structure": "结构",
    "other": "",
}


def clean_reply(raw: str, keep_markdown: bool = False) -> str:
    """把模型气泡的原文变成适合小卡片显示的干净文本。

    `keep_markdown=True`：**保留 `**加粗**`、`·` 这类行内标记**，只去掉标题井号。

    ★ 为什么需要这个开关：追问的回答是**自由格式**，浮窗会按 markdown 渲染
      （真加粗、列表缩进、英文强调，见 overlay.format_prose）。如果在清洗阶段
      就把 `**` 抹掉，那一层渲染永远触发不了 —— 用户看到的还是被"洗平"的纯文本。
      所以自由格式那条路要 keep_markdown=True；
      "字段模板"那条路仍用默认值（模板里不该有 markdown，抹掉更干净）。
    """
    if not raw:
        return ""

    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()

    # 1) 去掉开头的客套话（逐行尝试，最多剥 3 层）
    for _ in range(3):
        lines = text.split("\n")
        # 丢掉开头的空行
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return ""
        first = lines[0].strip()
        new_first = first
        for pat in config.REPLY_PREFIX_PATTERNS:
            new_first = re.sub(pat, "", new_first).strip()
            if new_first != first:  # 命中一次就重来
                break
        # 形如「好的，截图里我看到了这些生词：」整行都是废话 → 整行删掉
        if new_first != first and (not new_first or new_first.endswith(("：", ":"))):
            lines.pop(0)
        elif new_first != first:
            lines[0] = new_first
        text = "\n".join(lines).strip()
        if text == "":
            return ""

    # 2) 去掉结尾的客套/追问（不加 re.M：只在整段文本末尾生效）
    for pat in config.REPLY_SUFFIX_PATTERNS:
        text = re.sub(pat, "", text).strip()

    # 3) Markdown → 纯文本（保留 list 结构）
    #    标题井号一律去掉（两种模式都不要 ##）；其余标记只在"非自由格式"时抹掉。
    text = _MD_HEAD.sub("", text)
    if not keep_markdown:
        text = _MD_BOLD.sub(r"\1", text)
        text = _MD_LINK.sub(r"\1", text)
        text = _MD_QUOTE.sub("", text)
        text = _MD_TICK.sub("", text)
        text = _MD_LIST.sub("- ", text)

    # 4) 折叠 3 个以上空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_entry(body: str) -> tuple[str, str]:
    """把一条词条切成 (词/表达, 解释)。

    模型可能写成 `word —— 词性 / 释义`、`word → 含义`、`word: 释义`……
    这里统一按"最先出现的那个分隔符"切。
    返回 (head, tail)；切不开时 head 为空、tail 是原文。
    """
    s = body.strip()
    for sep in [":", "："] + list(_SEPS):
        if sep in s:
            head, _, tail = s.partition(sep)
            return head.strip(), tail.strip()
    return "", s


def _word_head(rest: str, kind: str) -> tuple[str, str]:
    """从**规范写法**的「生词：gonna /ˈɡɔːnə/ v. 释义」里切出词和释义。

    为什么 `_split_entry()` 不够：规范写法里词和释义之间**没有分隔符**
    （词后面直接跟空格 + 音标），所以按 ":" / "——" / "→" 都切不开，
    head 会是空的 —— 后果是渲染时那个高亮加粗的词头消失，
    整行变成一坨普通文字（实测：`生词：gonna /ˈɡɔːnə/ v. …` 里
    "gonna" 不再是蓝色加粗的）。

    切法（按可靠性排序）：
      ① 「词 + 空格 + /音标/」→ 第一个斜杠之前就是词（最可靠，规范写法命中这条）；
      ② 没有音标、且是「生词」→ 第一段空白之前算词（`生词：gonna 口语缩略`）；
      ③ 都不满足 → 返回空 head，由调用方原样处理（绝不丢内容）。
    """
    s = (rest or "").strip()
    if not s:
        return "", ""
    m = re.match(r"^(.{1,60}?)\s+(?=/)", s)          # ①
    if m:
        return m.group(1).strip(), s[m.end():].strip()
    if kind == "word":                               # ②
        parts = s.split(None, 1)
        if len(parts) == 2 and 0 < len(parts[0]) <= 30:
            return parts[0], parts[1]
    return "", s                                     # ③


def parse_entries(text: str) -> list[tuple[str, str, str]]:
    """把回复解析成 `[(kind, head, body)]`。**纯函数，离线可测。**

    · kind ∈ sentence / translation / word / idiom / structure / other
    · head：词条的"词本身"（生词就是那个单词、俚语就是那个表达）；
            句子/译文/结构/其它条目 head 为空。
    · body：冒号后面那截（生词的音标+词性+释义都在这里）。

    容错原则：**宁可当成 other 原样显示，也不要丢内容**。
    用户是来看释义的，格式没认出来不算大事，少一行字才是大事。
    """
    entries: list[tuple[str, str, str]] = []
    seen_sentence = seen_translation = False

    for raw_line in (text or "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line = _LINE_MARK.sub("", line).strip()
        if not line:
            continue

        m = _LABEL_RE.match(line)
        if m:
            kind = _LABELS[m.group(1)]
            rest = line[m.end():].strip()
        else:
            kind, rest = "", line

        if kind == "sentence":
            seen_sentence = True
            entries.append(("sentence", "", rest))
            continue
        if kind == "translation":
            seen_translation = True
            entries.append(("translation", "", rest))
            continue
        if kind == "structure":
            # 语法/结构说明：跟句子一样是整行一条，没有"词头"
            entries.append(("structure", "", rest))
            continue
        if kind in ("word", "idiom"):
            head, body = _split_entry(rest)
            if not head:
                # 模型按规范写法写（`生词：gonna /ˈɡɔːnə/ v. …`）时走这条
                head, body = _word_head(rest, kind)
            entries.append((kind, head, body))
            continue

        # ---- 没有字段名：按内容猜 ----
        # 词条（含分隔符、且有拉丁词头）→ 生词/俚语
        head, body = _split_entry(rest)
        # 没标签也没分隔符，但**带音标** → 几乎肯定是生词（`gonna /ˈɡɔːnə/ …`）。
        # ★ 这里只认音标，**不**用 `_word_head` 的"按第一个空白切"那条兜底 ——
        #   否则 `This sentence has no new words.` 会被切成
        #   head="This" 而误判成生词（实测踩到）。
        if not head and re.search(r"^\s*\S{1,40}\s+/[^/]{1,40}/", rest):
            head, body = _word_head(rest, "word")
        latin_head = bool(re.search(r"[A-Za-z]", head))
        if head and latin_head and len(head) <= 40:
            # 有空格的多半是短语/俚语，单词则是生词
            kind2 = "idiom" if (" " in head.strip()) else "word"
            # 「→」是最明确的俚语信号
            if "→" in rest or "->" in rest:
                kind2 = "idiom"
            entries.append((kind2, head, body))
            continue
        # ★ 判"这句是中文还是英文"要看**正文**，不能看 head。
        #   踩过的坑：`head` 是 `_split_entry()` 切出来的，散行切不开时
        #   head 恒为空字符串，于是 `latin_head` 永远 False ——
        #   一行英文原文会被当成"纯中文的散行"判成**译文**。
        #   （实测：`This sentence has no new words.` → 「译文：…」）
        latin_content = bool(re.search(r"[A-Za-z]", rest))
        # 纯中文的散行：第一句当译文
        if not seen_translation and not latin_content:
            seen_translation = True
            entries.append(("translation", "", rest))
            continue
        # 含拉丁字母的散行：第一句当原文
        if not seen_sentence and latin_content:
            seen_sentence = True
            entries.append(("sentence", "", rest))
            continue
        entries.append(("other", "", rest))

    return entries


def is_structured(text: str, min_ratio: float = 0.6, min_lines: int = 2) -> bool:
    """这段回答是不是**照字段模板写的**？（只看显式字段名，不猜）

    为什么需要它 —— 追问被"模板化"的那个坑：

      用户明确说过「追问可以不用按照我刚才的模版回答我」。也就是说追问的回答
      可能是**自由散文**（分点、列表、几段话，甚至带 markdown 的 `**加粗**`）。
      可下游有两处会把任何文本按模板归一化：

        · `normalize_answer()` —— 会把散文硬掰成「译文：能，但语气差一点…」
          这种带字段名的行（因为它得猜一个 kind 出来）；
        · `overlay.format_lines()` —— 会照着字段名给每行配一个彩色小标签。

      结果就是：一段正常的解释被贴上「译文」「句子」「俚语」的标签，
      又错又难看。所以**先判断形状，再决定要不要归一化/上标签**。

    ★ 判据只用**显式字段名**（句子/译文/生词/俚语/结构… 后面跟冒号那种），
      绝不复用 parse_entries 的"猜 kind"逻辑 —— 恰恰是那个猜测把散文认成译文的。

    参数经验值：
      · min_ratio=0.6 —— 带字段名的行要占非空行的 6 成以上；
      · min_lines=2   —— 至少两行带字段名。单行「译文：xxx」判成散文更安全，
        渲染出来就是一行原文，不会莫名其妙多个标签。
    """
    n_lines = n_labeled = 0
    for raw_line in (text or "").split("\n"):
        line = _LINE_MARK.sub("", raw_line.strip()).strip()
        if not line:
            continue
        n_lines += 1
        if _LABEL_RE.match(line):
            n_labeled += 1
    if n_lines == 0 or n_labeled < min_lines:
        return False
    return n_labeled / n_lines >= min_ratio


def to_plain(entries: list[tuple[str, str, str]]) -> str:
    """把条目输出成**唯一**的规范文本（下游只看这一种形状）。

    规范形状（行首固定字段名 + 中文全角冒号）：
        句子：<原句>
        译文：<翻译>
        生词：<单词> /<音标>/ <词性> <释义>
        俚语：<表达> → <含义>
        结构：<语法/句法分析（只在句子确实有难度时才有这行）>
    """
    out: list[str] = []
    for kind, head, body in entries:
        if kind == "other":
            out.append(body.strip())
            continue
        label = PLAIN_LABEL.get(kind, "")
        if not label:
            out.append(body.strip())
            continue
        if kind in ("word", "idiom"):
            sep = " → " if kind == "idiom" else " "
            if head and body:
                out.append(f"{label}：{head}{sep}{body}")
            elif head:
                out.append(f"{label}：{head}")
            elif body:
                out.append(f"{label}：{body}")
            continue
        out.append(f"{label}：{body}" if body else f"{label}：")
    return "\n".join(x for x in out if x.strip())


def normalize_answer(raw: str) -> str:
    """一步到位：原始回复 → 规范文本。浮窗显示 / 观看记录 / 日志都用它。

    注意它**不改变内容**，只统一"字段名 + 分隔符 + 每行一件事"这三个形状。
    """
    cleaned = clean_reply(raw)
    if not cleaned:
        return ""
    entries = parse_entries(cleaned)
    if not entries:
        return cleaned
    plain = to_plain(entries)
    return plain or cleaned


# 「没什么可讲」的写法。模型可能裸答一个「无」，也可能守规矩写成
# 「生词：无」—— 两种都要认得。
_EMPTY_WORDS = {
    "无", "没有", "暂无", "无生词", "没有生词", "该句无生词", "无新词",
    "没有新词", "无俚语", "没有俚语", "none", "n/a", "na", "无内容",
}


def is_empty_answer(text: str) -> bool:
    """判断是否"这一句没什么可讲的"（此时浮窗显示友好提示而不是空白）。

    ★ 2026-09-15 改成**逐行**判断，而不是拿整段去比对：
      旧写法是 `t in {"无", "没有生词", ...}`，只认整段只有一个词的情况。
      新提示词要求「没有生词就不写生词行」，但模型偶尔仍会写成
      `生词：无` —— 那种情况下整段既不等于"无"、也确实没内容，
      旧写法就判不出来，浮窗会显示一行干巴巴的「生词：无」。
      现在先把每行的字段名剥掉再看内容，`生词：无` 也能认出来。

    注意：空字符串返回 False —— "什么都没解析出来"是另一条分支
    （app._finish_ok 里显示"没有识别到有效文本"），不该混进来。
    """
    t = (text or "").strip()
    if not t:
        return False
    for line in t.split("\n"):
        s = _LINE_MARK.sub("", line.strip()).strip()
        if not s:
            continue
        m = _LABEL_RE.match(s)
        if m:
            s = s[m.end():].strip()
        if s.strip("。.!！:：、,， ").lower() not in _EMPTY_WORDS:
            return False            # 只要还有一行有实质内容，就不算"没东西可讲"
    return True
