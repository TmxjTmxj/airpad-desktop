# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = collect_data_files('mediapipe', include_py_files=False)
datas += [(str(root / 'models' / 'hand_landmarker.task'), 'models')]
for name in ('mediapipe', 'numpy', 'opencv-contrib-python', 'PySide6'):
    datas += copy_metadata(name)
a = Analysis([str(root / 'tools' / 'launch_frozen_debug.py')], pathex=[str(root)],
             binaries=[], datas=datas, hiddenimports=['airpad.self_check'],
             excludes=['pytest', 'IPython', 'notebook', 'tensorflow', 'jax', 'jaxlib',
                       'mediapipe.tasks.python.genai.converter',
                       'mediapipe.tasks.python.genai.bundler'],
             hookspath=[], runtime_hooks=[], noarchive=False,
             hooksconfig={'matplotlib': {'backends': ['Agg']}})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AIRPAD-DEBUG',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AIRPAD-Debug')
