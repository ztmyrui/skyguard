# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata

retired_city_map = {'map.html', 'leaflet.js', 'leaflet.css', 'LICENSE',
                    'changsha-official-2024.jpg', 'map-provenance.json', 'changsha.geojson'}
datas = [(str(path), 'skyguard/assets') for path in Path('skyguard/assets').iterdir()
         if path.name not in retired_city_map]
hiddenimports = []
datas += copy_metadata('langgraph')
datas += copy_metadata('langchain-core')
datas += copy_metadata('langgraph-checkpoint')
datas += copy_metadata('langgraph-checkpoint-sqlite')
hiddenimports += collect_submodules('langgraph')
hiddenimports += collect_submodules('langgraph.checkpoint.sqlite')


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# System ICU and API-set forwarding DLLs must resolve from Windows itself.
# The build host's document runtime contains unrelated ICU DLLs on PATH.
a.binaries = [item for item in a.binaries
              if item[0].lower() not in {'icuuc.dll', 'icuin.dll', 'icu.dll'}
              and not item[0].lower().startswith(('api-ms-win-', 'ext-ms-win-'))]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Skyguard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['skyguard/assets/skyguard.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Skyguard',
)
