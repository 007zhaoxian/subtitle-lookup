"""IINA 一键配置（input-ipc-server）的测试 —— 全部在**一次性的临时偏好域**上做。

为什么要这么小心：这个模块真的会写系统偏好。测试绝不能碰用户真实的
com.colliderli.iina（那是他正在用的 IINA 的配置）。所以这里把
`iina_setup.IINA_BUNDLE_ID` 换成一个一次性的域名，跑完删掉；
`iina_running()` / `iina_installed()` 也按用例打桩。

覆盖：
  1. 选项名规范化（`--input_ipc_server` 这种写法也要认出来，不能重复加）
  2. 合并逻辑：没有就追加 / 有就改值 / 已有的其它选项一个都不能丢
  3. 真写一次 → 读回校验 → 撤销 → 再读回校验
  4. IINA 在跑时必须**拒绝写入**（否则会被它退出时的整体写回覆盖掉）
  5. dry_run 绝不落盘

用法: python tools/test_iina_setup.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_iina_")

import iina_setup                                 # noqa: E402
from utils import log                             # noqa: E402
log.setLevel("ERROR")                             # 测试里别刷日志

# ★ 换成一次性的偏好域，绝不碰用户真实的 IINA 配置
TEST_DOMAIN = "com.example.subtitlelookup.iina_setup_test"
iina_setup.IINA_BUNDLE_ID = TEST_DOMAIN

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def eq(name: str, got, want) -> None:
    check(name, got == want, f"实际 {got!r}，期望 {want!r}")


def cleanup() -> None:
    subprocess.run(["defaults", "delete", TEST_DOMAIN],
                   capture_output=True, text=True)


def seed(options: list) -> None:
    """铺一个"用户本来就有别的 mpv 选项"的底稿。

    ⚠️ 别用 `defaults write dom key -array -array a b` 那条路：嵌套数组
    用命令行语法写不可靠（实测写进去不是我们想要的结构，于是"原有选项
    有没有被保留"这个断言会假失败）。走 plistlib + defaults import，
    和产品代码同一条路径，结果才确定。
    """
    cleanup()
    with tempfile.NamedTemporaryFile("wb", suffix=".plist", delete=False) as fh:
        plistlib.dump({"userOptions": options}, fh)
        tmp = fh.name
    subprocess.run(["defaults", "import", TEST_DOMAIN, tmp],
                   capture_output=True, text=True)
    Path(tmp).unlink(missing_ok=True)


# ======================================================================
def test_merge() -> None:
    print("== 1. 合并逻辑（不能丢用户已有的 mpv 选项）==")
    existing = [["vo", "gpu-next"], ["target-colorspace-hint", "yes"]]
    got = iina_setup._merge_option(existing, "/tmp/iina.sock")
    eq("没有同名项 → 追加到最后，其它选项原样保留",
       got, [["vo", "gpu-next"], ["target-colorspace-hint", "yes"],
             ["input-ipc-server", "/tmp/iina.sock"]])
    eq("原列表没被就地改坏", existing,
       [["vo", "gpu-next"], ["target-colorspace-hint", "yes"]])

    eq("已有同名项 → 改值，不重复加",
       iina_setup._merge_option(
           [["vo", "gpu-next"], ["input-ipc-server", "/tmp/old.sock"]],
           "/tmp/iina.sock"),
       [["vo", "gpu-next"], ["input-ipc-server", "/tmp/iina.sock"]])

    # 手写常见的两种错法，也要认出来是同名项
    eq("`--input-ipc-server` 也认得（不重复加）",
       iina_setup._merge_option([["--input-ipc-server", "/tmp/old.sock"]],
                                "/tmp/iina.sock"),
       [["input-ipc-server", "/tmp/iina.sock"]])
    eq("`input_ipc_server`（下划线写法）也认得",
       iina_setup._merge_option([["input_ipc_server", "/tmp/old.sock"]],
                                "/tmp/iina.sock"),
       [["input-ipc-server", "/tmp/iina.sock"]])

    print("\n== 2. 选项名规范化 ==")
    for raw in ("input-ipc-server", "--input-ipc-server", "-input-ipc-server",
                " input-ipc-server ", "input_ipc_server"):
        eq(f"{raw!r} → 归一", iina_setup._norm(raw), "input-ipc-server")


def test_write_readback() -> None:
    print("\n== 3. 真写一次 → 读回 → 撤销 → 再读回 ==")
    iina_setup.iina_installed = lambda: True
    iina_setup.iina_running = lambda: False          # 假装 IINA 已退出

    # 先铺一个"用户已有别的 mpv 选项"的底
    seed([["vo", "gpu-next"], ["target-colorspace-hint", "yes"]])
    eq("底稿读得回来", iina_setup.current_options(),
       [["vo", "gpu-next"], ["target-colorspace-hint", "yes"]])

    eq("还没配过", iina_setup.ipc_configured(), False)
    ok, msg = iina_setup.configure("/tmp/dl_test_iina.sock")
    check("写入成功", ok, msg)
    check("读回确认已配", iina_setup.ipc_configured())
    eq("读回的路径对得上", iina_setup.current_ipc_value(), "/tmp/dl_test_iina.sock")

    opts = iina_setup.current_options()
    check("原有的 vo gpu-next 没被写丢", ["vo", "gpu-next"] in opts, f"{opts}")
    check("原有的 target-colorspace-hint 也没丢",
          ["target-colorspace-hint", "yes"] in opts, f"{opts}")

    ok, msg = iina_setup.configure("/tmp/dl_test_iina.sock")
    check("重复配置 → 明确告知已配过，不重复加", ok and "已经配过" in msg, msg)
    eq("没有出现两条 input-ipc-server",
       len([o for o in iina_setup.current_options() if o and o[0] == "input-ipc-server"]), 1)

    ok, msg = iina_setup.remove()
    check("撤销成功", ok, msg)
    eq("撤销后不再认为配过", iina_setup.ipc_configured(), False)
    check("撤销时也保留了其它选项",
          ["vo", "gpu-next"] in iina_setup.current_options())


def test_refuse_when_running() -> None:
    print("\n== 4. IINA 在跑时必须拒绝写入 ==")
    iina_setup.iina_installed = lambda: True
    iina_setup.iina_running = lambda: True
    cleanup()
    ok, msg = iina_setup.configure("/tmp/dl_test_iina.sock")
    check("在跑 → 拒绝", not ok, msg)
    check("并且说清了要先退出", "退出" in msg, msg)
    eq("确认真的没写进去", iina_setup.ipc_configured(), False)

    print("\n== 5. dry_run 绝不落盘 ==")
    ok, msg = iina_setup.configure("/tmp/dl_test_iina.sock", dry_run=True)
    check("dry_run 返回计划内容", ok and "将写入" in msg, msg)
    eq("dry_run 之后偏好里仍然没有它", iina_setup.ipc_configured(), False)

    print("\n== 6. 没装 IINA 时的提示 ==")
    iina_setup.iina_installed = lambda: False
    ok, msg = iina_setup.configure("/tmp/dl_test_iina.sock")
    check("没装 → 不报错，只说明不用配", not ok and "没找到 IINA" in msg, msg)


def main() -> int:
    try:
        test_merge()
        test_write_readback()
        test_refuse_when_running()
    finally:
        cleanup()
    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
