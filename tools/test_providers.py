"""服务商配置（config.PROVIDERS + settings 引擎接口）的离线测试。

**不联网、不碰真实 settings.json** —— 把 DL_SETTINGS_DIR 指到临时目录，
跑完即弃，免得把用户真机上的设置改坏。

跑法：
    python tools/test_providers.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="test_providers_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import settings  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


print("== 1. 服务商表本身 ==")
check("服务商齐全（含新增的常用来源）", sorted(config.PROVIDERS),
      ["custom", "deepseek", "doubao", "hunyuan", "moonshot", "openai",
       "openrouter", "qwen", "siliconflow", "zhipu"])
check("默认服务商存在", config.DEFAULT_PROVIDER in config.PROVIDERS, True)

# ★ 关思考参数只能加在**实测验证过**的服务商上 —— 没验证的发出去可能被严格网关 400。
#   这条断言的作用是拦住"顺手给所有服务商都加上"的冲动。
check("只给验证过的服务商配 no_think",
      sorted(p for p, s in config.PROVIDERS.items() if s.get("no_think")),
      ["deepseek", "doubao"])
check("no_think 形状是 thinking.type=disabled",
      config.PROVIDERS["doubao"]["no_think"],
      {"thinking": {"type": "disabled"}})

for pid, spec in config.PROVIDERS.items():
    if pid == "custom":
        continue
    check(f"{pid} 有 base_url", bool(spec["base_url"]), True)
    check(f"{pid} 有默认模型", bool(spec["default_model"]), True)
    # ★ 视觉硬前提：默认模型必须自己就在视觉名单里，否则开箱即用是哑的
    check(f"{pid} 默认模型支持视觉",
          spec["default_model"] in spec["vision_models"], True)
    check(f"{pid} 视觉名单非空", len(spec["vision_models"]) > 0, True)

print("\n== 2. ★ 只剩一条查词通道（网页版豆包已整体删除） ==")
# 这几条是**反向回归钉子**：网页版那一套（Playwright + 扫码登录 + 抓页面）
# 在 v1.2.2 被整体删掉，用户的原话是「把我这个软件的网页版豆包提取回答这个
# 模块彻底删除，以后我只用 api 查词了」。谁哪天又加回来，这里立刻红。
check("config 里没有 BACKEND 开关", hasattr(config, "BACKEND"), False)
check("config 里没有后端标签表", hasattr(config, "BACKEND_LABELS"), False)
check("config 里没有浏览器配置目录", hasattr(config, "USER_DATA_DIR"), False)
check("settings 里没有 get_backend", hasattr(settings, "get_backend"), False)
check("settings 里没有 set_backend", hasattr(settings, "set_backend"), False)
check("settings 里没有约束句读写（guard）",
      hasattr(settings, "get_guard_prompt"), False)
check("settings 读到的服务商", settings.get_provider(), config.DEFAULT_PROVIDER)

print("\n== 3. provider_config 生效配置 ==")
cfg = settings.provider_config()
check("五个关键键齐全",
      all(k in cfg for k in ("label", "base_url", "model", "key", "vision")), True)
check("默认模型是 flash", cfg["model"], "deepseek-flash")
check("flash 判定为视觉", cfg["vision"], True)
check("初始没 key", cfg["key"], "")
check("没 key 时 provider_ready 为假", settings.provider_ready()[0], False)
check("且说清了原因", "API Key" in settings.provider_ready()[1], True)

print("\n== 4. 写 key / 模型 / base_url 并各自隔离 ==")
settings.set_api_key("sk-deepseek-AAAAAAAAAAAA")
settings.set_provider("hunyuan")
check("混元没 key", settings.get_api_key(), "")
settings.set_api_key("sk-hunyuan-BBBBBBBBBBBB")
check("混元 key 写进去了", settings.get_api_key(), "sk-hunyuan-BBBBBBBBBBBB")
settings.set_provider("deepseek")
check("deepseek 的 key 没被覆盖", settings.get_api_key(), "sk-deepseek-AAAAAAAAAAAA")
check("provider_ready 通过", settings.provider_ready(), (True, ""))

settings.set_model("deepseek-v4-pro")
check("换纯文本模型后 vision=False", settings.provider_config()["vision"], False)
check("provider_ready 仍为 True（视觉要真发图才知道）",
      settings.provider_ready()[0], True)
settings.set_model("")
check("清空模型回到默认", settings.get_model(), "deepseek-flash")

settings.set_provider("custom")
check("custom 默认没 base_url", settings.get_base_url(), "")
check("custom 没 url 时不可用", settings.provider_ready()[0], False)
settings.set_base_url("https://example.com/v1/")
check("尾部斜杠被去掉", settings.get_base_url(), "https://example.com/v1")
check("custom 现在可用（key 是空的，仍应为 False）",
      settings.provider_ready()[0], False)
settings.set_api_key("sk-custom-CCCCCCCCCCCC")
# custom 没有内置默认模型，模型也得用户自己填 —— 这是刻意的，
# 随便塞个默认值反而会让人以为填了 url + key 就能用。
check("custom 没模型时仍不可用", settings.provider_ready()[0], False)
check("  且说清是缺模型", "模型" in settings.provider_ready()[1], True)
settings.set_model("my-vision-model")
check("补齐模型后可用", settings.provider_ready(), (True, ""))

print("\n== 5. 非法输入被挡住 ==")
check("非法服务商被拒", settings.set_provider("nope"), False)
check("服务商没变", settings.get_provider(), "custom")

print("\n== 6. mask_key ==")
check("正常 key 掩码（前 6 后 4）", settings.mask_key("sk-abcdefgh12345678"), "sk-abc…5678")
# ⚠️ 这里只许用**假 key**。本仓库是公开的，任何真实 key 一律不许写进源码或测试
#    （2026-09-15 发布前扫描时，抓到过一次真实 key 被当测试样本写死在这里）。
check("32 位 hex 长度的 key", settings.mask_key("sk-0123456789abcdef0123456789abcdef"),
      "sk-012…cdef")
check("短 key 全打码", settings.mask_key("abc"), "•••")
check("空 key", settings.mask_key(""), "")

print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
sys.exit(0 if not FAILED else 1)
