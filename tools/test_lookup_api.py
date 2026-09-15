"""**查词主链路**的测试（mock 掉网络，不花一分钱额度）。

★ v1.2.2：本文件原来叫 test_api_backend.py —— 那时程序有两条并列的通道
  （直连 API / 网页版豆包），它测的是其中新加的一条。网页版删除后这条就是
  **唯一**的通道了，所以改名成 test_lookup_api.py，别再从名字上以为还有第二条。

覆盖：
  · 查词真的走 _ask_api，而且 App 里已经**没有** worker 这种东西了
  · messages 怎么拼（提示词 + base64 图）
  · 追问怎么把历史接上（无状态 HTTP 接口的多轮全靠这段历史）
  · ★ 出错时浮窗里显示的是 user_text()（含"下一步怎么办"），不是干巴巴的 str(exc)

跑法：
    python tools/test_lookup_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_lookupapi_")
# 测试也会走"回答 → 记观看记录"那条链路；不重定向就会在**用户桌面**上
# 生成「未知剧名 <日期>.md」。跑测试不许碰用户桌面。
os.environ["DL_VIEWLOG_DIR"] = tempfile.mkdtemp(prefix="dl_test_lookupapi_vl_")

import api_client  # noqa: E402
import app as app_mod  # noqa: E402
import config  # noqa: E402
import settings  # noqa: E402
from api_client import ApiError  # noqa: E402
from app import ASKING, IDLE, SHOWING  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"PASS  {name}: {got!r}")
    else:
        print(f"FAIL  {name}: {got!r} (期望 {want!r})")
        FAILED.append(name)


def wait(pred, timeout: float = 5.0) -> bool:
    """后台线程是异步的，轮询等它跑完（比 sleep 固定时长稳）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ 假的依赖
class FakeRuntime:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []
        self.answers: list[tuple[str, bool]] = []
        self.hidden = 0
        self.overlay = True
        self.typing = False

    def mark_auto_ui(self) -> None:
        pass

    def typing_active(self) -> bool:
        return self.typing

    def has_overlay(self) -> bool:
        return self.overlay

    def post_show(self, title, body) -> None:
        self.shown.append((title, body))
        self.overlay = True

    def post_hide(self) -> None:
        self.hidden += 1
        self.overlay = False

    def post_overlay_answer(self, text, error=False) -> None:
        self.answers.append((text, error))

    def post_overlay_status(self, text) -> None:
        pass


# ------------------------------------------------------------------ mock 网络
CHAT_CALLS: list[list] = []
NEXT_REPLY: list = []
NEXT_ERROR: list = []


def _fake_chat(messages, cfg=None, timeout=None, max_tokens=None) -> str:
    CHAT_CALLS.append([dict(m) for m in messages])
    if NEXT_ERROR:
        raise NEXT_ERROR.pop(0)
    return NEXT_REPLY.pop(0) if NEXT_REPLY else "face the music = 承担后果"


def fresh():
    a = app_mod.App()
    a.runtime = FakeRuntime()
    a._mode_on = True
    return a


