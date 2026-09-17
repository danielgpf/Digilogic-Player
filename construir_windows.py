"""
Empaqueta Digilogic como aplicación de Windows (.exe) con PyInstaller.

En macOS se usa py2app (ver setup.py); en Windows, PyInstaller. Son dos
herramientas distintas porque cada sistema empaqueta de forma diferente,
pero el código del reproductor es el mismo: 'ruta_recurso' ya sabe
encontrar los recursos en los dos empaquetados.

IMPORTANTE: un .exe solo se puede generar DESDE Windows. No se puede
compilar para Windows desde un Mac.

Uso, en Windows:

    python -m venv venv
    venv\\Scripts\\pip install -r requirements.txt
    venv\\Scripts\\pip install pyinstaller
    venv\\Scripts\\python construir_windows.py

El resultado queda en dist\\Digilogic.exe: un único archivo, sin carpeta
ni nada que descomprimir. Es lo que se sube a la release de GitHub y lo
que descarga la gente desde la web.
"""

import os
import subprocess
import sys


# Archivos que la aplicación carga en tiempo de ejecución con
# 'ruta_recurso'. Si añades uno nuevo al reproductor, súmalo también aquí
# o dentro del .exe no se encontrará.
RECURSOS = [
    "Nota-musica.svg",
    "icono aleatorio.png",
    "icono lista.png",
    # Icono de la ventana y la barra de tareas (Digilogic.py lo carga al
    # arrancar). El de "--icon" solo marca el archivo .exe en el Explorador.
    "Digilogic.ico",
]

NOMBRE = "Digilogic"
ICONO = "Digilogic.ico"
ENTRADA = "Digilogic.py"


def comprobar_cifrado():
    """Avisa si el .exe va a salir sin poder hablar por HTTPS.

    La radio pide las emisoras a un servidor por HTTPS, así que necesita
    los conectores de cifrado de Qt (la carpeta de complementos 'tls').
    PyInstaller los mete solo desde la versión 6, y si faltaran, la radio
    saldría vacía dentro del .exe mientras funciona perfectamente al
    ejecutar el código a mano: de los fallos más difíciles de encontrar.

    En Windows el reproductor usa siempre 'qschannelbackend', el cifrado
    del propio sistema (ver 'asegurar_tls' en reproductor.py). El de
    OpenSSL viaja también, pero no sirve de nada sin las bibliotecas de
    OpenSSL al lado, y PyQt no las distribuye. Así que lo único que hay
    que comprobar es que el de schannel esté.
    """
    from PyQt6.QtCore import QLibraryInfo

    from reproductor import MOTOR_TLS_WINDOWS

    carpeta = os.path.join(
        QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath), "tls"
    )
    conectores = sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []

    print("Cifrado (hace falta para la radio):")
    print("  conectores:", ", ".join(conectores) if conectores else "NINGUNO")

    if any(MOTOR_TLS_WINDOWS in c for c in conectores):
        print("  -> hay %s, el cifrado del propio Windows: la radio irá.\n" % MOTOR_TLS_WINDOWS)
        return True

    print(
        "\n  AVISO: no está 'q%sbackend.dll'. El .exe se generará igual,\n"
        "  pero dentro de él la radio dirá 'Radio no disponible en este\n"
        "  equipo'. Todo lo demás funcionará con normalidad.\n" % MOTOR_TLS_WINDOWS
    )
    return False


def main():
    if sys.platform != "win32":
        print(
            "Este script solo funciona en Windows.\n"
            "En macOS se empaqueta con py2app:  python setup.py py2app"
        )
        return 1

    faltan = [r for r in RECURSOS + [ICONO, ENTRADA] if not os.path.exists(r)]
    if faltan:
        print("Faltan archivos necesarios:")
        for f in faltan:
            print("  -", f)
        return 1

    comprobar_cifrado()

    orden = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        # Todo en un único .exe, en vez de una carpeta con el ejecutable
        # y sus DLL al lado. Así se puede subir tal cual a la release y
        # quien lo descarga no tiene que descomprimir nada.
        #
        # El precio es el arranque: un .exe de un solo archivo se
        # descomprime en una carpeta temporal cada vez que se abre, así
        # que tarda unos segundos más que la versión en carpeta. Merece
        # la pena por lo mucho que simplifica la descarga.
        "--onefile",
        # Sin consola: si no, Windows abriría una ventana negra detrás.
        "--windowed",
        "--name", NOMBRE,
        "--icon", ICONO,
    ]

    # PyInstaller usa ';' como separador en Windows (en Linux/macOS es ':').
    for recurso in RECURSOS:
        orden += ["--add-data", f"{recurso};."]

    orden.append(ENTRADA)

    print("Ejecutando:", " ".join(orden), "\n")
    resultado = subprocess.run(orden)

    if resultado.returncode == 0:
        print(f"\nListo: dist\\{NOMBRE}.exe")
    return resultado.returncode


if __name__ == "__main__":
    raise SystemExit(main())
