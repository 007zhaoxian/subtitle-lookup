"""直连大模型 API 的查词客户端 —— 只用标准库，不额外引入 requests。

设计要点
--------
1. **统一走 OpenAI 兼容协议**（`POST {base_url}/chat/completions`）。
   这样加服务商只要填 base_url + 模型名，不用改代码（见 config.PROVIDERS）。

2. **图片用 base64 内联**（`data:image/jpeg;base64,…`），不依赖图床、
   不需要单独上传接口，一次请求发完就走。

3. **错误必须"能看懂"**。这是本模块最花心思的地方 —— 用户填了个纯文本模型
   （比如 deepseek-v4-pro）时，服务端只会回一句含糊的 400。如果直接把原始
   报文抛出去，用户看到的是一串 JSON，根本不知道是自己模型选错了。
   所以 _classify() 把常见错误翻译成人话 + 给下一步怎么办。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import config
from utils import log


class ApiError(Exception):
    """带「给用户看的建议」的异常。

    永远用 user_text() 展示给用户，不要把 str(e) 直接丢到浮窗里 ——
    那里面是给开发看的，用户看不懂。
    """

    def __init__(self, message: str, hint: str = "", status: int | None = None,
                 raw: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status
        self.raw = raw

    def user_text(self) -> str:
        return f"{self.message}\n{self.hint}" if self.hint else self.message


# ------------------------------------------------------------------ 图片编码
def encode_image(path) -> str:
    """把本地图片读成 data URL。"""
    p = Path(path)
    raw = p.read_bytes()
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# ------------------------------------------------------------------ 错误翻译
def _classify(status: int, body: str) -> ApiError:
    """把 HTTP 错误翻译成「人话 + 下一步」。"""
    low = (body or "").lower()

    if status == 401 or "invalid_api_key" in low or "authentication" in low:
        return ApiError("API Key 无效或已失效", "到服务商后台重新复制一个，注意别带多余空格",
                        status, body)
    if status == 402 or "insufficient" in low or "balance" in low or "quota" in low:
        return ApiError("账户余额不足或额度已用完", "去服务商后台充值，或换成别的服务商",
                        status, body)
    if status == 403:
        return ApiError("这个 Key 没有调用该模型的权限",
                        "检查后台是否已开通此模型，或换一个模型试试", status, body)
    if status == 404 or "model_not_found" in low or "does not exist" in low:
        return ApiError("模型名不存在", "核对模型名（大小写、斜杠都要对），或换成服务商默认模型",
                        status, body)
    if status == 429:
        return ApiError("请求太频繁，被限流了", "等几秒再按一次空格", status, body)

    # ★ 最重要的一条：模型不支持图片。
    # 各家措辞不一样，靠关键词兜。
    image_kw = ("image", "vision", "multimodal", "图片", "图像", "视觉")
    if status == 400 and any(k in low for k in image_kw):
        return ApiError("这个模型不支持图片输入",
                        "换一个支持视觉的模型（DeepSeek 用 deepseek-flash；"
                        "在设置里点「测试连通性」可验证）", status, body)

    if status == 400:
        return ApiError("请求被服务端拒绝", f"原始返回：{body[:200]}", status, body)

    if status >= 500:
        return ApiError("服务商服务器出错", "过一会儿再试；持续报错就换个服务商",
                        status, body)

    return ApiError(f"请求失败（HTTP {status}）", (body or "")[:200], status, body)


def _post_json(url: str, payload: dict, key: str, timeout: float) -> dict:
    """发一个带鉴权的 JSON POST，返回解析后的响应体。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        log.warning("API HTTP %s: %s", exc.code, body[:400])
        raise _classify(exc.code, body) from None
    except socket.timeout:
        raise ApiError("请求超时",
                       f"等了 {timeout:.0f} 秒还没返回。可以调大 config.API_TIMEOUT_SEC，"
                       "或把截图范围改小一点") from None
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        if "timed out" in reason.lower():
            raise ApiError("请求超时", f"等了 {timeout:.0f} 秒还没返回") from None
        raise ApiError("连不上服务商", f"网络错误：{reason}｜检查网络或代理设置") from None
    except json.JSONDecodeError:
        raise ApiError("服务端返回的不是合法 JSON", "可能是 Base URL 填错了（末尾多了路径？）") from None


