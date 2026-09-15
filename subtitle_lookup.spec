# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Apple Silicon / arm64）。

构建：  pyinstaller --noconfirm --distpath <绝对路径> --workpath <绝对路径> subtitle_lookup.spec
产物：  <distpath>/SubtitleLookup.app（装到 /Applications 后再改成中文名「看剧查词-美剧」）

⚠️ --distpath 请用**绝对路径**：实测用相对路径时 BUNDLE 阶段会静默不产出 .app。
"""
import os
import re

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 版本号从 config.py 里读，别再手写 —— 之前手写 "1.1.0"，config 升到 1.2.0 之后
# 访达「显示简介」里还显示 1.1.0，对不上。
def _read_version() -> str:
    try:
        with open(os.path.join(SPECPATH, "config.py"), encoding="utf-8") as fh:
            m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
            if m:
                return m.group(1)
    except OSError:
        pass
    return "0.0.0"


_APP_VERSION = _read_version()
print(f"[spec] App 版本号 = {_APP_VERSION}")

# App 图标（Dock / 启动台 / 访达里显示的就是它）
# 由 `python tools/make_app_icons.py` 生成到 assets/AppIcon.icns
_icon = os.path.join(SPECPATH, "assets", "AppIcon.icns")
if not os.path.exists(_icon):
    _icon = None

# ★ v1.2.2：**没有任何额外数据文件了**。
#   · Playwright 的 node driver（几百 MB）随网页版豆包一起删除；
#   · 油猴脚本在 v1.2.0 就删了（网页视频通道整体去掉，只留 IINA）；
#   · assets/ 现在只剩图标，图标走下面的 icon= 参数，不进 datas。
#   所以这里保持空列表，别再往回加 collect_data_files("playwright")。
datas: list = []

# 这些包大量使用动态导入，必须显式声明
hidden = (
    collect_submodules("pynput")
    + [
        "rumps",
        "mss",
        "PIL.Image",
        "objc",
        "AppKit",
        "ApplicationServices",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.sip",
        # 这几个是在函数体里 import 的（延后导入），显式声明一遍最保险
        "settings",
        # region_picker 只在 panel 里**动态** import（点「框选…」才用），
        # PyInstaller 的静态扫描追不到，漏了就是"点框选没反应"且日志难查。
        "region_picker",
        # mouse 是顶层 import，但显式带上以防将来改成延后导入
        "mouse",
        # ↓ 这几个同样有"函数体里 import"的位置：
        #   iina_setup—— 一键给 IINA 开 IPC
        # 漏掉的症状是"按钮点了没反应"，很难从日志看出是打包问题。
        "iina_setup",
        "keytap",
        # ★ v1.2.1 新增：这两个也都是"在函数体里 import"的常客 ——
        #   reply_clean 在 app._finish_ok 里 import（做格式归一化）；
        #   viewlog 虽然 app.py 顶层就 import 了，但它是**新增模块**，
        #   显式写上，免得"桌面没有观看记录"这种症状被误判成逻辑 bug。
        "reply_clean",
        "viewlog",
    ]
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,                   # ← 现在是空的（v1.2.2 起无额外数据文件）
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 只用来瘦身：这些我们完全不用，留着会让 .app 大一倍
        "tkinter", "unittest", "pydoc", "doctest",
        "numpy", "pandas", "matplotlib", "scipy",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml",
        "PyQt6.QtQuick", "PyQt6.QtMultimedia", "PyQt6.Qt3DCore",
        "pygame", "IPython", "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubtitleLookup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # macOS 上 UPX 会破坏签名，必须关掉
    console=False,                   # 无终端窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",             # ★ Apple Silicon；Intel 机器改 "x86_64"
    codesign_identity=None,          # 交给 build.sh 里的 ad-hoc 签名
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SubtitleLookup",
)

app = BUNDLE(
    coll,
    name="SubtitleLookup.app",
    icon=_icon,                      # ★ assets/AppIcon.icns
    bundle_identifier="com.zhaowentian.subtitlelookup",
    version=_APP_VERSION,
    info_plist={
        # 注意：这里**故意**用 False。
        # LSUIElement=True 会让 App 不出现在启动台/Spotlight 里，用户根本搜不到它。
        # 所以让它在系统层面是个"正常 App"（启动台可见、能拖进 Dock），
        # 再由程序运行时把激活策略切成 Accessory 把 Dock 图标收起来 →
        # 既好找，又是干净的菜单栏应用。
        "LSUIElement": False,
        # 屏幕录制 / 辅助功能权限说明（TCC 弹窗里的文案）
        # ★ v1.2.2 起**不再申请 AppleScript/自动化权限** —— 那条权限是给
        #   "驱动 Chrome 打开网页版豆包"用的，随网页版一起删掉了。
        #   少一条权限申请 = 首次启动少一个系统弹窗。
        "NSScreenCaptureUsageDescription": "用于截取屏幕画面并提取字幕生词。",
        "NSAccessibilityUsageDescription": "用于监听你按下的触发键（默认空格）以触发字幕查词。",
        "NSInputMonitoringUsageDescription": "用于监听你按下的触发键（默认空格）以触发字幕查词。",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "12.0",
        "CFBundleDisplayName": "看剧查词-美剧",
        "LSApplicationCategoryType": "public.app-category.utilities",
    },
)
