"""A/B 验证：**追问能不能用上文、会不会照模板硬套**（2026-09-15 用户反馈）。

用户原话（两轮）：
  1. 「如果我继续去追问他一些问题的话，他直接就拒绝回答了。」
  2. 「可以不用按照我刚才的模版回答我。」

为什么必须真跑一次：这件事的成败完全取决于模型怎么理解提示词，
读代码/读提示词都推不出来。花几次 API 调用换一个确定结论，值。

做法：
  1. 现造一张假字幕截图（不依赖真实播放器）；
  2. 「旧提示词（只有模板）」和「新提示词（模板 + 【追问规则】）」各跑一轮多轮对话：
       第 1 轮：提示词 + 图  → 回答（查词）
       第 2 轮：纯文字追问    → 回答（这一步就是用户说"被拒绝"的那一步）
  3. 把两轮回答都打出来对比。

旧文案不手抄（抄错就白测）：直接从现在的新文案里**切掉【追问规则】那一段**得到。
★ v1.2.2：网页版豆包删除后，「发送时自动追加的约束句（guard）」也没了，
  所以这里不再拼 guard —— 两边的差别**只剩那一段【追问规则】**，更干净。

2026-09-15 实测结论（旧配置）：
  第 2 轮只回了一个字「无」（1 字符）；新配置回 615 字、联系上下文正常作答。

跑法：
    <venv>/bin/python tools/probe_followup_ab.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

SUBTITLE = "I've been meaning to tell you, but I never got around to it."
FOLLOWUP = "你上面写的 get around to，如果我是在跟老板说“这事我尽快处理”，能这么用吗？"

OUT = os.path.join(_ROOT, "tools", "_render", "followup_ab.txt")


def make_fake_shot(path: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    W, H = 960, 300
    im = Image.new("RGB", (W, H), (18, 20, 24))
    d = ImageDraw.Draw(im)
    # 画一点"画面"感，别让整张图纯黑
    d.rectangle([0, 0, W, H - 110], fill=(38, 44, 56))
    font = None
    for cand in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(cand):
            try:
                font = ImageFont.truetype(cand, 30)
                break
            except Exception:                      # noqa: BLE001
                pass
    d.text((W / 2, H - 60), SUBTITLE, fill=(255, 255, 255), font=font, anchor="mm")
    im.save(path, "PNG")
    return path


def run_variant(name: str, prompt: str, shot: str, cfg: dict) -> list[str]:
    import api_client

    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": api_client.encode_image(shot)}},
        ],
    }]
    out = []
    a1 = api_client.chat(msgs, cfg=cfg)
    out.append(a1)
    msgs.append({"role": "assistant", "content": a1})
    msgs.append({"role": "user", "content": FOLLOWUP})
    a2 = api_client.chat(msgs, cfg=cfg)
    out.append(a2)
    return out


def main() -> int:
    import config

    shot = make_fake_shot("/tmp/ab_subtitle.png")

    new_prompt = config.DEFAULT_PROMPT
    rule = config._FOLLOWUP_RULE                       # noqa: SLF001 —— 探针脚本
    old_prompt = new_prompt[: new_prompt.index(rule)].rstrip()
    assert old_prompt != new_prompt and "【追问规则" not in old_prompt, \
        "切分失败，旧文案没造出来"

    import settings

    cfg = settings.provider_config()
    print(f"服务商：{cfg.get('label')}  模型：{cfg.get('model')}")
    print(f"字幕：{SUBTITLE}")
    print(f"追问：{FOLLOWUP}\n")

    lines = [f"服务商：{cfg.get('label')} / {cfg.get('model')}",
             f"字幕：{SUBTITLE}", f"追问：{FOLLOWUP}", ""]
    for name, p in (("旧（只有字段模板，没说追问怎么办）", old_prompt),
                    ("新（模板 + 【追问规则】）", new_prompt)):
        print("=" * 72)
        print(f"### {name}")
        print("=" * 72)
        try:
            a1, a2 = run_variant(name, p, shot, cfg)
        except Exception as exc:                   # noqa: BLE001
            print(f"调用失败：{exc}")
            lines.append(f"### {name}\n调用失败：{exc}\n")
            continue
        print("--- 第 1 轮（带图查词）---")
        print(a1)
        print("\n--- 第 2 轮（纯文字追问）★★ 关键 ---")
        print(f"（{len(a2)} 字符）")
        print(a2)
        print()
        lines.append(f"### {name}\n\n【第 1 轮 带图查词】\n{a1}\n\n"
                     f"【第 2 轮 纯文字追问 · {len(a2)} 字符】\n{a2}\n\n")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"结果已存：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
