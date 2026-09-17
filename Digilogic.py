"""Punto de entrada de la aplicación Digilogic (macOS y Windows)."""

import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from reproductor import Reproductor, asegurar_tls, ruta_recurso


def _preparar_windows(app):
    """Icono de la app en la barra de tareas y en la ventana. En macOS lo
    pone el propio .app (Info.plist), pero en Windows hay que dárselo a
    Qt: sin esto la barra de tareas muestra el icono genérico de Python
    al ejecutar desde el código, y el del .exe solo por casualidad."""
    if sys.platform != "win32":
        return

    # Windows agrupa las ventanas en la barra de tareas por este
    # identificador. Sin uno propio, al ejecutar desde el código la
    # ventana se agrupa bajo "python.exe" y hereda su icono.
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Digilogic.Reproductor")

    app.setWindowIcon(QIcon(ruta_recurso("Digilogic.ico")))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Antes de nada: Qt fija el motor de cifrado la primera vez que
    # alguien lo usa, y en Windows el que elige solo puede no servir.
    asegurar_tls()
    _preparar_windows(app)
    ventana = Reproductor()
    ventana.show()
    sys.exit(app.exec())
