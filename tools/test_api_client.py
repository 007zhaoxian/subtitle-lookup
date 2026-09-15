"""api_client 的离线测试（**全部 mock 掉网络**，一条真请求都不发）。

为什么敢全 mock：我们要验证的是"参数拼得对不对、错误翻译得对不对"，
不是"DeepSeek 现在好不好使"。真机验证请到面板点「测试连通性」。

跑法：
    python tools/test_api_client.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="test_api_client_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_client  # noqa: E402
import config  # noqa: E402
from api_client import ApiError, encode_image  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


def cfg(**over) -> dict:
    base = {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash", "key": "sk-test", "vision": True}
    base.update(over)
    return base


# ---------------------------------------------------------------- 图片编码
print("== 1. encode_image ==")
from PIL import Image  # noqa: E402

tmp_png = os.path.join(tempfile.gettempdir(), "tac_sample.png")
Image.new("RGB", (40, 20), "white").save(tmp_png)
url = encode_image(tmp_png)
check("是 data URL", url.startswith("data:image/png;base64,"), True)
check("后面跟着 base64", len(url.split("base64,")[1]) > 20, True)

# ---------------------------------------------------------------- 错误翻译
print("\n== 2. _classify：把 HTTP 错误翻译成人话 ==")
cases = [
    (401, '{"error":{"message":"Authentication Fails"}}', "API Key 无效或已失效"),
    (402, '{"error":{"message":"Insufficient balance"}}', "账户余额不足或额度已用完"),
    (404, '{"error":{"message":"Model does not exist"}}', "模型名不存在"),
    (429, '{"error":{"message":"rate limit"}}', "请求太频繁，被限流了"),
    (500, '{"error":{"message":"server error"}}', "服务商服务器出错"),
]
for status, body, want in cases:
    err = api_client._classify(status, body)
    check(f"HTTP {status}", err.message, want)
    check(f"HTTP {status} 带建议", bool(err.hint), True)

# ★ 最重要的一条：模型不支持图片。各家措辞不同，靠关键词兜。
print("\n== 3. ★ 纯文本模型必须明确报错（不能静默失败） ==")
vision_bodies = [
    '{"error":{"message":"图片格式不受支持，请把字幕以文字形式发我"}}',
    '{"error":{"message":"This model does not support image input"}}',
    '{"error":{"message":"multimodal not supported"}}',
    '{"error":{"message":"vision capability required"}}',
]
for body in vision_bodies:
    err = api_client._classify(400, body)
    check(f"识别为不支持图片｜{body[:38]}…", err.message, "这个模型不支持图片输入")
    check("  建议里点了 flash", "deepseek-flash" in err.hint, True)
    check("  user_text 含建议", "换一个支持视觉的模型" in err.user_text(), True)

plain400 = api_client._classify(400, '{"error":{"message":"something else"}}')
check("普通 400 不会误报成不支持图片",
      plain400.message == "这个模型不支持图片输入", False)

# ---------------------------------------------------------------- 参数拼装
print("\n== 4. chat() 请求参数 ==")
captured = {}


def _fake_post(url, payload, key, timeout):
    captured.update(url=url, payload=payload, key=key, timeout=timeout)
    return {"choices": [{"message": {"content": "  face the music = 承担后果  "}}],
            "usage": {"total_tokens": 42}}


orig_post = api_client._post_json
api_client._post_json = _fake_post
try:
    out = api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg())
    check("URL 拼对", captured["url"], "https://api.deepseek.com/chat/completions")
    check("model 带上", captured["payload"]["model"], "deepseek-flash")
    check("不流式", captured["payload"]["stream"], False)
    check("max_tokens 用配置值", captured["payload"]["max_tokens"], config.API_MAX_TOKENS)
    check("key 透传", captured["key"], "sk-test")
    check("返回会 strip", out, "face the music = 承担后果")

    print("\n== 5. 缺配置时的前置校验（别等发请求才炸） ==")
    try:
        api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg(key=""))
        check("没 key 应报错", "没报错", "抛 ApiError")
    except ApiError as e:
        check("没 key 报错", "API Key" in e.message, True)
        check("  提示去哪填（面板 → 查词引擎）",
              ("查词引擎" in e.hint and "API Key" in e.hint), True)
    try:
        api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg(base_url=""))
        check("没 base_url 应报错", "没报错", "抛 ApiError")
    except ApiError as e:
        check("没 base_url 报错", e.message, "还没填 Base URL")

    print("\n== 6. ask() 把图内联进 messages ==")
    api_client.ask(tmp_png, "解释这句", cfg=cfg())
    msg = captured["payload"]["messages"][0]
    check("content 是列表", isinstance(msg["content"], list), True)
    check("第一段是文本", msg["content"][0], {"type": "text", "text": "解释这句"})
    check("第二段是图片", msg["content"][1]["type"], "image_url")
    check("图片是 data URL",
          msg["content"][1]["image_url"]["url"].startswith("data:image/png"), True)
finally:
    api_client._post_json = orig_post

# ------------------------------------------- ★ 思考型模型把预算吃光
print("\n== 7. ★ 思考吃光 token 预算时不能静默返回空 ==")
# 复现的真实场景：deepseek-flash 把 1200 预算全用在 reasoning_content 上，
# content 为空、finish_reason=length。此时如果直接把 "" 返回给上层，
# app 会把它当成"截图里没字幕"，把用户带偏。这里要求：自动翻倍重试；
# 仍失败就抛明确说清原因的 ApiError。
retry_log: list[int] = []


def _post_thinking_then_ok(url, payload, key, timeout):
    """第一次给空正文（被截断），第二次给正常答案。"""
    retry_log.append(payload["max_tokens"])
    if len(retry_log) == 1:
        return {"choices": [{"finish_reason": "length",
                             "message": {"content": "",
                                         "reasoning_content": "让我想想…" * 40}}],
                "usage": {"total_tokens": 3000}}
    return {"choices": [{"finish_reason": "stop",
                         "message": {"content": "hot water = 陷入麻烦"}}],
            "usage": {"total_tokens": 123}}


api_client._post_json = _post_thinking_then_ok
try:
    out = api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg())
    check("空正文被自动重试后拿到答案", out, "hot water = 陷入麻烦")
    check("第一次用配置预算", retry_log[0], config.API_MAX_TOKENS)
    check("第二次预算翻倍", retry_log[1], config.API_MAX_TOKENS * 2)
    check("只重试一次（共 2 次请求）", len(retry_log), 2)

    print("\n== 7b. 重试后仍为空 → 抛明确的错，且原因指向模型 ==")
    bomb = []


    def _post_always_empty(url, payload, key, timeout):
        bomb.append(payload["max_tokens"])
        return {"choices": [{"finish_reason": "length",
                             "message": {"content": "", "reasoning_content": "x" * 50}}],
                "usage": {"total_tokens": 3000}}


    api_client._post_json = _post_always_empty
    try:
        api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg())
        check("一直空应报错", "没报错", "抛 ApiError")
    except ApiError as e:
        check("说明了真实原因", "思考" in e.message, True)
        check("  没把锅甩给截图", "字幕" not in e.user_text(), True)
        check("  给了下一步", bool(e.hint), True)
    check("重试到天花板就停", max(bomb) <= config.API_MAX_TOKENS_CEILING, True)
    check("没无限重试", len(bomb), 2)

    print("\n== 7c. 预算已到天花板时不再翻倍 ==")
    api_client._post_json = _post_always_empty
    bomb.clear()
    try:
        api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg(),
                        max_tokens=config.API_MAX_TOKENS_CEILING)
    except ApiError:
        pass
    except Exception as e:  # noqa: BLE001
        check("不该抛别的异常", type(e).__name__, "ApiError")
    check("到顶后只请求一次", len(bomb), 1)

    print("\n== 7d. 正常回答不会误触发重试 ==")
    normal = []


    def _post_normal(url, payload, key, timeout):
        normal.append(payload["max_tokens"])
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": "正常答案"}}]}


    api_client._post_json = _post_normal
    check("正常返回", api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg()), "正常答案")
    check("一次请求搞定", len(normal), 1)

    print("\n== 7e. finish_reason=stop 但正文空 → 报错而不是当没生词 ==")
    api_client._post_json = lambda *a, **k: {
        "choices": [{"finish_reason": "stop", "message": {"content": "  "}}]}
    try:
        api_client.chat([{"role": "user", "content": "hi"}], cfg=cfg())
        check("空正文应报错", "没报错", "抛 ApiError")
    except ApiError as e:
        check("空回答报错", "空回答" in e.message, True)
    print("\n== 7f. 关思考参数：按服务商下发，被严格网关拒绝时自动去掉 ==")
    import settings as _settings  # noqa: E402

    got_payload: dict = {}
    api_client._post_json = lambda u, p, k, t: (got_payload.update(p) or
                                                {"choices": [{"finish_reason": "stop",
                                                              "message": {"content": "ok"}}]})
    ds_cfg = dict(_settings.provider_config("deepseek"), key="sk-test-ds")
    api_client.chat([{"role": "user", "content": "hi"}], cfg=ds_cfg)
    check("deepseek 带上关思考参数",
          got_payload.get("thinking"), {"type": "disabled"})

    # 没配 no_think 的服务商不能凭空多出字段（严格网关会 400）
    custom_cfg = dict(cfg(), no_think={})
    got_payload.clear()
    api_client.chat([{"role": "user", "content": "hi"}], cfg=custom_cfg)
    check("没配的服务商不带该字段", "thinking" in got_payload, False)

    # 严格网关拒了 → 去掉参数再发一次，而不是直接失败
    tries: list[dict] = []

    def _post_strict(url, payload, key, timeout):
        tries.append(dict(payload))
        if "thinking" in payload:
            raise ApiError("请求被服务端拒绝", "原始返回：unknown field 'thinking'",
                           status=400)
        return {"choices": [{"finish_reason": "stop", "message": {"content": "去掉后成功"}}]}

    api_client._post_json = _post_strict
    out = api_client.chat([{"role": "user", "content": "hi"}], cfg=ds_cfg)
    check("被拒后仍拿到答案", out, "去掉后成功")
    check("共发两次", len(tries), 2)
    check("第一次带参数", "thinking" in tries[0], True)
    check("第二次去掉了", "thinking" in tries[1], False)

    # 400 但跟关思考无关（比如模型不支持图片）不能误触发重发
    strict_tries: list[dict] = []

    def _post_400_other(url, payload, key, timeout):
        strict_tries.append(dict(payload))
        raise ApiError("这个模型不支持图片输入", "换一个", status=400)

    api_client._post_json = _post_400_other
    try:
        api_client.chat([{"role": "user", "content": "hi"}], cfg=custom_cfg)
        check("该抛错", "没报错", "抛 ApiError")
    except ApiError:
        pass
    check("无参数时 400 不重发", len(strict_tries), 1)
finally:
    api_client._post_json = orig_post

# ------------------------------------------------------- 连通性自测的行为
print("\n== 8. test_connection 的行为（mock ask） ==")
real_ask = api_client.ask


def _ask_ok(image_path, prompt, cfg=None, timeout=None, max_tokens=None):
    return api_client.TEST_CAPTION          # 一字不差读出来了


def _ask_blind(image_path, prompt, cfg=None, timeout=None, max_tokens=None):
    # 纯文本模型的典型表现：礼貌地说自己看不见图
    return "抱歉，我无法查看图片，请把字幕以文字形式发给我。"


def _ask_lazy(image_path, prompt, cfg=None, timeout=None, max_tokens=None):
    return "I'm all ears - spill the tea, mate!"[:20]   # 只读了一半


api_client.ask = _ask_ok
r = api_client.test_connection(cfg=cfg())
check("读对了 → ok", r["ok"], True)
check("  caption_ok", r["caption_ok"], True)
check("  有耗时", r["elapsed"] >= 0, True)

api_client.ask = _ask_blind
r = api_client.test_connection(cfg=cfg())
check("读不出来 → 不 ok", r["ok"], False)
check("  明确说不支持图片", "不支持图片" in r["message"], True)
check("  点名了 flash", "deepseek-flash" in r["message"], True)

api_client.ask = _ask_lazy
r = api_client.test_connection(cfg=cfg())
check("读不全 → 不 ok", r["ok"], False)
check("  说明了原因", "没能读出测试图" in r["message"], True)

print("\n== 9. test_connection 遇到网络异常也不炸 ==")


def _ask_boom(image_path, prompt, cfg=None, timeout=None, max_tokens=None):
    raise ApiError("请求超时", "等了 45 秒还没返回")


api_client.ask = _ask_boom
r = api_client.test_connection(cfg=cfg())
check("异常被收成结果", (r["ok"], r["caption_ok"]), (False, False))
check("  message 是给用户的", "超时" in r["message"], True)

api_client.ask = real_ask
try:
    os.remove(tmp_png)
except OSError:
    pass

print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
sys.exit(0 if not FAILED else 1)
