# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 설정: Windows는 .exe 한 파일, macOS는 .app 묶음으로 만듭니다.
#   pyinstaller --noconfirm dart_app.spec
import sys

NAME = "DartSimpleInvest"
DISPLAY = "DART 단순투자"
IS_MAC = sys.platform == "darwin"
_v = {}
exec(open("version.py", encoding="utf-8").read(), _v)
VERSION = _v["__version__"]

a = Analysis(
    ["dart_app.py"],
    hiddenimports=["lxml", "lxml.etree", "lxml.html", "openpyxl"],
    excludes=["matplotlib", "IPython", "scipy", "PyQt5", "PyQt6", "PySide6", "pytest"],
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False,
              argv_emulation=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        bundle_identifier="com.qoodman.dartsimpleinvest",
        info_plist={
            "CFBundleDisplayName": DISPLAY,
            "CFBundleName": DISPLAY,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSAppSleepDisabled": True,      # 창을 가려도 작업이 느려지지 않게 (App Nap 끔)
            "LSMinimumSystemVersion": "11.0",
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, console=False, upx=False)
