"""悬浮窗「位置 + 大小记忆」的回归测试 —— 纯逻辑，不用真开窗口。

覆盖用户诉求：「悬浮窗出现的位置要和它上次出现的位置和大小是一样的」。

测两件事：
  A. settings 的读写（含坏数据清洗、旧字段兼容）；
  B. overlay.fit_geometry 的位置换算 —— 这是最容易出错、也最该被钉死的部分：
     · 正常情况原样恢复
     · 没记录过位置 → 用默认（底部居中）
     · 换分辨率 / 缩放比 → 按比例平移，不跑出屏幕
     · 拔掉外接屏（记录的那块屏没了）→ 夹紧回可见区域
     · 副屏（负坐标 / 大偏移）也能正确认出来

用法: python tools/test_overlay_geometry.py   （退出码 0 = 全过）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 必须在 import config 之前：把设置文件挪到临时目录，别碰用户真实配置
_TMP = tempfile.mkdtemp(prefix="dl_test_geo_")
os.environ["DL_SETTINGS_DIR"] = _TMP

import config  # noqa: E402
import settings  # noqa: E402
from overlay import fit_geometry, pick_screen  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


# 常用屏幕：主屏 1440x880（左上角 y=25，模拟菜单栏占位），副屏在右边
MAIN = (0, 25, 1440, 880)
RIGHT = (1440, 0, 1920, 1080)
SINGLE = [MAIN]


def test_settings_roundtrip() -> None:
    settings.reset_overlay_geometry()
    check("初始状态没有几何记录", settings.get_overlay_geo() == {})

    settings.set_overlay_size(700, 420)
    geo = settings.get_overlay_geo()
    check("只记尺寸时：宽高存下来了", (geo.get("w"), geo.get("h")) == (700, 420))
    check("只记尺寸时：位置留空（用默认摆法）", "x" not in geo)
    check("get_overlay_size 能读到", settings.get_overlay_size() == (700, 420))

    settings.set_overlay_pos(300, 500, {"x": 0, "y": 25, "w": 1440, "h": 880})
    geo = settings.get_overlay_geo()
    check("补记位置后：只加了 x/y/screen，尺寸没丢",
          (geo.get("x"), geo.get("y"), geo.get("w"), geo.get("h")) == (300, 500, 700, 420))
    check("屏幕信息一起存了", geo.get("screen", {}).get("h") == 880)

    settings.reset_overlay_geometry()
    check("重置后回到空", settings.get_overlay_geo() == {})

    # 坏数据不能把 App 搞崩，也不能留下半截几何
    settings.save({"overlay_geo": {"w": "abc", "h": 400, "x": 1, "y": 2}})
    geo = settings.get_overlay_geo()
    check("坏尺寸 → 丢掉尺寸，但位置留着（逐字段清洗）",
          (geo.get("x"), geo.get("y")) == (1, 2) and "w" not in geo)
    settings.save({"overlay_geo": {"w": "a", "h": "b", "x": "c", "y": "d"}})
    check("全坏 → 整体作废", settings.get_overlay_geo() == {})
    settings.save({"overlay_geo": {"w": 10, "h": 10}})
    geo = settings.get_overlay_geo()
    check("尺寸小于最小值 → 抬到最小值",
          geo.get("w") == config.OVERLAY_MIN_WIDTH
          and geo.get("h") == config.OVERLAY_MIN_HEIGHT)
    settings.save({"overlay_geo": []})
    check("类型不对（list）→ 作废，且不许被旧尺寸复活",
          settings.get_overlay_geo() == {} and settings.get_overlay_size() is None)

    # 旧版本只写过 overlay_size，要能兼容过来
    settings.reset_overlay_geometry()
    settings.save({"overlay_size": [640, 300]})
    check("兼容旧字段 overlay_size", settings.get_overlay_size() == (640, 300))
    settings.reset_overlay_geometry()

    # ★ 用户「只拖位置、从不缩放」也必须能记住（曾经在这里丢过位置）
    settings.set_overlay_pos(500, 400, {"x": 0, "y": 25, "w": 1440, "h": 880})
    geo = settings.get_overlay_geo()
    check("只记位置、没有尺寸时位置不能丢",
          (geo.get("x"), geo.get("y")) == (500, 400), f"得到 {geo}")
    check("fit_geometry 能直接用它", fit_geometry(geo, 620, 320, SINGLE) == (500, 400))
    check("位置太靠下 → 会被夹到可见区域内（不许露出屏幕底部）",
          fit_geometry({"x": 500, "y": 600, "screen": {"x": 0, "y": 25, "w": 1440, "h": 880}},
                       620, 320, SINGLE) == (500, 585))
    settings.reset_overlay_geometry()

    # 只重置尺寸时要保留位置（菜单里那个「重置悬浮窗大小」）
    settings.set_overlay_pos(240, 300, {"x": 0, "y": 25, "w": 1440, "h": 880})
    settings.set_overlay_size(700, 420)
    settings.reset_overlay_size()
    geo = settings.get_overlay_geo()
    check("重置尺寸后位置还在、尺寸没了",
          (geo.get("x"), geo.get("y")) == (240, 300)
          and "w" not in geo and settings.get_overlay_size() is None)
    settings.reset_overlay_geometry()


def test_fit_geometry() -> None:
    W, H = 620, 320
    # ① 正常情况：原样恢复
    geo = {"x": 300, "y": 500, "w": W, "h": H,
           "screen": {"x": 0, "y": 25, "w": 1440, "h": 880}}
    check("同屏同分辨率 → 原样恢复",
          fit_geometry(geo, W, H, SINGLE) == (300, 500))

    # ② 完全没有位置记录 → None（调用方会用默认摆法）
    check("没有位置记录 → None", fit_geometry({"w": W, "h": H}, W, H, SINGLE) is None)
    check("空 dict → None", fit_geometry({}, W, H, SINGLE) is None)

    # ③ 分辨率变大：按相对比例平移（不是死用旧坐标）
    geo = {"x": 720, "y": 465, "w": W, "h": H,
           "screen": {"x": 0, "y": 0, "w": 1440, "h": 880}}
    big = [(0, 0, 2880, 1760)]
    x, y = fit_geometry(geo, W, H, big)
    check("分辨率翻倍 → 位置按比例跟着走（x≈1440）", abs(x - 1440) <= 2, f"得到 {x}")

    # ④ 屏幕变小：位置被夹紧，绝不跑出可见区域
    geo = {"x": 1300, "y": 800, "w": W, "h": H,
           "screen": {"x": 0, "y": 0, "w": 1440, "h": 880}}
    small = [(0, 0, 800, 600)]
    x, y = fit_geometry(geo, W, H, small)
    check("屏幕变小 → 夹紧在可见区域里",
          x <= 800 - W and y <= 600 - H and x >= 0 and y >= 0, f"得到 ({x}, {y})")

    # ⑤ 拔掉外接屏：记录的那块屏没了 → 夹回主屏
    geo = {"x": 2000, "y": 400, "w": W, "h": H,
           "screen": {"x": 1440, "y": 0, "w": 1920, "h": 1080}}
    x, y = fit_geometry(geo, W, H, SINGLE)
    check("外接屏拔掉 → 回到主屏可见范围",
          0 <= x <= 1440 - W and 25 <= y <= 25 + 880 - H, f"得到 ({x}, {y})")

    # ⑥ 点在副屏上 → 认得出副屏，不改动
    geo = {"x": 2000, "y": 400, "w": W, "h": H,
           "screen": {"x": 1440, "y": 0, "w": 1920, "h": 1080}}
    check("副屏还在 → 原样留在副屏",
          fit_geometry(geo, W, H, [MAIN, RIGHT]) == (2000, 400))

    # ⑦ 副屏在左边（负坐标）：也要能认出来
    left = [(-1920, 0, 1920, 1080), MAIN]
    geo = {"x": -1500, "y": 300, "w": W, "h": H,
           "screen": {"x": -1920, "y": 0, "w": 1920, "h": 1080}}
    check("左侧副屏（负坐标）也能认出", fit_geometry(geo, W, H, left) == (-1500, 300))

    # ⑧ 尺寸比屏幕还大 → 夹到屏幕左上角，而不是负到看不见
    geo = {"x": 100, "y": 100, "w": 620, "h": 320,
           "screen": {"x": 0, "y": 0, "w": 1440, "h": 880}}
    x, y = fit_geometry(geo, 2000, 2000, [(0, 0, 1440, 880)])
    check("窗口比屏幕大 → 夹到左上角", (x, y) == (0, 0), f"得到 ({x}, {y})")

    # ⑨ pick_screen 的兜底：点不在任何屏上 → **一律回主屏**（screens 里第一个）
    #
    # 【为什么这条这么写】这里原本是"挑和原来一样大的那块屏"，多显示器时严重
    # 误判：副屏分辨率跟主屏一样（很常见）时，浮窗会被摆到副屏上去。实测抓到过
    # 浮窗落在 x=-680 的左边屏，主屏上完全看不到，用户以为「按了没反应」。
    # 所以现在的约定是：screens[0] 必须是主屏，兜底只认它。
    scr = pick_screen(9999, 9999, [MAIN, RIGHT], {"w": 1920, "h": 1080})
    check("点不在任何屏幕 → 回主屏（不看尺寸相同的那块）", scr == MAIN,
          f"得到 {scr}")
    same_size = [MAIN, (-1440, 0, 1440, 880)]     # 左边还有一块同尺寸的屏
    scr = pick_screen(9999, 9999, same_size, {"w": 1440, "h": 880})
    check("副屏与主屏同尺寸时 → 仍然回主屏，绝不跑到负坐标的副屏", scr == MAIN,
          f"得到 {scr}")

    # ⑨b 真机场景还原：上次存在主屏 (756, 303)，但这次只有一块主屏可用，
    #     必须落在主屏可见区域内（而不是乘以比例跑到副屏）。
    geo = {"w": W, "h": H, "x": 756, "y": 303,
           "screen": {"x": 0, "y": 25, "w": 1440, "h": 880}}
    x, y = fit_geometry(geo, 480, 399, [MAIN])
    check("单主屏时位置落在主屏内（不会跑到负坐标的副屏）",
          0 <= x <= 1440 - 480 and 25 <= y <= 25 + 880 - 399, f"得到 ({x}, {y})")

    check("一块屏都没有 → None", pick_screen(0, 0, [], None) is None)


def test_geometry_survives_reload() -> None:
    """写盘再读回来（模拟"关掉 App 明天再打开"）。"""
    settings.reset_overlay_geometry()
    settings.set_overlay_pos(412, 420, {"x": 0, "y": 25, "w": 1440, "h": 880})
    settings.set_overlay_size(684, 356)
    settings.load(force=True)          # 丢掉内存缓存，强制从文件读
    geo = settings.get_overlay_geo()
    check("重启后位置还在", (geo.get("x"), geo.get("y")) == (412, 420))
    check("重启后尺寸还在", (geo.get("w"), geo.get("h")) == (684, 356))
    check("fit_geometry 用它算出来的位置一致",
          fit_geometry(geo, 684, 356, SINGLE) == (412, 420))
    settings.reset_overlay_geometry()


def main() -> int:
    print(f"设置文件（临时）: {config.SETTINGS_FILE}\n")
    test_settings_roundtrip()
    print()
    test_fit_geometry()
    print()
    test_geometry_survives_reload()

    bad = [n for n, ok in results if not ok]
    print(f"\n共 {len(results)} 项，失败 {len(bad)} 项")
    for n in bad:
        print("  ✗", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
