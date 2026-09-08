"""
Configuración para empaquetar el reproductor como app de macOS con py2app.
"""
from setuptools import setup

APP = ['Digilogic.py']
OPTIONS = {
    'argv_emulation': False,
    'packages': ['PyQt6'],
    # Todo lo que 'ruta_recurso' busque en tiempo de ejecución tiene que
    # estar aquí, o dentro del .app el icono no se encontraría.
    'resources': ['Nota-musica.svg', 'icono aleatorio.png'],
    'iconfile': 'Digilogic.icns',
    'plist': {
        'CFBundleName': 'Digilogic',
        'CFBundleDisplayName': 'Digilogic',
        'CFBundleGetInfoString': "Reproductor MP3 Digilogic",
        'CFBundleIdentifier': "com.daniel.digilogic",
        'CFBundleVersion': "1.0.0",
        'CFBundleShortVersionString': "1.0.0",
        'NSHumanReadableCopyright': "Daniel",
    }
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
