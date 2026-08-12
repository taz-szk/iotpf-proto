# -*- mode: python ; coding: utf-8 -*-
import os

sdk_path = os.path.abspath(os.path.join(SPECPATH, '..', 'sdk', 'python'))

a = Analysis(
    ['simulator.py'],
    pathex=[SPECPATH, sdk_path],
    binaries=[],
    datas=[],
    hiddenimports=[
        'paho.mqtt',
        'paho.mqtt.client',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'tkinter',
        'tkinter.ttk',
        'tkinter.scrolledtext',
        'tkinter.messagebox',
        'tkinter.simpledialog',
        'device_worker',
        'iot_platform',
        'iot_platform.client',
        'iot_platform.provisioning',
        'iot_platform.ota',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='IoT_Simulator',
    debug=False,
    strip=False,
    upx=False,
    console=False,      # GUIアプリ：コンソールウィンドウを表示しない
    runtime_tmpdir=None,
)
