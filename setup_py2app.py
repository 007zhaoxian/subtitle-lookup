"""py2app 备选方案（如果你更偏好 py2app 而不是 PyInstaller）。

    python setup_py2app.py py2app          # 开发构建（别名，快，能直接跑）
    python setup_py2app.py py2app -A       # 同上
    python setup_py2app.py py2app          # 发布构建（独立，体积大但自洽）

产物：dist/DoubaoLookup.app
注意：py2app 打包 PyQt6 的钩子不如 PyInstaller 成熟，
      若报 ModuleNotFoundError，把缺的模块名补进 packages / includes。
"""
from setuptools import setup

APP = ["main.py"]
OPTIONS = {
    "argv_emulation": False,          # 我们的 app 不处理 open-document 事件
    "iconfile": None,                 # 有 .icns 时填路径
    "plist": {
        "CFBundleName": "DoubaoLookup",
        "CFBundleDisplayName": "豆包查词",
        "CFBundleIdentifier": "com.zhaowentian.doubaolookup",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,          # 只驻留菜单栏
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "12.0",
        "NSAppleEventsUsageDescription": "用于控制浏览器完成豆包查词。",
        "NSScreenCaptureUsageDescription": "用于截取屏幕画面并提取字幕生词。",
        "NSAccessibilityUsageDescription": "用于监听触发键以识别字幕生词。",
        "NSInputMonitoringUsageDescription": "用于监听触发键以识别字幕生词。",
    },
    "packages": [
        "rumps", "pynput", "PyQt6", "mss", "PIL",
        "objc", "AppKit", "ApplicationServices",
    ],
    "includes": [
        "pynput.keyboard._darwin", "pynput.mouse._darwin", "pynput._util.darwin",
        "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.sip",
    ],
    "excludes": [
        "tkinter", "numpy", "pandas", "matplotlib",
        "PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick",
    ],
    "plist_extra": {},
}

setup(
    app=APP,
    name="DoubaoLookup",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
