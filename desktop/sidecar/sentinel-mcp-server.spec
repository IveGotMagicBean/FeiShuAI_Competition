# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：把 dashboard server 冻成 onefile 二进制。

跑法：
    cd 0427_test01
    pyinstaller --clean desktop/sidecar/sentinel-mcp-server.spec

输出：
    dist/sentinel-mcp-server[.exe]  (复制到 desktop/src-tauri/binaries/sentinel-mcp-server-<TARGET_TRIPLE>[.exe])

为什么要 spec 而不是 --onefile 命令行：
  - 要明确把 pwa_dashboard/templates + pwa_dashboard/static + sentinel_mcp/config
    打进 bundle，命令行 --add-data 跨平台路径分隔符不一致难受
  - hiddenimports 显式列：FastAPI / starlette / uvicorn 用到的子模块靠静态分析抓不全
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path
import sys

# 注意：spec 文件被 pyinstaller 当成 python 跑，__file__ 指向 spec 自己
SPEC_DIR = Path(SPECPATH).resolve()        # PyInstaller 注入的 SPECPATH
PROJECT_ROOT = SPEC_DIR.parent.parent       # 0427_test01/

# ---- 数据文件 ------------------------------------------------------
datas = [
    (str(PROJECT_ROOT / "pwa_dashboard" / "templates"), "pwa_dashboard/templates"),
    (str(PROJECT_ROOT / "pwa_dashboard" / "static"),    "pwa_dashboard/static"),
    (str(PROJECT_ROOT / "sentinel_mcp" / "config"),     "sentinel_mcp/config"),
]
# uvicorn / starlette 的内部资源
datas += collect_data_files("uvicorn")
datas += collect_data_files("starlette")
datas += collect_data_files("fastapi")

# ---- 隐式 import ---------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("uvicorn.protocols")
hiddenimports += collect_submodules("uvicorn.lifespan")
hiddenimports += collect_submodules("uvicorn.loops")
hiddenimports += collect_submodules("websockets")
hiddenimports += collect_submodules("watchfiles")
hiddenimports += collect_submodules("anyio")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")
hiddenimports += [
    "guard.audit",
    "guard.core",
    "guard.policies",
    "guard.sandbox",
    "guard.detectors.dlp",
    "guard.detectors.prompt_injection",
    "sentinel_mcp.approvals",
    "pwa_dashboard.server",
    "pwa_dashboard.push",
    "cryptography.hazmat.backends.openssl",
    "pywebpush",
]

block_cipher = None

a = Analysis(
    [str(SPEC_DIR / "bootstrap.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 砍掉绝对用不上的大头依赖
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "PySide6",
        "PyQt5",
        "PyQt6",
        "test",
        "tests",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sentinel-mcp-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # 保留控制台输出，方便用户看 uvicorn log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
