"""「所有发给 AI 的提示词都能编辑」+「追问规则必须在场」的回归测试。

用户诉求 ②：写提示词的软件里必须能自己改提示词。
用户诉求 ③（2026-09-15 二次修订）：追问时模型不能拒答，且**可以不照模板回答**。

★ v1.2.2 改了什么：
  · 删掉了「② 发送时自动追加的约束句（guard）」那一整节测试 ——
    那段「只看本条消息这张图、忽略上文」是**网页版豆包专用**的硬约束，
    随网页版一起删除（详见 config.py 顶部与 prompt_edit.py 文件头）。
  · 新增「追问规则」测试。这是本轮真正的修复点：
    提示词会**常驻在追问的对话历史里**（app._api_messages），
    所以必须在提示词内部显式声明"纯文字追问不受字段模板约束"，
    否则模型会把「只输出那五行」当成对整个对话都生效 → 拒答或只回「无」。
    实测证据见 tools/probe_followup_ab.py（旧提示词追问只回 1 个字，
    新提示词回 615 字）。

要钉死的点：
  1. 主提示词：改了就生效、能恢复默认、可判断"是否已自定义"；
  2. ★ 追问规则：默认提示词带、**每一套预设都带**、内容含"作废/不受约束/自由"语义；
  3. ★ 尾随换行：DEFAULT_PROMPT 不能以 "\\n" 结尾（否则 set_prompt 的 strip()
     会让"存进去又读出来"≠默认值，面板上永远显示"已自定义"）；
  4. 预设：内置的能覆盖、能还原；用户自建的能加能删；
  5. 坏数据（空的/超长/类型不对）不能让设置文件变成一颗雷。

用法: python tools/test_prompt_editable.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_prompt_")

import config  # noqa: E402
import settings  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def test_main_prompt() -> None:
    settings.reset_prompt()
    check("默认状态：取到内置默认提示词",
          settings.get_prompt() == config.DEFAULT_PROMPT)
    check("默认状态：不算「已自定义」", settings.is_customized() is False)

    settings.set_prompt("只解释图里的生词，一行一条，格式：词 —— 释义。")
    check("改完立刻生效", settings.get_prompt().startswith("只解释图里的生词"))
    check("改完标记为已自定义", settings.is_customized() is True)

    settings.load(force=True)          # 丢掉缓存，模拟重启
    check("重启后自定义内容还在",
          settings.get_prompt().startswith("只解释图里的生词"))

    settings.reset_prompt()
    check("恢复默认后不再是自定义", settings.is_customized() is False)


def test_followup_rule() -> None:
    """★★ 本轮的核心：追问不受字段模板约束，且必须对所有预设生效。"""
    rule = getattr(config, "_FOLLOWUP_RULE", "")
    check("config 里有 _FOLLOWUP_RULE", bool(rule.strip()))

    # ---- 默认提示词必须带着它 ----
    check("默认提示词含【追问规则】", "【追问规则" in config.DEFAULT_PROMPT)
    check("默认提示词以追问规则收尾",
          config.DEFAULT_PROMPT.endswith(rule))
    check("存进去再读出来仍算「默认」（无尾随换行）",
          config.DEFAULT_PROMPT == config.DEFAULT_PROMPT.strip(),
          f"末尾 = {config.DEFAULT_PROMPT[-12:]!r}")

    # ---- 语义校对：三个要点缺一不可 ----
    # ① 划清作用域：说清"只对带图那条消息生效、追问时作废"
    check("声明了『第 1~10 条全部作废』", "全部作废" in rule)
    check("声明了旧规则只管那条带图的消息", "只管这一条带图片的消息" in config.DEFAULT_PROMPT
          or "带图片的消息" in rule)
    # ② 明确松绑格式（用户原话：「可以不用按照我刚才的模版回答我」）
    check("明确说不用套那五行模板", "五行模板" in rule or "模板" in rule)
    check("给了『想怎么组织就怎么组织』的自由", "想怎么组织就怎么组织" in rule)
    # ③ 禁止拒答 —— 这是用户实际遇到的症状
    check("明令禁止拒绝回答", "不要拒绝" in rule)
    check("点名了『请重新发图』这类推脱话", "请重新发图" in rule)
    # ④ 允许联系上下文
    check("要求结合上文那张图与自己的回答", "结合上文" in rule)

    # ---- 每一套预设都要带（否则用户一套用预设，追问又坏回去）----
    check("预设数量 >= 2", len(config.PROMPT_PRESETS) >= 2,
          f"{len(config.PROMPT_PRESETS)} 套")
    missing = [n for n, t in config.PROMPT_PRESETS.items()
               if not t.endswith(rule)]
    check("每一套预设都带追问规则", not missing, f"缺的：{missing}")
    dup = [n for n, t in config.PROMPT_PRESETS.items()
           if t.count(rule) != 1]
    check("追问规则没有被重复追加两遍", not dup, f"重复的：{dup}")

    # ---- 套用预设之后，保存-读取往返一致 ----
    first = next(iter(config.PROMPT_PRESETS))
    settings.set_prompt(config.PROMPT_PRESETS[first])
    check(f"套用预设「{first}」后能读回来",
          settings.get_prompt() == config.PROMPT_PRESETS[first])
    settings.reset_prompt()
    check("恢复默认后又是内置默认", settings.get_prompt() == config.DEFAULT_PROMPT)


def test_presets() -> None:
    settings.save({"preset_overrides": {}})
    names = [n for n, _ in settings.get_presets()]
    for builtin in config.PROMPT_PRESETS:
        check(f"内置预设还在：{builtin}", builtin in names)

    # 覆盖一个内置预设
    target = "字幕精读（音标 + 词根词缀 + 结构）"
    if target not in names:
        target = "字幕生词（默认）" if "字幕生词（默认）" in names else names[0]
    settings.save_preset(target, "改过的默认预设")
    text = dict(settings.get_presets())[target]
    check("能改内置预设的内容", text == "改过的默认预设")
    settings.delete_preset(target)
    text = dict(settings.get_presets())[target]
    check("删掉内置预设的改动 → 回到内置原文",
          text == config.PROMPT_PRESETS[target])

    # 用户自建预设
    settings.save_preset("我自己的模板", "我的模板正文")
    check("能新增自己的预设", "我自己的模板" in [n for n, _ in settings.get_presets()])
    settings.delete_preset("我自己的模板")
    check("能删掉自己的预设", "我自己的模板" not in [n for n, _ in settings.get_presets()])

    check("空名字/空内容不允许存", settings.save_preset("", "x") is False
          and settings.save_preset("x", "  ") is False)


def test_bad_data() -> None:
    settings.save({"prompt": "   "})
    check("主提示词被清空 → 退回默认", settings.get_prompt() == config.DEFAULT_PROMPT)
    settings.save({"prompt": "x" * (config.PROMPT_MAX_CHARS + 500)})
    check("超长提示词被截断", len(settings.get_prompt()) == config.PROMPT_MAX_CHARS)
    settings.save({"prompt": 12345})
    check("提示词是数字 → 退回默认", settings.get_prompt() == config.DEFAULT_PROMPT)
    settings.save({"preset_overrides": {"a": 1, "": "b", "c": "ok"}})
    check("预设里的坏条目被丢掉", list(settings.load()["preset_overrides"]) == ["c"])

    # ★ 老设置文件里残留的 guard 字段不能再冒出任何行为（网页版已删）
    settings.save({"guard_prompt": "老数据", "guard_enabled": True})
    check("残留的 guard 字段不再被读出来",
          not hasattr(settings, "get_guard_prompt")
          and "guard_prompt" not in settings.load())
    settings.save({"backend": "web"})
    check("残留的 backend 字段不再被读出来",
          not hasattr(settings, "get_backend"))

    settings.reset_prompt()
    settings.save({"preset_overrides": {}})


def main() -> int:
    print(f"设置文件（临时）: {config.SETTINGS_FILE}\n")
    test_main_prompt()
    print()
    test_followup_rule()
    print()
    test_presets()
    print()
    test_bad_data()

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