# ------------------------------------------------------------------ 对外接口
def ask(image_path, prompt: str, cfg: dict | None = None,
        timeout: float | None = None, max_tokens: int | None = None) -> str:
    """把截图 + 提示词发给模型，返回纯文本回答。

    cfg 用 settings.provider_config() 的返回值；不传则从 settings 现取。
    """
    if cfg is None:
        import settings
        cfg = settings.provider_config()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": encode_image(image_path)}},
        ],
    }]
    return chat(messages, cfg=cfg, timeout=timeout, max_tokens=max_tokens)


def _extract_answer(body: dict) -> tuple[str, str, str]:
    """从响应里取出 (正文, 思考内容, 结束原因)。

    ★ 为什么要单独拎出来：思考型模型（deepseek-flash）会把推理过程放进
    `message.reasoning_content`，正文放 `message.content`。当 max_tokens
    被推理过程吃光时，`finish_reason` 是 `length` 而 `content` 是**空字符串** ——
    直接读 content 就会得到一个空回答，用户看到的是空浮窗，
    而且表象会被误判成"截图里没字幕/豆包没登录"，排查方向全错。
    """
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        raise ApiError("返回格式不认识",
                       f"原始返回：{json.dumps(body, ensure_ascii=False)[:200]}") from None
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    # 不同服务商对"思考过程"的字段名不一样，都兜一下
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return text, reasoning, (choice.get("finish_reason") or "")


def chat(messages: list, cfg: dict | None = None,
         timeout: float | None = None, max_tokens: int | None = None) -> str:
    """把一段 OpenAI 格式的 messages 发出去，返回纯文本回答。

    这是核心函数：ask() 只是把「图 + 提示词」拼成 messages 再交给它；
    app 里的追问也是拼好历史后直接调它 —— 无状态 API 的多轮就靠这段历史。
    """
    if cfg is None:
        import settings
        cfg = settings.provider_config()

    if not cfg.get("key"):
        raise ApiError(f"还没填 {cfg.get('label', '该服务商')} 的 API Key",
                       "面板 → 查词引擎 → ⚡ API 直连 → API Key 里填一下")
    if not cfg.get("base_url"):
        raise ApiError("还没填 Base URL", "面板 → 查词引擎 → ⚡ API 直连 → Base URL 里填一下")

    url = f"{cfg['base_url']}/chat/completions"
    budget = int(max_tokens or config.API_MAX_TOKENS)
    to = float(timeout or config.API_TIMEOUT_SEC)
    no_think = dict(cfg.get("no_think") or {})

    def _send(tokens: int, extra: dict) -> tuple[str, str, str]:
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "max_tokens": tokens,
            "stream": False,
        }
        payload.update(extra)
        t0 = time.time()
        log.info("API 请求 → %s (%s @ %s)，%d 条消息，max_tokens=%d%s",
                 cfg["label"], cfg["model"], cfg["base_url"], len(messages), tokens,
                 "，已关思考" if extra else "")
        body = _post_json(url, payload, cfg["key"], to)
        text, reasoning, finish = _extract_answer(body)
        usage = body.get("usage") or {}
        log.info("API 返回 %.1fs，正文 %d 字，思考 %d 字，finish=%s，tokens=%s",
                 time.time() - t0, len(text), len(reasoning), finish or "?",
                 usage.get("total_tokens", "?"))
        return text, reasoning, finish

    try:
        text, reasoning, finish = _send(budget, no_think)
    except ApiError as exc:
        # 兜底：个别严格网关会拒掉它不认识的字段（400）。既然关思考只是
        # "优化项"，被拒就老老实实去掉再发一次，别让用户因为提速措施而彻底用不了。
        if no_think and exc.status == 400:
            log.warning("服务商不接受关思考参数（400），去掉后重试：%s", exc.message)
            text, reasoning, finish = _send(budget, {})
        else:
            raise

    # 空正文 + 被截断 = 「预算被思考过程吃光」。
    # 这类失败是概率性的，同一张图重发一次大概率就正常，所以先自己重试一轮
    # （翻倍预算），别把这个锅甩给用户。只重试一次，避免把等待时间无限拉长。
    if not text.strip() and finish == "length":
        bigger = min(budget * 2, config.API_MAX_TOKENS_CEILING)
        if bigger > budget:
            log.warning("正文为空且被截断（思考 %d 字），把预算提到 %d 重试一次",
                        len(reasoning), bigger)
            text, reasoning, finish = _send(bigger, no_think)

    if not text.strip():
        # 还是空：这次要说清楚是模型的问题，不能让 app 层把它当成
        # "截图里没字幕"，那会把用户带偏。
        if finish == "length":
            raise ApiError(
                "模型把这次的回答预算全用在思考上了，还没写出正文",
                "再按一次触发键重试；若反复出现，换一个非思考型的模型",
            )
        raise ApiError("模型返回了空回答",
                       f"这次没拿到正文（思考 {len(reasoning)} 字，finish={finish or '?'}）。"
                       "再试一次；持续为空就换个模型")
    return text.strip()


