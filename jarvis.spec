# -*- mode: python ; coding: utf-8 -*-
"""
jarvis.spec — PyInstaller build definition.

Build with:
    jarvis-env\\Scripts\\pyinstaller.exe --noconfirm jarvis.spec

Produces dist\\Jarvis.exe: a single file that runs on a machine with no Python
installed. templates/ and static/ are bundled inside it; jarvis.db and
sync.log are created next to the .exe at runtime (see config.DATA_DIR).
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# pywebview drives Edge WebView2 through WebView2Loader.dll and the
# Microsoft.Web.WebView2.*.dll assemblies it ships in webview/lib. PyInstaller
# cannot see those through imports, so they are collected explicitly; without
# them the window fails to open and Jarvis falls back to the browser.
webview_datas = collect_data_files('webview')
webview_binaries = collect_dynamic_libs('webview')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=webview_binaries,
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ] + webview_datas,
    hiddenimports=[
        # Selected at runtime by config.OUTLOOK_BACKEND, so PyInstaller's
        # static analysis cannot see these imports.
        'win32com.client',
        'win32timezone',   # pywin32 needs this when unpickling COM datetimes
        'pythoncom',
        'pywintypes',
        'mock_outlook',
        # Imported lazily inside main() for --probe and --check-desktop.
        'probe',
        'redaction',
        'desktop',
        'desktop_check',
        'page_check',
        # The desktop window: pywebview's WebView2 backend, which it picks at
        # runtime, and the .NET bridge (pythonnet) that backend is built on.
        'webview',
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'clr',
        'clr_loader',
        'win32clipboard',
        'win32con',
        'werkzeug.serving',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # pywebview's other renderers. On Windows only WebView2 is used, and
        # bundling Qt or GTK would add ~100 MB for nothing.
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'qtpy',
        'gi',
        'tkinter',
        'matplotlib',
        'numpy',
        'PIL',
        'pytest',
        # pythonwin ships win32ui.pyd, which wants the MFC runtime. Jarvis
        # never uses it, and excluding it drops the build's only warning.
        'pythonwin',
        'win32ui',
        'win32uiole',
        'dde',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Jarvis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Kept: --probe and --check-desktop print to it, and in browser mode
    # closing it is how Jarvis is stopped. In window mode desktop.py hides it
    # once the window is up, so the app looks like an app.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