def main() -> int:
    app_mod.notify = lambda *a, **k: None
    app_mod.screenshot = type("_FakeShot", (), {
        "capture_fullscreen": staticmethod(lambda: Path("/tmp/_fake_shot.png")),
        "cleanup": staticmethod(lambda _p: None),
    })
    api_client.chat = _fake_chat
    api_client.encode_image = lambda _p: "data:image/png;base64,FAKEBASE64"
    settings.set_api_key("sk-fake-key-for-test")

    # ★ 回归钉子：网页版豆包删干净了 —— App 上不该再有 worker，
    #   config/settings 上不该再有后端开关。谁哪天又加回来，这里立刻红。
    check("App 上已经没有 worker 了", hasattr(app_mod.App(), "worker"), False)
    check("config 里没有 BACKEND 开关", hasattr(config, "BACKEND"), False)
    check("settings 里没有 get_backend", hasattr(settings, "get_backend"), False)

    # ================================================================ 1. 查词
    print("== 1. 查词走 API 后端 ==")
    CHAT_CALLS.clear()
    a = fresh()
    a._state = IDLE
    a._capture_and_ask()
    check("等到了 chat() 被调用", wait(lambda: len(CHAT_CALLS) >= 1), True)
    check("状态变成 showing", wait(lambda: a._state == SHOWING), True)

    msgs = CHAT_CALLS[0]
    check("只发了 1 条消息", len(msgs), 1)
    check("角色是 user", msgs[0]["role"], "user")
    check("内容是分段的", isinstance(msgs[0]["content"], list), True)
    check("第一段是提示词", msgs[0]["content"][0]["type"], "text")
    check("  提示词非空", bool(msgs[0]["content"][0]["text"]), True)
    check("第二段是图片", msgs[0]["content"][1]["type"], "image_url")
    check("  图片是 data URL",
          msgs[0]["content"][1]["image_url"]["url"].startswith("data:image/"), True)
    check("结果进了浮窗", bool(a.runtime.shown), True)
    check("  浮窗里有回答", "face the music" in a.runtime.shown[-1][1], True)
    check("历史记下来了（供追问用）", len(a._api_messages), 2)
    check("  历史最后是 assistant", a._api_messages[-1]["role"], "assistant")

    # ================================================================ 2. 追问
    print("\n== 2. 追问把历史接上 ==")
    NEXT_REPLY.append("这里是口语化的「接受后果」。")
    CHAT_CALLS.clear()
    a.submit_question("这句里的 face the music 是什么语气？")
    check("等到了第二次 chat()", wait(lambda: len(CHAT_CALLS) >= 1), True)

    msgs = CHAT_CALLS[0]
    check("追问发了 3 条（图+回答+新问题）", len(msgs), 3)
    check("  第 1 条还是原来那张图", msgs[0]["content"][1]["type"], "image_url")
    check("  第 2 条是 assistant 的旧回答", msgs[1]["role"], "assistant")
    check("  第 3 条是这次的问题", msgs[2]["role"], "user")
    check("  问题内容对得上", "face the music" in msgs[2]["content"], True)
    check("回答追加到浮窗",
          wait(lambda: len(a.runtime.answers) >= 1), True)
    if a.runtime.answers:
        check("  追加的是新回答", "接受后果" in a.runtime.answers[0][0], True)
        check("  不是错误", a.runtime.answers[0][1], False)
    check("状态回到 showing", wait(lambda: a._state == SHOWING), True)

    # ================================================================ 3. 报错
    print("\n== 3. ★ 出错时给用户看得懂的话（user_text） ==")
    NEXT_ERROR.append(ApiError("这个模型不支持图片输入",
                               "换一个支持视觉的模型（DeepSeek 用 deepseek-flash）",
                               status=400))
    CHAT_CALLS.clear()
    b = fresh()
    b._state = SHOWING
    b.submit_question("再问一次")
    check("等到了错误回调", wait(lambda: len(b.runtime.answers) >= 1), True)
    if b.runtime.answers:
        text, is_err = b.runtime.answers[0]
        check("标记为错误", is_err, True)
        check("说了不支持图片", "不支持图片" in text, True)
        check("★ 连建议一起给了（证明用的 user_text 而非 str(exc)）",
              "deepseek-flash" in text, True)

    # 查词失败同样要把建议露出来
    NEXT_ERROR.append(ApiError("请求太频繁，被限流了", "等几秒再按一次空格", status=429))
    CHAT_CALLS.clear()
    c = fresh()
    c._state = IDLE
    c._capture_and_ask()
    check("查词失败也进了浮窗", wait(lambda: len(c.runtime.shown) >= 1), True)
    if c.runtime.shown:
        check("  显示的是限流提示", "限流" in c.runtime.shown[-1][1], True)
        check("  建议也在里面", "等几秒" in c.runtime.shown[-1][1], True)

    print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
