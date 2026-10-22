# -*- mode: python ; coding: utf-8 -*-
# 单独的安装器打包配置：install.py -> 安装水果刀.exe
a = Analysis(
    ['install.py'],
    pathex=[],
    binaries=[],
    datas=[('app_icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['PyQt5', 'numpy', 'PIL'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='安装水果刀',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,          # 安装器要看得见输出
    icon='app_icon.ico',
    disable_windowed_traceback=False,
)
