"""
Configuración para empaquetar el reproductor como app de macOS con py2app.
"""
from setuptools import setup

APP = ['Digilogic.py']
OPTIONS = {
    'argv_emulation': False,
    'packages': ['PyQt6'],
    # MediaPlayer y objc se importan dentro de un try/except, y py2app no
    # siempre sigue esas ramas. Sin nombrarlos aquí el .app se compila sin
    # ellos y, ya empaquetado, se pierden las teclas de reproducción del
    # teclado y la ficha del Centro de Control.
    'includes': ['objc', 'Foundation', 'MediaPlayer'],
    # Lo contrario: módulos que NO deben viajar dentro del .app aunque
    # estén en la carpeta del proyecto. 'descargas' y 'portadas' son
    # privados y no forman parte de Digilogic tal y como se publica;
    # como 'reproductor.py' los importa dentro de un try/except, py2app
    # los sigue igualmente y acababan metidos en el paquete, con lo que
    # el .dmg salía con el botón de descargas de YouTube dentro. Sin
    # ellos, el try falla, 'descargas' queda en None y la aplicación se
    # comporta exactamente como la versión pública, que es lo suyo.
    'excludes': ['descargas', 'portadas', 'yt_dlp'],
    # Todo lo que 'ruta_recurso' busque en tiempo de ejecución tiene que
    # estar aquí, o dentro del .app el icono no se encontraría.
    'resources': ['Nota-musica.svg', 'icono aleatorio.png', 'icono lista.png'],
    'iconfile': 'Digilogic.icns',
    'plist': {
        'CFBundleName': 'Digilogic',
        'CFBundleDisplayName': 'Digilogic',
        'CFBundleGetInfoString': "Reproductor MP3 Digilogic",
        'CFBundleIdentifier': "com.daniel.digilogic",
        'CFBundleVersion': "1.1.0",
        'CFBundleShortVersionString': "1.1.0",
        'NSHumanReadableCopyright': "Daniel",
        # Lo marca Qt 6, que exige macOS 13.0 (el intérprete de python.org
        # con el que se compila para distribuir admite desde macOS 11).
        # Declararlo hace que un Mac más antiguo dé un aviso claro al
        # instalar, en vez de dejar que la app se cierre sin explicación.
        # IMPORTANTE: compilar el .dmg con el Python de python.org, no con
        # el de Homebrew: ese solo funciona en la versión de macOS del
        # equipo donde se compila (ver README).
        'LSMinimumSystemVersion': "13.0",
    }
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
