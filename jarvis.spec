# -*- mode: python ; coding: utf-8 -*-
"""
jarvis.spec — PyInstaller build definition.

Build with:
    jarvis-env\\Scripts\\pyinstaller.exe --noconfirm jarvis.spec

Produces dist\\Jarvis.exe: a single file that runs on a machine with no Python
installed. templates/ and static/ are bundled inside it; jarvis.db and
sync.log are created next to the .exe at runtime (see config.DATA_DIR).
"""

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        # Selected at runtime by config.OUTLOOK_BACKEND, so PyInstaller's
        # static analysis cannot see these imports.
        'win32com.client',
        'win32timezone',   # pywin32 needs this when unpickling COM datetimes
        'pythoncom',
        'pywintypes',
        'mock_outlook',
        # Imported lazily inside main() for --probe.
        'probe',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    console=True,          # keeps the sync log visible; set False for silent
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
