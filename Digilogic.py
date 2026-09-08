"""Punto de entrada de la aplicación de macOS Digilogic."""

import sys

from PyQt6.QtWidgets import QApplication

from reproductor import Reproductor


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ventana = Reproductor()
    ventana.show()
    sys.exit(app.exec())
