"""输出格式归一化的回归测试 —— **完全离线**，不需要网络/浏览器/权限。

盯的是用户 2026-09-15 的第 3 条要求：
「目前的提示词输出不稳定，有时候这个格式有时候那个格式。
  让输出的格式非常稳定，而且易读醒目。」

提示词写死只是**一半**（那一半在 config.DEFAULT_PROMPT）。另一半在这里：
`reply_clean` 把"不管模型这次写成什么样"的回复统一解析成结构化条目，
再输出**唯一**的规范文本。这才是"格式稳定"的保证 ——
模型总有跑偏的概率，指望提示词 100% 约束住是不现实的。

跑法：
    python3 tools/test_reply_clean.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                     # noqa: E402
import reply_clean                                                # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}\n      期望 {want!r}\n      实际 {got!r}")


# ================================================== 1. 提示词本身
print("== 1. 提示词：逐字模板 + 禁止项都在 ==")
p = config.DEFAULT_PROMPT
for must in ("句子：<", "译文：<", "生词：<", "俚语：<", "结构：<"):
    check(f"模板里有 {must!r}", must in p, True)
for must in ("中文全角", "不要编号", "不要 markdown", "不要问我"):
    check(f"规则里有 {must!r}", must in p, True)
# 用户原来那版提示词的三个"不稳源"必须都被新模板消掉
check("不再要求用「——」写句子", "句子——" not in p, True)
check("不再要求模型上色（上色由渲染层做）",
      all(x not in p for x in ("头部用蓝色", "用蓝色显示（", "加粗显示")), True)
check("保留了用户要的：音标", "音标" in p, True)
check("保留了用户要的：词根词缀", "词根词缀" in p, True)
check("保留了用户要的：六级水平过滤", "六级" in p, True)
check("保留了用户要的：没有就只回复「无」", "只回复两个字：无" in p, True)
check("预设用的是同一套固定标签写法",
      all("句子：<" in v for v in config.PROMPT_PRESETS.values()), True)

# ================================================== 2. 各种跑偏写法都归一
print("\n== 2. 归一化：模型换写法，输出不变 ==")
CANON = ("句子：You're gonna have to face the music.\n"
         "译文：你迟早得承担后果。\n"
         "生词：gonna /ˈɡɔːnə/ v. going to 的口语缩略\n"
         "俚语：face the music → 承担后果\n"
         "结构：have to 后面省略了 that 引导的从句，语气上是被迫的")

variants = {
    "标准写法（应原样保持）": CANON,
    "字段名换同义词（原句/翻译/词汇/口语/语法）":
        "原句：You're gonna have to face the music.\n"
        "翻译：你迟早得承担后果。\n"
        "词汇：gonna /ˈɡɔːnə/ v. going to 的口语缩略\n"
        "口语：face the music → 承担后果\n"
        "语法：have to 后面省略了 that 引导的从句，语气上是被迫的",
    "半角冒号 + 编号 + 客套开头":
        "好的，我看完了：\n"
        "1. 句子: You're gonna have to face the music.\n"
        "2. 译文: 你迟早得承担后果。\n"
        "3. 生词: gonna /ˈɡɔːnə/ v. going to 的口语缩略\n"
        "4. 俚语: face the music → 承担后果\n"
        "5. 结构: have to 后面省略了 that 引导的从句，语气上是被迫的",
    "markdown 加粗 + 列表符":
        "**句子：** You're gonna have to face the music.\n"
        "- **译文：** 你迟早得承担后果。\n"
        "- **生词：** gonna — /ˈɡɔːnə/ v. going to 的口语缩略\n"
        "- **俚语：** face the music → 承担后果\n"
        "- **结构：** have to 后面省略了 that 引导的从句，语气上是被迫的",
    "俚语用 -> 而不是 →、结构写成「句子结构」":
        "句子：You're gonna have to face the music.\n"
        "译文：你迟早得承担后果。\n"
        "生词：gonna : /ˈɡɔːnə/ v. going to 的口语缩略\n"
        "俚语：face the music -> 承担后果\n"
        "句子结构：have to 后面省略了 that 引导的从句，语气上是被迫的",
}
for label, raw in variants.items():
    check(label, reply_clean.normalize_answer(raw), CANON)

# ================================================== 3. 无字段名时的猜测
print("\n== 3. 模型彻底不写字段名时，也要猜对（且不丢内容）==")
check("英文散行 → 句子（不是译文）",
      reply_clean.normalize_answer("This sentence has no new words."),
      "句子：This sentence has no new words.")
check("中文散行 → 译文",
      reply_clean.normalize_answer("这句话没什么生词。"),
      "译文：这句话没什么生词。")
check("`word —— 释义` 老写法 → 生词",
      reply_clean.normalize_answer("gonna —— going to 的口语缩略"),
      "生词：gonna going to 的口语缩略")
check("带空格的表达 → 俚语",
      reply_clean.normalize_answer("face the music —— 承担后果"),
      "俚语：face the music → 承担后果")
check("认不出的整行原样保留（绝不丢内容）",
      reply_clean.normalize_answer(
          "句子：Hi\n译文：你好\n——— 剧情补充：本集片尾有彩蛋 ———"),
      "句子：Hi\n译文：你好\n——— 剧情补充：本集片尾有彩蛋 ———")

# ================================================== 4. 幂等 + 稳定
print("\n== 4. 归一化是幂等的（跑两遍结果一样）==")
for label, raw in variants.items():
    once = reply_clean.normalize_answer(raw)
    check(f"幂等 · {label}", reply_clean.normalize_answer(once), once)

# ================================================== 5. 空内容判据
print("\n== 5. is_empty_answer：裸答「无」和守规矩的「生词：无」都要认 ==")
for text, want in [
    ("无", True),
    ("没有生词", True),
    ("生词：无", True),
    ("生词：无\n俚语：无", True),
    ("   生词：  没有 ", True),
    ("句子：Hi\n译文：你好", False),
    ("This sentence has no new words.", False),
    ("", False),                      # 空文本走"没识别到有效文本"分支，不算 empty
]:
    check(f"{text!r} → {want}", reply_clean.is_empty_answer(text), want)

# ================================================== 6. 段落切分
print("\n== 6. parse_entries 的结构化结果 ==")
entries = reply_clean.parse_entries(reply_clean.clean_reply(CANON))
check("五类都解析出来了",
      [e[0] for e in entries],
      ["sentence", "translation", "word", "idiom", "structure"])
check("生词的词头/释义切开了",
      (entries[2][1], entries[2][2]),
      ("gonna", "/ˈɡɔːnə/ v. going to 的口语缩略"))
check("句子/译文的 head 是空的",
      (entries[0][1], entries[1][1]), ("", ""))
check("「句子结构」不会被误当成「句子」（长标签优先）",
      reply_clean.normalize_answer("句子结构：这是倒装句"),
      "结构：这是倒装句")
check("「语法」「句法」也认",
      [reply_clean.parse_entries(f"{k}：x")[0][0]
       for k in ("语法", "句法", "句式")],
      ["structure"] * 3)

# ================================================== 7. is_structured（追问用）
# 用户 2026-09-15 的补充要求：「可以不用按照我刚才的模版回答我」。
# 也就是说追问的回答**允许是自由散文**，那时绝不能按字段名归一化/上标签 ——
# 判据就是下面这个函数，它是"要不要走模板那条路"的总闸门。
print("\n== 7. is_structured：判断「这段回答是不是照字段模板写的」==")

TEMPLATE_ANSWER = (
    "句子：I've been meaning to tell you.\n"
    "译文：我一直想告诉你。\n"
    "生词：mean to /ˈmiːnɪŋ tuː/ phr. 打算、有意\n"
    "俚语：face the music → 承担后果"
)
PROSE_ANSWER = (
    "能，但语气差一点，得看你想给老板什么感觉：\n"
    "\n"
    "1. get around to 的核心是“一直想做、拖到现在才做”，隐含**我自己拖延**。\n"
    "   对老板说 I'll get around to it，等于承认“我早该做了但没做”。\n"
    "2. 想表达“尽快处理”且不背拖延的锅，用下面这些更稳：\n"
    "   · I'll get right on it. —— 马上办，最贴合你的意思。"
)
for label, text, want in [
    ("四行字段模板", TEMPLATE_ANSWER, True),
    ("自由散文（分点 + 列表）", PROSE_ANSWER, False),
    ("单行「译文：xxx」", "译文：就一句", False),
    ("空文本", "", False),
    ("带编号的字段模板（编号会被剥掉再判）",
     "1. 句子：Hi\n2. 译文：你好", True),
    ("模板只占少数（1/4 行带字段名）",
     "译文：就一句\n然后我想补充几点：\n第一，这句在剧里是反讽。\n第二，重音落在 music 上。", False),
    ("模板占多数（2/3 行带字段名）",
     "句子：Hi\n译文：你好\n再补一句我自己的看法。", True),
]:
    check(f"{label} → {want}", reply_clean.is_structured(text), want)

# ★ 这条是"为什么要加 is_structured"的**证据本身**：
#   normalize_answer 会把散文改写成带字段名的行（字段名还全是错的），
#   所以自由的追问回答**绝不能**过这道归一化。
mangled = reply_clean.normalize_answer(PROSE_ANSWER)
check("反例：normalize_answer 会把散文贴上错误字段名",
      "译文：能，但语气差一点" in mangled, True)
check("  而 is_structured 能把它拦下来（所以调用方要先用它判断）",
      reply_clean.is_structured(PROSE_ANSWER), False)

print(f"\n共 {PASS + FAIL} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