def _looks_like_vision(mid: str) -> bool:
    """靠名字猜这个模型支不支持图片。

    只在服务商**没给**机器可读的模态信息时才用（目前只有 OpenRouter 会给）。
    猜错的代价仅仅是候选列表里多一个不能用的名字 —— 用户手填仍能覆盖，
    而且还有「测试连通性」兜底，所以够用。
    """
    low = (mid or "").lower()
    if any(k in low for k in config.NON_VISION_NAME_KEYWORDS):
        return False
    return any(k in low for k in config.VISION_NAME_KEYWORDS)


def fetch_models(cfg: dict | None = None, timeout: float = 20.0) -> dict:
    """拉服务商的模型列表，并尽量标出「哪些能看图」。

    返回 {"models": [{"id","vision","explicit"}, …], "total": n, "explicit": bool}

    `explicit=True` 表示这个"能不能看图"的判断来自服务商给的**真实字段**
    （OpenRouter 的 `architecture.input_modalities`），而不是猜的。
    界面据此决定提示语该说"已按官方信息筛选"还是"按名字猜的，请自己验证"。

    为什么要做这件事：这些服务商的模型列表一周就能变一次，写死在
    config.PROVIDERS 里的候选迟早过期。让它能现拉，用户就不用等我们发版。
    """
    if cfg is None:
        import settings
        cfg = settings.provider_config()
    if not cfg.get("base_url"):
        raise ApiError("还没填 Base URL", "面板 → 查词引擎 → ⚡ API 直连 → Base URL 里填一下")

    url = f"{cfg['base_url']}/models"
    headers = {"Accept": "application/json"}
    if cfg.get("key"):
        headers["Authorization"] = f"Bearer {cfg['key']}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise _classify(exc.code, raw) from None
    except socket.timeout:
        raise ApiError("拉取模型列表超时", f"等了 {timeout:.0f} 秒") from None
    except urllib.error.URLError as exc:
        raise ApiError("连不上服务商", f"网络错误：{getattr(exc, 'reason', exc)}") from None
    except json.JSONDecodeError:
        raise ApiError("服务端返回的不是合法 JSON",
                       "可能是 Base URL 填错了（有的服务商不提供 /models 接口）") from None

    raw_list = body.get("data") if isinstance(body, dict) else None
    if not isinstance(raw_list, list):
        raise ApiError("这个服务商没返回模型列表",
                       "可能它不提供标准的 /models 接口，直接在模型下拉框里手填模型名即可")

    entries: list[dict] = []
    explicit_seen = False
    for m in raw_list:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("model") or ""
        if not isinstance(mid, str) or not mid.strip():
            continue
        mid = mid.strip()

        explicit: bool | None = None
        arch = m.get("architecture")
        if isinstance(arch, dict):
            mods = arch.get("input_modalities")
            if isinstance(mods, list):
                explicit = "image" in [str(x).lower() for x in mods]
                explicit_seen = True

        vision = explicit if explicit is not None else _looks_like_vision(mid)
        entries.append({"id": mid, "vision": bool(vision),
                        "explicit": explicit is not None})

    # 排序：能看图的排前面（本程序离开图片就没法工作），
    # 同组内**把 config 里精选的那几个提到最前**（按精选顺序），
    # 剩下的按名字排。否则 OpenRouter 拉四百多个，用户看到的全是 amazon/…
    # 这种根本不会去用的模型，下拉框就废了。
    curated = [m for m in (cfg.get("models") or []) if isinstance(m, str)]
    rank = {m: i for i, m in enumerate(curated)}

    def _sort_key(e: dict) -> tuple:
        return (not e["vision"], rank.get(e["id"], len(rank)), e["id"].lower())

    entries.sort(key=_sort_key)

    limit = int(config.MODELS_FETCH_LIMIT)
    if len(entries) > limit:
        keep_vis = [e for e in entries if e["vision"]][:limit]
        keep_rest = [e for e in entries if not e["vision"]]
        entries = keep_vis + keep_rest[: max(0, limit - len(keep_vis))]

    log.info("拉取模型列表：%s 共 %d 个，保留 %d 个（其中 %d 个疑似支持图片，信息%s）",
             cfg.get("label", cfg.get("base_url")), len(raw_list), len(entries),
             sum(1 for e in entries if e["vision"]),
             "来自服务商字段" if explicit_seen else "为按名字推断")
    return {"models": entries, "total": len(raw_list), "explicit": explicit_seen}


