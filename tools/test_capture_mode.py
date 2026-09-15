"""截图范围（config.CAPTURE_MODES + settings）的回归测试。

为什么值得单独测：
  截图裁错了会导致豆包"看不到字幕"，用户只会觉得"又失败了"，
  很难往"范围配置"上想。而且 crop_box 是纯函数，测起来最划算。

用法: python tools/test_capture_mode.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DL_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="dl_test_capture_")

import config  # noqa: E402
import settings  # noqa: E402
from screenshot import crop_box  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


W, H = 1440, 932


def main() -> int:
    # ---------------------------------------------------------- crop_box
    check("全屏 = 不裁剪", crop_box(W, H, "fullscreen") == (0, 0, W, H),
          str(crop_box(W, H, "fullscreen")))

    l, t, r, b = crop_box(W, H, "subtitle")
    check("字幕区只切纵向、保留整宽", l == 0 and r == W and t > 0 and b == H,
          f"{(l, t, r, b)}")
    check("字幕区从 40% 高度往下",
          t == round(H * config.CAPTURE_BANDS["subtitle"][0]), f"top={t}")

    l, t, r, b = crop_box(W, H, "bottom")
    check("下半屏 = 从中间到底部", (l, t, r, b) == (0, H // 2, W, H),
          str((l, t, r, b)))

    l, t, r, b = crop_box(W, H, "top")
    check("上半屏 = 从顶到中间", (l, t, r, b) == (0, 0, W, H // 2),
          str((l, t, r, b)))

    # 自定义区域（比例 → 像素）
    box = crop_box(W, H, "custom", [0.0, 0.0, 1.0, 1.0])
    check("自定义 100% 区域 = 整屏", box == (0, 0, W, H), str(box))
    box = crop_box(W, H, "custom", [0.5, 0.5, 0.5, 0.5])
    check("自定义右下角四分之一", box == (720, 466, 1440, 932), str(box))

    # ---- 边界：绝不能产出 0 尺寸 / 越界框（PIL 会直接抛异常）----
    for bad in ([0, 0, 0, 0], [1, 1, 0.5, 0.5], [-1, -1, 2, 2],
                [0.98, 0.98, 0.5, 0.5], ["a", "b", "c", "d"], None, [1, 2]):
        l, t, r, b = crop_box(W, H, "custom", bad)
        ok = (0 <= l < r <= W) and (0 <= t < b <= H)
        check(f"坏区域 {bad!r} → 夹紧成合法框", ok, str((l, t, r, b)))

    l, t, r, b = crop_box(0, 0, "subtitle")
    check("尺寸为 0 也不崩", r >= 1 and b >= 1, str((l, t, r, b)))

    # 未知模式 → 回落到默认，而不是崩
    l, t, r, b = crop_box(W, H, "nonsense")
    check("未知模式回落到默认范围", (r - l) > 0 and (b - t) > 0, str((l, t, r, b)))

    # ---------------------------------------------------------- settings
    check("默认范围是字幕区", settings.get_capture_mode() == "subtitle",
          settings.get_capture_mode())

    settings.set_capture_mode("fullscreen")
    check("可以改成整屏", settings.get_capture_mode() == "fullscreen")

    settings.set_capture_mode("not-a-mode")
    check("非法模式被拒绝（保持原值）", settings.get_capture_mode() == "fullscreen",
          settings.get_capture_mode())

    settings.set_capture_rect([0.1, 0.2, 0.3, 0.4])
    check("自定义区域能存能读", settings.get_capture_rect() == [0.1, 0.2, 0.3, 0.4],
          str(settings.get_capture_rect()))
    check("框过之后会记住（不再反复弹框选）",
          settings.load().get("capture_rect_set") is True)

    settings.set_capture_rect([5, 5, 5, 5])          # 越界
    r4 = settings.get_capture_rect()
    check("越界的自定义区域被拉回 0~1", all(0.0 <= v <= 1.0 for v in r4), str(r4))

    settings.set_capture_rect("garbage")
    check("类型不对 → 回默认区域",
          settings.get_capture_rect() == list(config.DEFAULT_CAPTURE_RECT))

    settings.set_capture_mode("custom")
    check("切到自定义模式", settings.get_capture_mode() == "custom")

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
