"""全局配置 —— 所有可调参数集中在这里，改这一个文件即可。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- 应用标识
APP_NAME = "SubtitleLookup"
APP_DISPLAY_NAME = "看剧查词-美剧"
BUNDLE_ID = "com.zhaowentian.subtitlelookup"
VERSION = "1.2.3"

# ---------------------------------------------------------------- 查词引擎
# ★ v1.2.2：**只剩一条通道 —— 直连大模型 API**。
#
#   以前还有"网页版豆包"这条路（Playwright 驱动 doubao.com、抓页面里的回答）。
#   它在 2026-09-15 被**整体删除**，用户的原话：
#     「把我这个软件的网页版豆包提取回答这个模块彻底删除，以后我只用 api 查词了。」
#
#   删它的三个实际原因（翻旧账时别再往回加）：
#     · 慢：一次 20~75 秒；API 端到端只要 1~2 秒。
#     · 脆：要维持扫码登录态，豆包前端一改版就抓不到回答，全是不可控的维护。
#     · 脏：登录态 / cookie 续期 / 对话复用 / 页面选择器加起来两千多行，
#           而它唯一的收益只是"免费"。
#
#   现在截图直接 base64 塞进 messages，一次请求搞定：无需浏览器、无需登录、
#   不怕服务商改版。实测 deepseek-flash 端到端约 1 秒（关掉思考后；
#   见 PROVIDERS["deepseek"]["no_think"]）。

# ---------------------------------------------------------------- 服务商
# 全部走 **OpenAI 兼容** 接口（POST {base_url}/chat/completions），
# 所以只需要记住 base_url + 模型名两件事，加新服务商不用写代码。
#
# ⚠️ 关键约束：本程序**依赖图片输入**，所选模型必须支持视觉（多模态）。
#    实测（2026-09-15）：
#      · deepseek-flash      ✅ 能读字幕并解释
#      · deepseek-v4-pro     ❌ "图片格式不受支持，请把字幕以文字形式发我"
#    所以 deepseek 的默认模型是 flash 而不是 pro —— 别想当然换成 pro。
#    设置界面里的「测试连通性」会真的发一张带文字的图过去，
#    直接告诉你这个 key + 模型到底能不能看图，避免用起来才发现是哑的。
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek（深度求索）",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "key_hint": "sk-…（platform.deepseek.com → API keys）",
        "vision_models": ["deepseek-flash"],
        # ★ 关掉"思考"。
        # deepseek-flash 是思考型模型，实测查一次词会在 reasoning_content 上
        # 写 2500~9500 字，端到端 6~33 秒，还经常把 max_tokens 吃光导致正文为空。
        # 而查词这个任务（照抄字幕 + 查词义 + 套格式）根本不需要推理，
        # 实测加上这个参数后思考降到 0 字、耗时 1~2 秒，回答质量不变。
        # 注意：不同服务商对"思考"的开关名不一样，所以做成按服务商配置，
        # 只有实测验证过的才填 —— 没验证的服务商宁可不发，免得被严格网关 400。
        "no_think": {"thinking": {"type": "disabled"}},
    },
    "doubao": {
        # 豆包 = 字节的模型，API 走火山引擎「方舟」（Ark）平台。
        # ⚠️ 方舟不是"拿了 Key 就能调所有模型"：每个模型要在控制台
        #    「开通管理」里**单独开通**，没开通的会报 ModelNotOpen / 404，
        #    看起来像"模型不存在"，其实是没开通 —— 这是最常见的踩坑点。
        "label": "豆包（火山方舟 Ark）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seed-1-6-flash-250828",
        "models": [
            "doubao-seed-1-6-flash-250828",
            "doubao-seed-1-6-251015",
            "doubao-seed-1-6-vision-250815",
            "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-pro-260215",
            "doubao-1-5-vision-pro-32k-250115",
        ],
        "key_hint": "火山方舟控制台 → API Key 管理（形如 3f2a…；模型要逐个开通）",
        "vision_models": [
            "doubao-seed-1-6-flash-250828",
            "doubao-seed-1-6-251015",
            "doubao-seed-1-6-vision-250815",
            "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-pro-260215",
            "doubao-1-5-vision-pro-32k-250115",
        ],
        # 方舟也支持关思考（官方文档：extra_body 里 thinking.type = disabled），
        # 参数形状和 DeepSeek 一致，实测同样能把手写推理压到 0 字。
        "no_think": {"thinking": {"type": "disabled"}},
    },
    "hunyuan": {
        "label": "腾讯混元",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "default_model": "hunyuan-turbos-vision",
        "models": ["hunyuan-turbos-vision", "hunyuan-vision", "hunyuan-turbos-latest"],
        "key_hint": "腾讯云控制台 → 混元大模型 → API Key",
        "vision_models": ["hunyuan-turbos-vision", "hunyuan-vision"],
    },
    "qwen": {
        # 阿里云百炼（DashScope）的 **OpenAI 兼容模式** —— 注意路径里必须带
        # compatible-mode，走原生 DashScope 接口的话请求格式不是 OpenAI 那套。
        "label": "阿里通义千问（百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-vl-max",
        "models": ["qwen-vl-max", "qwen-vl-plus", "qwen3-vl-plus", "qwen-vl-max-latest"],
        "key_hint": "sk-…（bailian.console.aliyun.com → API-KEY 管理）",
        "vision_models": ["qwen-vl-max", "qwen-vl-plus", "qwen3-vl-plus",
                          "qwen-vl-max-latest"],
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4v-plus",
        "models": ["glm-4v-plus", "glm-4v", "glm-4v-flash", "glm-4.5v"],
        "key_hint": "（bigmodel.cn → API Keys，形如 id.secret）",
        "vision_models": ["glm-4v-plus", "glm-4v", "glm-4v-flash", "glm-4.5v"],
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k-vision-preview",
        "models": [
            "moonshot-v1-8k-vision-preview",
            "moonshot-v1-32k-vision-preview",
            "moonshot-v1-128k-vision-preview",
            "kimi-latest",
        ],
        "key_hint": "sk-…（platform.moonshot.cn → API Key 管理）",
        "vision_models": [
            "moonshot-v1-8k-vision-preview",
            "moonshot-v1-32k-vision-preview",
            "moonshot-v1-128k-vision-preview",
            "kimi-latest",
        ],
    },
    "openrouter": {
        # 聚合平台：一个 key 调各家模型。好处是模型最多（含不少免费的），
        # 代价是比直连贵 5~10%。
        # ★ 它的 /models 接口会返回 architecture.input_modalities，
        #   能**准确**筛出支持图片的模型 —— 所以面板上给了「拉取模型列表」按钮，
        #   拉到的新模型会自动进候选（见 api_client.list_models）。
        # ⚠️ 别用 `openrouter/auto`：它会自己挑模型，可能挑到不支持图片的，
        #   图片被静默丢掉，查词就哑了。
        "label": "OpenRouter（聚合，含免费模型）",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "google/gemini-2.5-flash",
        "models": [
            "google/gemini-2.5-flash",
            "google/gemini-2.5-flash-lite",
            "google/gemma-3-27b-it:free",
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
            "qwen/qwen3-vl-235b-a22b-instruct",
            "meta-llama/llama-4-scout",
        ],
        "key_hint": "sk-or-v1-…（openrouter.ai/keys）",
        "vision_models": [
            "google/gemini-2.5-flash",
            "google/gemini-2.5-flash-lite",
            "google/gemma-3-27b-it:free",
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
            "qwen/qwen3-vl-235b-a22b-instruct",
            "meta-llama/llama-4-scout",
        ],
    },
    "openai": {
        "label": "OpenAI（官方）",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        "key_hint": "sk-…（platform.openai.com → API keys；国内需自备网络）",
        "vision_models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
    },
    "siliconflow": {
        "label": "硅基流动（SiliconFlow）",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-VL-72B-Instruct",
        "models": [
            "Qwen/Qwen2.5-VL-72B-Instruct",
            "Qwen/Qwen3-VL-235B-A22B-Instruct",
            "deepseek-ai/DeepSeek-V3",
        ],
        "key_hint": "sk-…（cloud.siliconflow.cn → API 密钥）",
        "vision_models": [
            "Qwen/Qwen2.5-VL-72B-Instruct",
            "Qwen/Qwen3-VL-235B-A22B-Instruct",
        ],
    },
    "custom": {
        "label": "自定义（任意 OpenAI 兼容端点）",
        "base_url": "",
        "default_model": "",
        "models": [],
        "key_hint": "填服务商给的 key",
        "vision_models": [],
    },
}
DEFAULT_PROVIDER = "deepseek"

# ---------------------------------------------------- 「拉取模型列表」用的启发式
# 大部分服务商没有"这个模型支不支持图片"的机器可读字段，只能靠名字猜；
# 猜错的代价只是候选列表里多几个不能用的，用户手填仍可覆盖，所以够用。
# OpenRouter 是例外 —— 它的 /models 会明确给出 input_modalities，优先用它。
VISION_NAME_KEYWORDS = (
    "vl", "vision", "gpt-4o", "gpt-4.1", "gpt-5", "o4", "gemini", "claude",
    "glm-4v", "glm-4.5v", "pixtral", "llama-4", "llama-3.2", "qwen-vl",
    "internvl", "minicpm-v", "doubao-seed", "hunyuan-vision", "turbos-vision",
    "multimodal", "-v-",
)
# 明确**不支持**图片的，直接踢出候选（避免用户在长长的列表里踩雷）
NON_VISION_NAME_KEYWORDS = (
    "embedding", "rerank", "tts", "whisper", "audio", "speech", "moderation",
    "dall-e", "image-1", "stable-diffusion", "flux", "sd3", "codex", "coder",
)
# 一次拉取最多保留多少个候选（面板下拉框塞太多会很难用）
MODELS_FETCH_LIMIT = 60


# ---------------------------------------------------------------- 目录
HOME = Path.home()

# 打包成 .app 后 __file__ 位于只读 bundle 内，临时文件一律走系统临时目录
TMP_DIR = Path(tempfile.gettempdir()) / "subtitle_lookup"
TMP_DIR.mkdir(parents=True, exist_ok=True)
# 日志放 ~/Library/Logs（App 无控制台，这里是唯一能翻到的地方）
LOG_DIR = Path(os.environ.get("DL_LOG_DIR", HOME / "Library" / "Logs" / APP_NAME)).expanduser()
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# ---------------------------------------------------------------- 状态栏后端
# "qt"    : QSystemTrayIcon 菜单栏 + Qt 悬浮窗，**同一个主线程** —— 默认，最稳。
# "rumps" : rumps 菜单栏。注意：macOS 强制要求 QApplication 在主线程创建，
#           而 rumps 也占着主线程，两者无法共存（实测 Qt 会抛 NSException 崩溃）。
#           所以只有在把悬浮窗换成别的实现时才用得上；否则别开。
MENUBAR_BACKEND = os.environ.get("DL_MENUBAR", "qt").strip().lower()

# ---------------------------------------------------------------- 触发器
# 触发键：space(空格，默认) / n / m / enter(回车) / 'f13' 等
#
# 触发键演进：空格 → M → N → **空格**（2026-09-15 定稿，用户最终选择）
#   · 空格（最初）：多数播放器空格=暂停/继续，每查一次词视频就被暂停一次。
#     不过 2026-09-15 用户明确要求改回空格 —— 对看剧来说这个副作用其实是
#     **加分项**：按空格查词 = 顺手把画面停住，正好看清字幕。
#   · M：MPV / IINA / VLC / 网页播放器的 M 都是**静音**开关，
#     按一次查词就把声音切掉 —— 用户明确反馈「m 键不好用」。
#   · N：主流播放器基本没绑定，副作用最小，但用户觉得不顺手。
#
# 【空格现在是"一个键三态"，靠状态机区分】（2026-09-15）
#   浮窗不在 + 视频在播 → 按空格 = 暂停画面 + 截图查词 + 弹浮窗
#   浮窗正显示         → 按空格 = 关掉浮窗 + 继续播放
#   浮窗不在 + 视频已停 → 按空格 = 继续播放
# 播放器由我们主动控制（player.py：IINA mpv IPC / 系统媒体键），
# 不再依赖"空格顺带落到播放器上"。空格在有需要时会被**吃掉**（keytap.py），
# 但只在"浮窗显示中 / 前台是播放器"时才吃，打字时照旧。
# 想"关掉浮窗但先别播" → 用**点浮窗外面**（mouse.py），那条路不碰播放状态。
TRIGGER_KEY = os.environ.get("DL_TRIGGER_KEY", "space").strip().lower()
# 界面上给人看的名字（通知/悬浮窗/面板里的提示文案统一用它）
TRIGGER_LABEL = {
    "space": "空格",
    "空格": "空格",
    "enter": "回车",
    "return": "回车",
    "esc": "Esc",
}.get(TRIGGER_KEY, TRIGGER_KEY.upper())
# 同一个键的连击去抖（秒），防止长按或手抖重复触发
DEBOUNCE_SEC = 0.35

# ---------------------------------------------------------------- 关闭键
# 悬浮窗显示时，按**空格**直接关掉它（用户明确要求：空格最顺手）。
#
# 【2026-09-15 起：空格升级成"三态交互"，本块是它的总开关】
# 用户要的手感是——空格是**唯一**的交互键，全程不用碰鼠标：
#     ① 视频在播   → 按空格：暂停、截图查词、弹浮窗
#     ② 浮窗在显示 → 按空格：关掉浮窗、继续播放
#     ③ 视频已停   → 按空格：继续播放
# 播放器不再靠"空格顺带落到它身上"，而是我们自己主动控制（见 player.py）——
# 这样和"焦点在谁手里"彻底解耦（老方案就死在浮窗抢焦点上）。
#
# 由此带来两条硬约束：
#   1) 这一下空格必须**只处理一次**，所以要把它**吃掉**（见 keytap.py）。
#      但只"有条件地吃"：浮窗显示时、或前台是已知播放器时才吃，
#      在 Word / Slack 里打字时绝不干预 —— 否则用户打不出空格。
#   2) 想"关掉但先别播"，就用**点浮窗外面**（见 mouse.py）——
#      那条路完全不碰播放器状态。
CLOSE_KEY_ENABLED = True
CLOSE_KEY = os.environ.get("DL_CLOSE_KEY", "space").strip().lower()
CLOSE_KEY_LABEL = {"space": "空格", "enter": "回车", "esc": "Esc"}.get(
    CLOSE_KEY, CLOSE_KEY.upper()
)
# 用 CGEventTap 做"有条件吞键"。关掉它 = 退回老行为（空格不吞、靠播放器
# 自己响应）—— 那时浮窗一显示，空格就再也到不了播放器，等于回到老 bug。
# 只有在没给辅助功能权限 / pyobjc 缺失时才需要关它。
SPACE_TAP_ENABLED = os.environ.get("DL_SPACE_TAP", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# ---------------------------------------------------------------- 截图
# 【直接决定查一次词要等多久】
# 实测（2026-09-14）：整屏 1440×932 的 PNG 丢给模型，它要 80+ 秒才吭声 ——
# 得先对整幅图做 OCR；而只有一行字幕的小图 10 秒就答完。所以截完先做三件事：
#   1) 裁掉上方的播放器 UI / 标题栏，只留字幕常出现的下方区域；
#   2) 等比缩到 MAX_IMAGE_WIDTH（文字仍清晰，OCR 快很多）；
#   3) 存 JPEG（同内容比 PNG 小好几倍，上传也快）。
CROP_TOP_RATIO = 0.40        # 只保留从屏幕高度 40% 往下的部分（字幕区）
MAX_IMAGE_WIDTH = 1280
IMAGE_JPEG_QUALITY = 88      # 88 足够保住字幕笔画，体积远小于无损 PNG
# 是否把截图保留在临时目录（False = 用完立即删除）
KEEP_SCREENSHOTS = False

# ---------------------------------------------------------------- 截图范围
# 用户可以自己选"每次截屏幕的哪一块"（设置面板里的下拉框）。
# 为什么值得做：模型拿到整屏大图要先做全图 OCR，实测 80+ 秒才开口；
# 只有字幕那一小条的话 10 秒就答完 —— 截得越准，出结果越快。
CAPTURE_MODES = {
    "subtitle":  "字幕区（下方 60%）",   # 默认：字幕几乎都在这里
    "fullscreen": "整块屏幕",
    "bottom":    "下半屏",
    "top":       "上半屏",
    "custom":    "自定义区域…",          # 自己拖一个框，位置会记住
}
DEFAULT_CAPTURE_MODE = "subtitle"
# 每种模式保留「屏幕高度的哪一段」：(上边界比例, 下边界比例)
CAPTURE_BANDS = {
    "fullscreen": (0.0, 1.0),
    "subtitle": (0.40, 1.0),     # 与 CROP_TOP_RATIO 保持一致
    "bottom": (0.5, 1.0),
    "top": (0.0, 0.5),
}
# 自定义区域：按屏幕**比例**存（换分辨率/换显示器也不会跑偏）
# 形如 [x, y, w, h]，取值 0~1
DEFAULT_CAPTURE_RECT: list[float] = [0.05, 0.75, 0.90, 0.22]

# ---------------------------------------------------------------- 追问（浮窗输入框）
# 浮窗底部的输入框：点一下就能打字继续问，回车发送，回答追加显示在同一个浮窗里。
#
# ★ v1.2.2 起，追问走的是**同一条 API 对话历史**（app._api_messages）：
#   新查一次词会重置成 [提示词+这张图]，之后你手打的每一问都追加在这条历史后面，
#   所以模型看得到刚才那张截图和它自己的解释 —— 这正是追问能"联系上下文"的原因。
#
# 另外：主提示词里专门写了一段【追问规则】声明"纯文字追问不受字段模板约束"，
# 因为提示词本身也常驻在这条历史里，不显式划清作用域的话，模型会把
# "只输出那五行"当成对整个对话都生效，于是就出现用户反馈的"追问被拒绝回答"。
OVERLAY_INPUT_ENABLED = True
OVERLAY_INPUT_H = 46            # 输入行占用高度 px
OVERLAY_INPUT_PLACEHOLDER = "点这里打字，继续问…（回车发送）"
OVERLAY_INPUT_BTN = "发送"

# ---------------------------------------------------------------- API 直连
# 直连大模型 API 的超时（秒）。实测 deepseek-flash 端到端约 1 秒，
# 留出网络抖动与排队余量给到 45 秒；再久就该报错让用户重试。
API_TIMEOUT_SEC = 45
# 单次回答的最大 token 数。
#
# ⚠️ 别按"解释几个生词能写多长"来估这个值 —— 思考型模型（deepseek-flash 就是）
# 先把 token 花在 reasoning_content 上，正文才开始写。实测 1200 时会出现
# **整段预算被思考吃光、content 为空**的情况（用户侧表现就是浮窗空白），
# 而且这个失败是概率性的：同样的图同一次会话，上一秒成功、下一秒就空。
# 所以这里给到明显宽裕的值 —— 它只是个上限，模型写完就停，正常情况不会变慢。
API_MAX_TOKENS = 3000
# chat() 发现"空正文且被截断"时，会把预算翻倍重试一次；这是重试的天花板。
API_MAX_TOKENS_CEILING = 9000
# 「测试连通性」用的超时（要真的发一张图过去，给宽一点）
API_TEST_TIMEOUT_SEC = 60

# ---------------------------------------------------------------- 播放器控制
# 「按空格顺便控制播放/暂停」—— 让查词时画面自动停住，关掉浮窗后自动继续播。
#
# 【为什么需要这一整块】
# 以前的做法是"不吞键，让真实的空格自己送到播放器"。这依赖一个前提：
# **我们的 App 不能抢走前台**。一旦抢走（曾经的 raise_() bug），空格就再也
# 到不了播放器，用户必须先鼠标点一下播放器 —— 极其烦人。
# 现在改成"我们自己主动去控制播放器"，于是**和焦点彻底解耦**。
#
# 两条通道，从精确到万能降级（见 player.py）：
#   ① IINA → mpv JSON IPC（UDS socket）：能读真实状态、能精确设置
#   ② 兜底 → 合成系统媒体键（NX_KEYTYPE_PLAY）：不受焦点影响，通吃，
#            但只能"切换"不能"设定"
#
# ★ v1.2.0：浏览器的在线视频通道（网页视频桥 + Chrome AppleScript/JS）
#   已整体删除 —— 用户只用 IINA 看片。
PLAYER_CONTROL = os.environ.get("DL_PLAYER_CONTROL", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# IINA 的 mpv IPC socket 路径。
# 需要在 IINA → 设置 → 高级 → 勾「启用高级设置」→ 附加 mpv 选项 里加一条：
#     input-ipc-server = /tmp/iina.sock
# 然后重启 IINA。（本机实测 IINA 1.4.3 支持；该选项存在偏好文件的
# userOptions 数组里，形状是 [[名称, 值], …]，可以程序化写入。）
IINA_SOCKET_PATH = os.environ.get("DL_IINA_SOCKET", "/tmp/iina.sock")
# 发一条 mpv 命令的超时（秒）。本地 UDS，正常是亚毫秒级。
PLAYER_CMD_TIMEOUT = 1.0

# 媒体键兜底。关掉它的话，只有在 IINA IPC 可用时才会去控制播放。
MEDIA_KEY_FALLBACK = True

# 暂停之后、截图之前要等多久（秒）。
# 播放器收到暂停指令到"画面真的停住并重绘完"之间有延迟，截太早会截到下一帧。
PLAYER_PAUSE_SETTLE_SEC = 0.30
# 恢复播放前是否也稍等一下（一般不用，留个口子方便调）
PLAYER_RESUME_SETTLE_SEC = 0.0

# 空格"看门狗"超时（秒）。
# 空格已经被我们吞掉了，如果这么久还没真的做成一件事（暂停/继续/查词），
# 就补发一次原生空格把控制权还给播放器 —— 宁可多一次，不可彻底失灵。
# 别调太小：查词链路本身要几百毫秒，误触发会造成"暂停完又播回去"。
SPACE_FALLBACK_SEC = float(os.environ.get("DL_SPACE_FALLBACK_SEC", "2.5"))

# 已知播放器的 bundle id —— 用"前台是谁"来判定这一下空格该不该由我们接管。
#
# ★ v1.2.0：浏览器（Chrome / Edge / Arc…）**不在**这张表里了。
#   本程序不再支持在浏览器里控制在线视频（那条链路整体删除），
#   所以前台是浏览器时，空格原样留给浏览器自己处理。
#   "iina" 走 mpv IPC（精确）；"generic" 只能靠系统媒体键切换（读不到状态）。
PLAYER_BUNDLE_IDS = {
    "com.colliderli.iina": "iina",
    "org.videolan.vlc": "generic",
    "com.apple.QuickTimePlayerX": "generic",
}


# ---------------------------------------------------------------- 用户设置
# 用户可编辑的配置（目前主要是提示词）单独存一份 JSON，改它不需要重新打包。
# 放 Application Support 而不是 bundle 内（bundle 是只读的）。
#
# ⚠️ 环境变量要判"空字符串"而不是只看有没有设置：
#    `os.environ.get("DL_SETTINGS_DIR", 默认)` 在变量被设成空串时**会返回空串**，
#    于是 Path("") 变成当前目录 —— 设置会被写到 `./settings.json`，
#    看起来"写成功了"，实际上用户真正的设置一个字节都没动。
#    （2026-09-15 实测踩到：`DL_SETTINGS_DIR= python - <<EOF` 就是这么翻的车。）
_SETTINGS_ENV = (os.environ.get("DL_SETTINGS_DIR") or "").strip()
SETTINGS_DIR = Path(
    _SETTINGS_ENV or (HOME / "Library" / "Application Support" / APP_NAME)
).expanduser()
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# ---------------------------------------------------------------- 提示词
# 这是**默认**提示词（用户可在 App 面板里点「✏️ 提示词」随时改，改完立即生效，
# 存在 SETTINGS_FILE 里）。
#
# ★★ v1.2.1：整段重写，目标只有一个 —— **输出格式稳定**。
#    2026-09-15 用户反馈：「目前的提示词输出不稳定，有时候这个格式有时候那个格式」。
#    旧写法的毛病：只给了"格式为 生词 —— 词性 / 中文释义"这种**口头描述**，
#    模型自由发挥的空间很大 —— 一会儿用「——」一会儿用「:」，
#    一会儿加编号一会儿加粗，一会儿把两条合成一行。
#
#    新写法的三个要点（改这段时请保留）：
#      · **给出逐字的模板**（"照这个写"永远比"格式为…"稳定）；
#      · **字段名固定在行首 + 中文全角冒号** —— 行首有固定标签，
#        解析就永远有锚点，渲染端也才能稳定地做成醒目版式；
#      · **明确列出禁止项**（编号 / markdown / 表格 / 寒暄 / 追问……）。
#        模型对"不要做什么"比"要做什么"更敏感。
#    ★ 2026-09-15 二次修订：用户**实际用的是存在 settings.json 里的自定义提示词**
#      （不是这个默认值）—— 所以光改这里对他没用。已把他的自定义提示词
#      按同一套固定模板重写并存回去（原文件备份在 backup/）。
#      他原来那版的三个不稳定源，都在新模板里被消掉了：
#        · 「句子—— 英文（中文）」靠破折号+括号 → 改成固定标签的两行；
#        · 「生词 —— 音标/中文释义/词根词缀」用斜杠挤在一行 → 改成
#          `生词：<词> /<音标>/ <词性> <释义>` 的固定位次；
#        · 「头部用蓝色显示」—— 模型根本做不到上色，只会让它自由发挥 →
#          删掉，改由渲染层自动上色（见 overlay.format_lines）。
# ---------------------------------------------------------------- 追问规则
# ★★ 2026-09-15 用户实测反馈："追问直接被拒绝回答"；
#    同日二次修订，用户补充："可以不用按照我刚才的模版回答我"。
#
#   根因不是模型不会聊天，而是**这套规则是「常驻在对话历史里的指令」**：
#   为了支持追问，每次查词的那段提示词 + 那张图 + 模型的回答都会留在
#   app._api_messages 里，追问时整条历史一起发出去。于是模型把
#   「只允许这五种开头的行」「除了这些行一个字都不要多写」「没有内容就回『无』」
#   理解成**对整个对话都生效的规矩** —— 一收到纯文字追问，它既没有新图可看、
#   又不敢越出模板，最省事的做法就是拒答或回一个「无」。
#
#   解法：在提示词里**显式划清作用域**，并声明冲突时以这一段为准。
#   两个要点缺一不可：
#     ① 说清楚"不带图片的纯文字消息 = 追问"，第 1~10 条对它**全部作废**；
#     ② **明确给格式松绑**。用户原话是"可以不用按照我刚才的模版回答我"，
#        所以直说"想怎么组织就怎么组织"。不写这句的话，模型仍会努力往那五行
#        模板上凑，结果是既不像解释、也不像模板，反而更难读。
#
#   ★ 这段是**所有预设共用**的（见文件末尾对 PROMPT_PRESETS 的处理）——
#     否则用户一旦套用别的预设，追问又会退回"被模板挡住"的老毛病。
_FOLLOWUP_RULE = (
    "\n"
    "【追问规则 —— 优先级最高，和上面任何一条冲突时，一律以这一段为准】\n"
    "我看到上面这张图之后，很可能再发一条**不带图片的纯文字**消息继续问你，"
    "比如「这个词还有别的意思吗」「这句为什么用现在完成时」「换成书面语怎么说」。"
    "那叫「追问」，不是新一轮看图查词。遇到追问时：\n"
    "· 上面第 1~10 条**全部作废**。特别是不必再套「句子/译文/生词/俚语/结构」"
    "那五行模板 —— 你想怎么组织就怎么组织：该分点就分点，该列表就列表，"
    "该写几段就写几段，一句话能说清就只写一句话。格式完全由问题决定。\n"
    "· 结合上文那张图的字幕、以及你自己刚才那条回答来回答我。"
    "可以引用、可以对比、可以顺着往下讲，也可以纠正你自己刚才说得不准的地方。\n"
    "· 用自然的中文口语回答，想讲多细就讲多细；"
    "回答里出现的英文单词和句子照原样写出来就行（浮窗会自动高亮）。\n"
    "· 绝对不要拒绝，也绝对不要说「我只能分析图片」「无法查看之前的图片」"
    "「请重新发图」「请把字幕发我」这类话 —— 你看得到上面的图片和你自己的回答，"
    "直接用就是了。\n"
    "· 如果我问的跟刚才那句字幕关系不大（换个词、换个语法点、甚至闲聊），"
    "也照常回答，不要拿「这超出了字幕范围」来推脱。"
)

# 主提示词 = 「每次看图查词」的固定模板 + 上面那段追问规则。
# ★ 最后一行**不要**带 "\n"：settings.set_prompt() 会 strip()，
#   带换行的话"存进去又读出来"就和 DEFAULT_PROMPT 不相等了 ——
#   panel 上会一直显示"已自定义"，tools/test_prompt_editable.py 也会红。
DEFAULT_PROMPT = (
    "你是美剧字幕精读助手，服务对象是大学英语六级水平、想靠看剧记单词的成年人。\n"
    "看图里的字幕，只处理图中直接出现的内容，绝不发散解释图里没有的词。\n"
    "\n"
    "严格按下面的模板输出，字段名和冒号必须逐字照抄"
    "（冒号用中文全角「：」，冒号后不要空格）：\n"
    "\n"
    "句子：<英文原句，一字不改地照抄字幕>\n"
    "译文：<这句话自然的中文翻译>\n"
    "生词：<单词> /<音标>/ <词性> <中文释义>（结合本句语境的一句话说明）"
    "词根词缀：<拆解>，即词根<含义>+词缀<含义>\n"
    "俚语：<表达> → <中文实际含义>\n"
    "结构：<仅当这句话有省略、倒装或复杂从句时，用一句话讲清句子结构>\n"
    "\n"
    "规则：\n"
    "1. 只允许出现「句子：」「译文：」「生词：」「俚语：」「结构：」"
    "这五种开头的行，冒号必须是中文全角「：」。\n"
    "2. 「句子：」和「译文：」各只写一行，必须是第 1 行和第 2 行。\n"
    "3. 「生词：」最多 8 行，按单词在字幕中出现的先后顺序排列。"
    "六级水平一看就懂的词（如 the、time、really）一律不要写；"
    "词性只能用这几个缩写：n. v. adj. adv. prep. conj. int. phr.。"
    "看不出词根词缀时，「词根词缀：」那一小段整个省略。\n"
    "4. 「俚语：」最多 4 行，只写真正的俚语、固定搭配和口语缩略，"
    "并且必须用「 → 」把表达和实际含义连起来。\n"
    "5. 「结构：」最多 1 行，只在句子确实有难度时才写；"
    "没什么可讲的就把这一行整个省掉。\n"
    "6. 如果整句既没有生词、没有俚语、句子结构也没什么可讲的，"
    "就只回复两个字：无。\n"
    "7. 不要编号（1. 一、 ① ）、不要 markdown（** # - | ` ）、不要表格、"
    "不要表情符号、不要在行内用括号补充说明。\n"
    "8. 不要复述我的问题，不要寒暄、开场白、总结、免责声明，"
    "也不要问我「要不要展开」。\n"
    "9. 不要在内容里写「用蓝色显示」「加粗」「醒目」这类话 —— "
    "浮窗会自动给每一类配上颜色和底色。\n"
    "10. 除了上面这些行，一个字都不要多写。\n"
    "★ 上面第 1~10 条**只管这一条带图片的消息**。"
    "如果我之后发的是不带图片的问题，请按下面那段【追问规则】来回答。"
) + _FOLLOWUP_RULE
# 兼容旧名字（旧代码里引用的是 STRICT_PROMPT）
STRICT_PROMPT = DEFAULT_PROMPT

# 面板里「恢复默认」之外还提供的几套预设（用户可直接套用再微调）
#
# ★ 每个预设都沿用同一套「行首固定标签 + 中文全角冒号」的写法。
#   这是格式稳定的关键：只要行首有固定标签，渲染端就能稳定地做出统一版式，
#   模型也不会在"用 —— 还是用 :"这种事情上自由发挥。
PROMPT_PRESETS: dict[str, str] = {
    "字幕生词（默认）": DEFAULT_PROMPT,
    "口语 + 俚语详解": (
        "你是美剧口语教练。看图里的字幕，只讲图中直接出现的口语表达、俚语和固定搭配，"
        "不要解释其它没出现的词。\n"
        "\n"
        "严格按下面的模板输出，字段名和冒号逐字照抄：\n"
        "\n"
        "句子：<英文原句，一字不改地照抄字幕>\n"
        "译文：<这句话自然的中文翻译>\n"
        "俚语：<表达> → <中文实际含义（带一句使用场景）>\n"
        "生词：<单词> /<音标>/ <词性> <中文释义>\n"
        "\n"
        "规则：\n"
        "1. 只允许「句子：」「译文：」「生词：」「俚语：」四种开头的行，"
        "冒号用中文全角「：」。\n"
        "2. 「句子：」「译文：」各一行，必须在最前面。\n"
        "3. 「俚语：」最多 4 行，必须用「 → 」连接表达与含义；没有就不写这行。\n"
        "4. 「生词：」最多 4 行，只写不解释就看不懂的词；没有就不写这行。\n"
        "5. 不要编号、不要 markdown、不要表格、不要表情符号、不要寒暄、"
        "不要问我「要不要展开」。"
    ),
    "中英对照字幕翻译": (
        "把图片字幕里的英文台词翻译成自然、口语化的中文。\n"
        "\n"
        "严格按下面的模板输出，字段名和冒号逐字照抄：\n"
        "\n"
        "句子：<英文原句，一字不改地照抄字幕>\n"
        "译文：<中文翻译，要口语化，不要书面腔>\n"
        "俚语：<表达> → <中文实际含义>\n"
        "\n"
        "规则：\n"
        "1. 只允许「句子：」「译文：」「俚语：」三种开头的行，冒号用中文全角「：」。\n"
        "2. 「句子：」「译文：」各一行，必须在最前面。\n"
        "3. 只有台词里有影响理解的俚语/梗时才写「俚语：」行，最多 2 行。\n"
        "4. 不要编号、不要 markdown、不要表格、不要表情符号、不要任何其它内容。"
    ),
    "精简划重点": (
        "从图片字幕里挑出最容易听错或看不懂的部分，用最短的话讲清楚。\n"
        "\n"
        "严格按下面的模板输出，字段名和冒号逐字照抄：\n"
        "\n"
        "句子：<英文原句，一字不改地照抄字幕>\n"
        "译文：<中文翻译>\n"
        "生词：<单词> /<音标>/ <词性> <中文释义，不超过 15 个字>\n"
        "\n"
        "规则：\n"
        "1. 只允许「句子：」「译文：」「生词：」三种开头的行，冒号用中文全角「：」。\n"
        "2. 「句子：」「译文：」各一行，必须在最前面。\n"
        "3. 「生词：」最多 3 行，只挑最容易听错的，释义不超过 15 个字。\n"
        "4. 不要编号、不要 markdown、不要表格、不要寒暄，一个字都不要多写。"
    ),
    # ★ 这一套是**用户自己那版**（六级 + 音标 + 词根词缀 + 句子结构）的固化版。
    #   他原来存在 settings.json 里，格式不稳定；这是同一套要求 + 固定模板。
    #   他要是改了默认想换回来，面板里选这一项即可。
    "字幕精读（音标 + 词根词缀 + 结构）": DEFAULT_PROMPT,
}
# 提示词最大长度（面板里会拦住，防止误粘一整篇文章）
# ★ 所有预设统一补上【追问规则】—— 理由见 _FOLLOWUP_RULE 顶部的说明：
#   提示词会常驻在追问的对话历史里，不划清作用域的话，套用任何一套预设
#   都会重演"追问被拒绝回答"。DEFAULT_PROMPT 自己已经带了，不会重复追加。
PROMPT_PRESETS = {
    _name: (_text if _text.endswith(_FOLLOWUP_RULE) else _text + _FOLLOWUP_RULE)
    for _name, _text in PROMPT_PRESETS.items()
}

PROMPT_MAX_CHARS = 4000

# 清洗回复前缀：豆包常见的“好的，截图里……”之类客套话
REPLY_PREFIX_PATTERNS = [
    r"^(好的|好呀|好的呀|好嘞|收到|明白|了解|没问题|OK|ok)[，,。!！:：\s]*",
    r"^(根据|按照)(你|您)?(提供|上传)?的?(图片|截图|字幕)[，,。:：\s]*",
    r"^(从|看)(这张|这幅|该)?(图片|截图|画面)(里|中|来看|来看，|中提取)[，,。:：\s]*",
    r"^(我(先|来)?(看|查看|识别|提取)(了|一下)?(图片|截图|画面|字幕)(里|中)?)[，,。:：\s]*",
    r"^(图片|截图|画面|字幕)(里|中)(直接出现|出现|包含|有)[^。\n]*[。:：]\s*",
    r"^(以下是|如下是|这是)[^。\n]{0,20}[。:：]\s*",
]
# 清洗回复尾部：豆包的追问/客套
# 注意：这些正则**不加 re.M**，$ 只匹配整个文本的末尾，
#       否则会把每一行的结尾都吃掉（踩过坑）。
REPLY_SUFFIX_PATTERNS = [
    r"\n*[（(]?\s*(需要我|要不要我|如果(你|您)?需要|还(想|要)(我)?(了解|知道)|"
    r"希望对你有帮助|希望(能)?帮到你|有其他问题|随时(告诉|问)我)[^\n]{0,40}$",
    r"\n*[-—=\s]*以上[。!！]?\s*$",
]

# ---------------------------------------------------------------- 悬浮窗 UI
OVERLAY_WIDTH = 620          # 卡片宽度 px（首次启动的默认值）
OVERLAY_MAX_HEIGHT = 460     # 卡片最大高度 px（超出滚动）
OVERLAY_MARGIN_BOTTOM = 90   # 距屏幕底部边距 px
OVERLAY_RADIUS = 18          # 圆角半径
OVERLAY_OPACITY = 0.90       # 卡片不透明度
OVERLAY_FONT_SIZE = 15       # 正文字号（默认值；用户可在设置面板里改，见 settings.get_overlay_font_size）
# 用户在面板上能调的范围。下限别低于 11（再小就真看不清了），
# 上限 30（再大一行就放不下几个字，卡片宽度撑不住）。
# ★ 这个范围同时被三处引用：settings 的钳制、panel 的滑块、overlay 的 ui_sizes。
OVERLAY_FONT_MIN = 11
OVERLAY_FONT_MAX = 30
OVERLAY_AUTO_HIDE_SEC = 0    # >0 时 N 秒后自动关闭；0 = 只由下一次按键关闭

# 悬浮窗可拖动缩放：用户拖着边/右下角就能改宽高，尺寸会记住（settings.json）。
OVERLAY_RESIZABLE = True
OVERLAY_MIN_WIDTH = 360
OVERLAY_MIN_HEIGHT = 130
OVERLAY_GRIP_PX = 7          # 四条边热区厚度（px）
OVERLAY_GRIP_CORNER = 18     # 右下角缩放手柄大小（px）

# ★ 记住位置与大小：下次浮窗出现在**上次你摆的那个地方**，尺寸也一样。
#   之前只记尺寸、位置每次都回到底部居中，用户把浮窗拖到顺眼的位置后
#   下一次又被挪回去，很烦（用户明确要求）。
#   实现要点：位置按「屏幕 + 相对比例」存，换分辨率 / 插拔外接屏也不会跑到屏幕外；
#   恢复时还会夹紧到当前屏幕的可见区域（见 overlay._restore_geometry）。
OVERLAY_REMEMBER_GEOMETRY = True

# ★ 毛玻璃（NSVisualEffectView）默认【关闭】—— 这不是偷懒，是必须关。
#   实测：只要往 Qt 的窗口里插 NSVisualEffectView（无论作为 contentView 的
#   子视图、把 effect 提成 contentView、还是挂成 contentView 的兄弟层），
#   Qt 画的文字就整个不再被合成到屏幕上 —— 用户看到的就是「一个空白浮框，
#   里面什么都没有」，而 widget.grab() 里文字明明是在的。
#   三种挂法都已截图验证为空白，故彻底关闭；卡片改用半透明深色底
#   （OVERLAY_OPACITY），压在全屏视频上同样清楚。
#   想自己再试：DL_VIBRANCY=1 启动即可。
OVERLAY_VIBRANCY = os.environ.get("DL_VIBRANCY", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

# 悬浮窗深色配色（默认暗色；亮色主题改这里）
OVERLAY_BG = (22, 24, 30)        # 卡片基色 RGB
OVERLAY_FG = "#EAECEF"
OVERLAY_ACCENT = "#7CC4FF"       # 生词高亮色
OVERLAY_TITLE = "#9AA4B2"

# ==================================================================== 观看记录
# 用户要求：「形成一个观看记录，名称为我正在看的剧名+日期，内容就是我目前看的
#           所有的 AI 输出的内容，加上编号和时间。直接放在桌面就行。」
#
# 设计取舍：
#  · 落成 .md 而不是 .docx/.rtf —— 这是一份**边看边追加**的流水账，
#    每次查词写一段；Word 这类格式必须整份重写，写坏一次整本记录就废了。
#    .md 是纯文本，追加是原子的、断电最多丢最后一次，还能用任意编辑器打开。
#  · 放桌面（而不是日志目录）—— 用户要的是"看得见、能翻"，不是"能排查"。
#  · 剧名 + 日期当文件名。日期取**写入当天**，跨零点会自动开新文件；
#    中途换剧（剧名变了）也会自动开新文件，两份记录不会串在一起。
# 记录落在哪。★ 允许用 DL_VIEWLOG_DIR 覆盖 —— **测试和探针必须覆盖**：
# 回归测试里会走完整的"回答问题"链路（test_lookup_api / test_close_key），
# 那条链路会调 viewlog.record()。不覆盖的话，跑一次测试就在**用户桌面上**
# 多出一个「未知剧名 YYYY-MM-DD.md」—— 污染用户的桌面，还容易让人以为
# 是 App 自己在乱写。（实战踩到过：桌面那份"未知剧名"就是这么来的。）
# ★ 目录优先级：DL_VIEWLOG_DIR 环境变量 > 设置面板里用户选的目录 > 「桌面」。
#   本文件只提供**默认值**和**环境变量覆盖**；真正生效的目录一律走
#   `settings.effective_viewlog_dir()`（viewlog 模块就是这么取的）。
#   环境变量**必须**保留：回归测试会走完整问答链路 → 调 viewlog.record()，
#   不覆盖就会在用户桌面上多出一份「未知剧名 YYYY-MM-DD.md」（实战踩到过）。
_VIEWLOG_ENV = (os.environ.get("DL_VIEWLOG_DIR") or "").strip()
# 用户没设过目录时落在哪。放桌面 —— 用户要的是"看得见、能翻"，不是"能排查"。
VIEWLOG_DIR_DEFAULT = HOME / "Desktop"
# 兼容旧引用（= 环境变量或默认值，**不含**用户在面板里设的目录）
VIEWLOG_DIR = Path(_VIEWLOG_ENV or VIEWLOG_DIR_DEFAULT).expanduser()
VIEWLOG_ENABLED = True
# 文件名里不能出现的字符（含 macOS 的 '/' 和 Windows 的保留字符，
# 因为这份记录很可能被拷到 U 盘/网盘上）
VIEWLOG_BAD_CHARS = '/\\:*?"<>|'
# 单条记录里正文最多留多少字符（防止某次模型抽风回一整篇文章撑爆文件）
VIEWLOG_MAX_BODY = 4000