def list_models(cfg: dict | None = None, timeout: float = 20.0) -> list[str]:
    """只要模型 id 的简化版（内部/测试用）。"""
    return [e["id"] for e in fetch_models(cfg, timeout)["models"]]


# ------------------------------------------------------------------ 连通性自检
TEST_CAPTION = "I'm all ears - spill the tea, mate."


def _make_test_image(path: Path) -> str:
    """生成一张带英文字幕的测试图，用来**真的验证这个模型能不能看图**。

    不用现成截图是故意的：测试图上的文字是我们自己写的，标准答案已知，
    能准确还原才说明视觉链路真的通了（而不是模型在瞎猜）。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 220), "black")
    draw = ImageDraw.Draw(img)
    font = None
    for cand in ("/System/Library/Fonts/Helvetica.ttc",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            font = ImageFont.truetype(cand, 38)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((30, 90), TEST_CAPTION, fill="white", font=font)
    img.save(path)
    return TEST_CAPTION


def test_connection(cfg: dict | None = None, timeout: float = 45.0) -> dict:
    """真实发一次带图请求，判断「key + 模型」这套组合到底能不能用。

    返回 {"ok": bool, "message": str, "caption_ok": bool, "elapsed": float}
    caption_ok 表示模型有没有把测试图上的文字读对 —— 这才是"能看图"的硬证据。
    """
    if cfg is None:
        import settings
        cfg = settings.provider_config()

    tmp = config.TMP_DIR / "api_selftest.png"
    expect = _make_test_image(tmp)

    t0 = time.time()
    try:
        text = ask(tmp, "图里有一句英文字幕。请原样抄出这句话，不要解释。",
                   cfg=cfg, timeout=timeout, max_tokens=200)
    except ApiError as exc:
        return {"ok": False, "message": exc.user_text(), "caption_ok": False,
                "elapsed": time.time() - t0}
    except Exception as exc:
        return {"ok": False, "message": f"意外错误：{exc}", "caption_ok": False,
                "elapsed": time.time() - t0}
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass

    cost = time.time() - t0
    # 容错比较：忽略大小写、连字符与空格差异
    def _norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    hit = _norm(expect) in _norm(text)
    if hit:
        return {"ok": True,
                "message": f"连通正常，模型能准确读出图片文字（{cost:.1f}s）",
                "caption_ok": True, "elapsed": cost}
    return {"ok": False,
            "message": ("连通正常，但**模型没能读出测试图上的文字**。\n"
                        "多半是这个模型不支持图片输入（纯文本模型）。\n"
                        f"它返回的是：{text[:120]}\n"
                        "建议换一个支持视觉的模型，DeepSeek 请用 deepseek-flash。"),
            "caption_ok": False, "elapsed": cost}
