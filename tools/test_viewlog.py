"""桌面「观看记录」的回归测试 —— **完全离线**，不连 IINA、不写桌面。

盯的是用户 2026-09-15 的第 1 条要求：
「形成一个观看记录，名称为我正在看的剧名+日期，内容就是我目前看的所有的
  AI 输出的内容，加上编号和时间。直接放在桌面就行。」

这里把「剧名怎么认」「文件怎么定」「编号怎么接着排」三件事全部离线钉住。
真机桌面只由 App 写，测试一律写进临时目录（`config.VIEWLOG_DIR` 被改指向
tempdir），**绝不碰用户的桌面**。

跑法：
    python3 tools/test_viewlog.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                     # noqa: E402
import settings                                                   # noqa: E402
import viewlog                                                    # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}\n      期望 {want!r}\n      实际 {got!r}")


# ================================================== 1. 剧名解析（纯函数）
print("== 1. show_name_from_filename：各种发布文件名 ==")
cases = {
    "The.Bear.S03E04.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv": "The Bear",
    "老友记.Friends.S01E03.2160p.WEB-DL.mkv": "老友记 Friends",
    "D:\\剧\\Breaking.Bad.S05E14.720p.BluRay.x264.mkv": "Breaking Bad",
    "Severance.S02E01.1080p.ATVP.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv": "Severance",
    "Some.Show.1x04.HDTV.mkv": "Some Show",
    "Friends - EP03 - The One With.mp4": "Friends",
    "老友记.Friends.第03集.mkv": "老友记 Friends",
    "Shogun.2024.S01E01.mkv": "Shogun",
    "xxx.S01E01.zh.ass": "xxx",
    # ★ 回归：剧名本身不能被当成扩展名吃掉（曾经 `The.Bear.mkv` → `The`）
    "The.Bear.mkv": "The Bear",
    "The.Bear": "The Bear",
    "": "",
    "   ": "",
}
for raw, want in cases.items():
    check(f"{raw!r} → {want!r}", viewlog.show_name_from_filename(raw), want)

# ================================================== 2. 文件名清洗
print("\n== 2. safe_filename / target_filename ==")
check("路径分隔符与 Windows 保留字符都被换掉",
      viewlog.safe_filename('a/b:c*d?e"f<g>h|i\\j'), "a-b-c-d-e-f-g-h-i-j")
check("换行换成空格", viewlog.safe_filename("a\nb"), "a b")
check("超长会被截断", len(viewlog.safe_filename("x" * 200)), 60)
check("纯坏字符洗净后是空串（交给 target_filename 兜底）",
      viewlog.safe_filename("///"), "")
check("所以这种情况仍然能得到一个合法文件名",
      viewlog.target_filename("///", "2026-09-15"), "未知剧名 2026-09-15.md")
check("认不出剧名时退化", viewlog.target_filename("", "2026-09-15"),
      "未知剧名 2026-09-15.md")
check("正常文件名", viewlog.target_filename("老友记 Friends", "2026-09-15"),
      "老友记 Friends 2026-09-15.md")

# ================================================== 3. 落盘：编号 / 时间 / 续排
print("\n== 3. 写文件：头部 + 编号 + 时间 ==")
tmp = Path(tempfile.mkdtemp(prefix="viewlog-test-"))
_orig_dir = config.VIEWLOG_DIR
_orig_fname = viewlog._media_filename
_orig_enabled = settings.get_viewlog_enabled
_orig_env_dir = os.environ.get("DL_VIEWLOG_DIR")
config.VIEWLOG_DIR = tmp
# ★ v1.2.3 起记录目录走 settings.effective_viewlog_dir()（面板里可以改），
#   而它的**最高优先级是 DL_VIEWLOG_DIR 环境变量** —— 所以测试必须靠环境变量
#   重定向，光改 config.VIEWLOG_DIR 已经不管用了。
os.environ["DL_VIEWLOG_DIR"] = str(tmp)
viewlog.reset_for_test()


class FakeTime:
    """把 viewlog 看到的"现在"钉死，才能断言文件名和编号。"""

    def __init__(self, y, m, d, hh=20, mm=14, ss=32):
        self.tm = time.struct_time((y, m, d, hh, mm, ss, 0, 0, -1))

    def localtime(self):
        return self.tm

    def strftime(self, fmt, t=None):
        return time.strftime(fmt, t if t is not None else self.tm)


_real_time_mod = viewlog.time
try:
    viewlog.time = FakeTime(2026, 9, 15)                          # type: ignore[assignment]
    viewlog._media_filename = lambda: "The.Bear.S03E04.1080p.mkv"

    p1 = viewlog.append_now("句子：Hi\n译文：你好", kind="查词")
    check("文件按「剧名 日期.md」命名", p1.name, "The Bear 2026-09-15.md")
    check("写在指定的目录里", p1.parent, tmp)

    text = p1.read_text(encoding="utf-8")
    check("有标题头", text.startswith("# 观看记录 · The Bear\n"), True)
    check("头部有日期", "- 日期：2026-09-15" in text, True)
    check("第 1 条编号是 001", "## 001 · 20:14:32" in text, True)
    check("正文原样保留", "句子：Hi\n译文：你好" in text, True)

    viewlog.time = FakeTime(2026, 9, 15, 20, 18, 7)               # type: ignore[assignment]
    viewlog.append_now("译文：第二句", kind="查词")
    text = p1.read_text(encoding="utf-8")
    check("第 2 条编号是 002、时间是第二个时刻", "## 002 · 20:18:07" in text, True)

    # ★ 重启续排：reset 掉内存状态（等价于重开 App），编号必须接着走
    viewlog.reset_for_test()
    viewlog.append_now("译文：第三句", kind="查词")
    text = p1.read_text(encoding="utf-8")
    check("重开 App 后编号接着排（不会回到 001）", "## 003 · 20:18:07" in text, True)
    check("同一个文件、没有新开", sorted(x.name for x in tmp.iterdir()),
          ["The Bear 2026-09-15.md"])

    # ---- 追问：把问题也记下来 ----
    viewlog.time = FakeTime(2026, 9, 15, 20, 20, 11)               # type: ignore[assignment]
    viewlog.append_now("这句是反话，语气很讽刺。", ask="这是什么语气？", kind="追问")
    text = p1.read_text(encoding="utf-8")
    check("追问条目带「追问」标记", "## 004 · 20:20:11 · 追问" in text, True)
    check("追问条目记下了问题", "> 问：这是什么语气？" in text, True)
    check("追问的回答也在", "这句是反话，语气很讽刺。" in text, True)

    # ---- 换剧 → 新文件 ----
    viewlog._media_filename = lambda: "Severance.S02E01.1080p.mkv"
    p2 = viewlog.append_now("句子：Hello", kind="查词")
    check("换剧 → 新开一份记录", p2.name, "Severance 2026-09-15.md")
    check("新文件编号从 001 开始",
          "## 001 ·" in p2.read_text(encoding="utf-8"), True)

    # ---- 认不出剧名 → **不**新开（避免 socket 一抖就切碎记录）----
    viewlog._media_filename = lambda: ""
    p3 = viewlog.append_now("句子：继续", kind="查词")
    check("认不出剧名时沿用手上这份，不新开文件", p3, p2)
    check("沿用时编号继续", "## 002 ·" in p2.read_text(encoding="utf-8"), True)

    # ---- 跨零点 → 新文件 ----
    viewlog._media_filename = lambda: "Severance.S02E01.1080p.mkv"
    viewlog.time = FakeTime(2026, 9, 16, 0, 3, 5)                 # type: ignore[assignment]
    p4 = viewlog.append_now("句子：明天", kind="查词")
    check("跨零点 → 新文件（日期换了）", p4.name, "Severance 2026-09-16.md")
    check("新日期文件编号从 001 起", "## 001 · 00:03:05" in p4.read_text(
        encoding="utf-8"), True)

    # ---- 超长正文截断 ----
    viewlog.time = FakeTime(2026, 9, 16, 0, 5, 0)                 # type: ignore[assignment]
    viewlog.append_now("字" * (config.VIEWLOG_MAX_BODY + 500), kind="查词")
    body = p4.read_text(encoding="utf-8")
    check("超长正文被截断而不是整条丢掉", "（内容过长，已截断）" in body, True)
    check("截断后文件不会无限膨胀",
          len(body) < config.VIEWLOG_MAX_BODY * 3, True)

    # ---- 空正文不入库 ----
    n_file = len(list(tmp.iterdir()))
    check("空正文不写文件", viewlog.append_now("   ", kind="查词"), None)
    check("空正文没有多出文件", len(list(tmp.iterdir())), n_file)

    # ============================================ 4. 异步入队（界面路径）
    print("\n== 4. record()：异步入队 + flush 排空 ==")
    viewlog._media_filename = lambda: "The.Bear.S03E04.1080p.mkv"
    viewlog.reset_for_test()
    for i in range(5):
        viewlog.record(f"句子：batch {i}", kind="查词")
    check("flush 能等到队列排空", viewlog.flush(5.0), True)
    qtext = (tmp / "The Bear 2026-09-16.md").read_text(encoding="utf-8")
    check("5 条异步记录都落盘了",
          sum(1 for i in range(5) if f"句子：batch {i}" in qtext), 5)
    check("异步路径的编号是连续的",
          [f"## {n:03d} ·" in qtext for n in (1, 2, 3, 4, 5)],
          [True] * 5)

    # ============================================ 5. 开关
    print("\n== 5. 关掉开关后：record() 一条都不写 ==")
    viewlog.reset_for_test()
    settings.get_viewlog_enabled = lambda: False
    before = {p: p.read_text(encoding="utf-8") for p in tmp.iterdir()}
    viewlog.record("句子：不该出现", kind="查词")
    check("flush 正常返回", viewlog.flush(3.0), True)
    after = {p: p.read_text(encoding="utf-8") for p in tmp.iterdir()}
    check("文件内容一个字都没变", before == after, True)

    settings.get_viewlog_enabled = lambda: True
    viewlog.record("句子：恢复记录", kind="查词")
    viewlog.flush(3.0)
    check("重新打开后继续记",
          "句子：恢复记录" in (tmp / "The Bear 2026-09-16.md").read_text(
              encoding="utf-8"), True)

finally:
    config.VIEWLOG_DIR = _orig_dir
    viewlog._media_filename = _orig_fname
    if _orig_env_dir is None:
        os.environ.pop("DL_VIEWLOG_DIR", None)
    else:
        os.environ["DL_VIEWLOG_DIR"] = _orig_env_dir
    settings.get_viewlog_enabled = _orig_enabled
    viewlog.time = _real_time_mod                                  # type: ignore[assignment]
    viewlog.reset_for_test()

# ================================================== 6. 开关的默认值
print("\n== 6. settings：观看记录默认开、字号可读写 ==")
d = settings._defaults()
check("默认开启", d["viewlog_enabled"], True)
check("默认字号 == config.OVERLAY_FONT_SIZE", d["overlay_font_size"],
      int(config.OVERLAY_FONT_SIZE))
check("字号下限钳制", settings._clamp_font(-999), int(config.OVERLAY_FONT_MIN))
check("字号上限钳制", settings._clamp_font(999), int(config.OVERLAY_FONT_MAX))
check("坏值退回默认", settings._clamp_font("abc"), int(config.OVERLAY_FONT_SIZE))
check("正常值原样通过", settings._clamp_font(19), 19)

print(f"\n共 {PASS + FAIL} 项，失败 {FAIL} 项")
print(f"（临时目录，可删：{tmp}）")
sys.exit(1 if FAIL else 0)
