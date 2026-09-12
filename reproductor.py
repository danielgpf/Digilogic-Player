"""
Reproductor MP3 - Estilo "iPod + Cristal líquido"
---------------------------------------------------
- Elige una carpeta (puede estar en un USB) y reproduce los MP3 directamente desde ahí,
  sin copiarlos a ningún sitio.
- Muestra la portada del álbum si el MP3 la tiene incrustada.
- Si no hay portada, muestra una onda de audio animada estilo Siri (3 ondas RGB que
  se mezclan aditivamente, con desenfoque/glow).
- Ventana sin bordes, con esquinas redondeadas "de verdad" (recortadas con máscara)
  y efecto translúcido tipo "vidrio".
"""

import sys
import os
import math
import random
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem, QSlider,
    QStackedLayout, QGraphicsOpacityEffect, QGraphicsBlurEffect, QStyle,
    QGraphicsScene, QGraphicsPixmapItem, QLineEdit
)
from PyQt6.QtCore import (
    Qt, QUrl, QTimer, QRect, QRectF, QPointF, QSize, QEvent, QSettings,
    pyqtSignal, QPropertyAnimation, QEasingCurve, QVariantAnimation
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QPainterPath, QPainterPathStroker, QColor, QFont,
    QRegion, QPen, QFontMetrics, QTransform, QImage, QBitmap, QLinearGradient
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

try:
    import mutagen
except ImportError:
    mutagen = None

# Integración con el centro multimedia de macOS. Es lo que hace que las
# teclas de reproducción del teclado lleguen a Digilogic aunque la
# ventana no tenga el foco, y que la canción aparezca en el Centro de
# Control y en la pantalla bloqueada.
#
# Opcional a propósito, igual que mutagen: si pyobjc no está instalado
# -o estamos en Windows- el reproductor funciona exactamente igual, solo
# que sin esa integración.
try:
    from MediaPlayer import (
        MPRemoteCommandCenter,
        MPNowPlayingInfoCenter,
        MPMediaItemPropertyTitle,
        MPMediaItemPropertyArtist,
        MPMediaItemPropertyPlaybackDuration,
        MPNowPlayingInfoPropertyElapsedPlaybackTime,
        MPNowPlayingInfoPropertyPlaybackRate,
        MPNowPlayingPlaybackStatePlaying,
        MPNowPlayingPlaybackStatePaused,
    )
except ImportError:
    MPRemoteCommandCenter = None

# Módulo opcional: si 'descargas.py' no está presente, el reproductor
# funciona igual pero sin poder descargar de YouTube. La barra de arriba
# se queda entonces como un simple buscador de la música que ya tienes.
# Esta es la única diferencia entre la versión completa y la pública.
try:
    import descargas
except ImportError:
    descargas = None


# ------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------

# Iconos compartidos con MusicPi: son exactamente los mismos archivos que
# usa el reproductor web de la Raspberry Pi, para que las dos aplicaciones
# se vean como una sola familia.
ARCHIVO_ICONO_ALEATORIO = "icono aleatorio.png"

# Iconos ya teñidos y escalados, cacheados por (archivo, lado, color).
# El teñido implica crear y pintar dos QPixmap, y paintEvent se ejecuta
# constantemente, así que sin caché se reharía el mismo trabajo en cada
# repintado del botón.
_iconos_tenidos = {}

# Los mismos iconos ya recortados a su contenido, cacheados por archivo.
_iconos_recortados = {}


def _icono_recortado(nombre):
    """
    Devuelve el PNG sin el margen transparente que lo rodea. Estos iconos
    vienen en lienzos de 1024x1024 donde el dibujo ocupa apenas la mitad;
    escalarlos tal cual dejaría el icono diminuto dentro de su botón y
    obligaría a ajustar un factor distinto para cada archivo. Recortando,
    el tamaño que se pide es el que de verdad se ve.
    """
    if nombre in _iconos_recortados:
        return _iconos_recortados[nombre]

    original = QPixmap(ruta_recurso(nombre))
    if original.isNull():
        _iconos_recortados[nombre] = QPixmap()
        return _iconos_recortados[nombre]

    # La caja se busca sobre una miniatura: recorrer el millón de píxeles
    # del original desde Python tardaría casi un segundo, y a esta escala
    # la precisión sobra de todos modos.
    imagen = original.toImage()
    muestra = imagen.scaled(
        128, 128,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    min_x, min_y, max_x, max_y = muestra.width(), muestra.height(), -1, -1
    for y in range(muestra.height()):
        for x in range(muestra.width()):
            if muestra.pixelColor(x, y).alpha() > 20:
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)

    if max_x < 0:  # imagen completamente transparente
        _iconos_recortados[nombre] = original
        return original

    factor_x = imagen.width() / muestra.width()
    factor_y = imagen.height() / muestra.height()
    caja = QRect(
        int(min_x * factor_x),
        int(min_y * factor_y),
        max(1, int((max_x - min_x + 1) * factor_x)),
        max(1, int((max_y - min_y + 1) * factor_y)),
    )

    _iconos_recortados[nombre] = original.copy(caja)
    return _iconos_recortados[nombre]


def icono_tenido(nombre, lado, color, escala_pantalla=1.0):
    """
    Carga un PNG y lo usa como plantilla de recorte: se conserva su forma
    (el canal alfa) pero se pinta del color que se pida. Es la misma
    técnica que MusicPi aplica por CSS con 'mask-image' + 'currentColor',
    y permite reutilizar sus mismos iconos aquí sin tener una copia de
    cada uno en blanco, en azul y en gris.

    'escala_pantalla' es el factor de la pantalla (2 en las Retina). Hay
    que generar el mapa de bits a esa resolución real: si se crea al
    tamaño lógico, macOS lo amplía al doble y el icono se ve borroso y
    con el trazo desvaído, como si estuviera mal dibujado.
    """
    clave = (nombre, lado, color.rgba(), escala_pantalla)
    if clave in _iconos_tenidos:
        return _iconos_tenidos[clave]

    original = _icono_recortado(nombre)
    if original.isNull():
        _iconos_tenidos[clave] = QPixmap()
        return _iconos_tenidos[clave]

    lado_fisico = max(1, round(lado * escala_pantalla))
    escalado = original.scaled(
        lado_fisico, lado_fisico,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    resultado = QPixmap(escalado.size())
    resultado.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(resultado)
    pintor.drawPixmap(0, 0, escalado)
    # SourceIn deja el color solo donde el icono tiene forma: el dibujo
    # actúa de molde y el relleno toma el color pedido.
    pintor.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    pintor.fillRect(resultado.rect(), color)
    pintor.end()

    # Con esto Qt sabe que el mapa de bits tiene el doble de píxeles de lo
    # que ocupa en pantalla, y lo dibuja nítido en vez de estirarlo.
    resultado.setDevicePixelRatio(escala_pantalla)

    _iconos_tenidos[clave] = resultado
    return resultado


def formatear_tiempo(milisegundos):
    """Convierte milisegundos a un texto tipo 'm:ss'."""
    segundos_totales = int(milisegundos / 1000)
    minutos = segundos_totales // 60
    segundos = segundos_totales % 60
    return f"{minutos}:{segundos:02d}"


def ruta_recurso(nombre):
    """
    Localiza un recurso (icono, SVG) tanto al ejecutar el código suelto
    como dentro de la aplicación ya empaquetada.

    Cada empaquetador coloca los recursos en un sitio distinto, así que
    hay que probarlos todos:
      - macOS con py2app: dentro del .app, en Contents/Resources.
      - Windows con PyInstaller: en una carpeta temporal que el propio
        ejecutable crea al arrancar y cuya ruta deja en sys._MEIPASS.
    """
    candidatas = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), nombre)
    ]

    if getattr(sys, "frozen", False):
        # PyInstaller (Windows y también macOS si algún día se usa ahí).
        carpeta_temporal = getattr(sys, "_MEIPASS", None)
        if carpeta_temporal:
            candidatas.append(os.path.join(carpeta_temporal, nombre))

        carpeta_ejecutable = os.path.dirname(sys.executable)
        # py2app: el ejecutable vive en Contents/MacOS y los recursos en
        # Contents/Resources, un nivel por encima.
        candidatas.append(
            os.path.normpath(os.path.join(carpeta_ejecutable, "..", "Resources", nombre))
        )
        # PyInstaller en modo carpeta: junto al propio ejecutable.
        candidatas.append(os.path.join(carpeta_ejecutable, nombre))

    for ruta in candidatas:
        if os.path.exists(ruta):
            return ruta

    # Ninguna existe: se devuelve la primera para que el error, si lo hay,
    # apunte a la ruta esperada durante el desarrollo.
    return candidatas[0]


def extraer_portada(ruta_mp3):
    """Lee la portada incrustada en un MP3 (si la tiene). Devuelve QPixmap o None."""
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(ruta_mp3)
        if audio is None or audio.tags is None:
            return None
        for clave in audio.tags.keys():
            if str(clave).startswith("APIC"):
                datos_imagen = audio.tags[clave].data
                pixmap = QPixmap()
                if pixmap.loadFromData(datos_imagen):
                    return pixmap
    except Exception:
        pass
    return None


def extraer_metadatos(ruta_mp3):
    """Devuelve (titulo, artista). Si no hay etiquetas, usa el nombre del archivo."""
    titulo = os.path.splitext(os.path.basename(ruta_mp3))[0]
    artista = ""
    if mutagen is not None:
        try:
            audio = mutagen.File(ruta_mp3, easy=True)
            if audio and audio.tags:
                if audio.tags.get("title"):
                    titulo = audio.tags["title"][0]
                if audio.tags.get("artist"):
                    artista = audio.tags["artist"][0]
        except Exception:
            pass
    return titulo, artista


# ------------------------------------------------------------------
# Onda animada estilo Siri "clásico": cintas de luz finas y limpias,
# con mezcla aditiva SOLO donde se cruzan, y un glow muy localizado
# (varios trazos concéntricos alrededor de la línea, no un desenfoque
# de todo el widget) para que la silueta se mantenga siempre nítida.
# ------------------------------------------------------------------

def suavizar(t):
    """Curva 'smoothstep': transición suave entre 0 y 1."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def envolvente(progreso, zona_transicion=0.16):
    """
    Devuelve un valor 0→1→0 a lo largo del recorrido, pero con una meseta
    plana en el centro (no un simple pico), y ambos extremos desvaneciéndose
    suavemente hasta casi una línea recta. Así es como 'nace' y 'muere'
    la onda de Siri en los bordes.
    """
    subida = suavizar(progreso / zona_transicion) if progreso < zona_transicion else 1.0
    bajada = suavizar((1 - progreso) / zona_transicion) if progreso > (1 - zona_transicion) else 1.0
    return min(subida, bajada)


def curva_cerrada_suave(puntos):
    """
    Convierte una lista de puntos (un contorno cerrado) en una curva
    fluida sin cambios bruscos de dirección, usando la técnica clásica
    de Catmull-Rom convertida a Bezier cúbica. Cada punto pasa a formar
    parte de una curva continua, en vez de segmentos rectos entre puntos.
    """
    ruta = QPainterPath()
    n = len(puntos)
    ruta.moveTo(*puntos[0])
    for i in range(n):
        p0 = puntos[(i - 1) % n]
        p1 = puntos[i % n]
        p2 = puntos[(i + 1) % n]
        p3 = puntos[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        ruta.cubicTo(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
    ruta.closeSubpath()
    return ruta


def envolvente_dinamica(progreso, zona_tensa=0.03, zona_transicion=0.10):
    """
    A diferencia de 'envolvente' (que sube y baja como una campana), esta
    devuelve 0 en un pequeño tramo pegado a cada borde (la parte "tensa",
    sin oscilación) y 1 en el resto del recorrido, con una transición
    suave entre ambas zonas.
    """
    distancia_al_borde = min(progreso, 1 - progreso)
    if distancia_al_borde < zona_tensa:
        return 0.0
    if distancia_al_borde < zona_transicion:
        t = (distancia_al_borde - zona_tensa) / (zona_transicion - zona_tensa)
        return suavizar(t)
    return 1.0


def pulso_cardiaco(fase):
    """
    Envolvente de 0 a 1, ahora en forma de "respiración": una única onda
    continua y redondeada (coseno elevado a una potencia), sin picos duros
    ni caídas bruscas. Se cambió el diseño anterior (dos golpes tipo
    "lub-dub") por esto porque, aunque más fiel a un latido real, generaba
    una sensación de parpadeo -molesta si alguien tiene la app de fondo
    mientras trabaja o estudia. 'fase' recorre 0→1 y se repite en bucle.
    """
    base = (math.cos(fase * 2 * math.pi) + 1) / 2  # 0..1, sube y baja sin saltos
    return base ** 1.6  # matiza un poco más el tramo bajo sin volverlo brusco


def difuminar_pixmap(pixmap, radio):
    """
    Aplica un desenfoque gaussiano real a un QPixmap y devuelve el resultado.
    QGraphicsBlurEffect solo funciona sobre widgets/items gráficos, así que
    montamos una escena de un solo elemento como "truco" para desenfocar
    una imagen suelta.
    """
    if radio <= 0:
        return pixmap

    escena = QGraphicsScene()
    item = QGraphicsPixmapItem(pixmap)
    efecto = QGraphicsBlurEffect()
    efecto.setBlurRadius(radio)
    efecto.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(efecto)
    escena.addItem(item)

    resultado = QPixmap(pixmap.size())
    resultado.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(resultado)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    escena.render(pintor, QRectF(resultado.rect()), QRectF(pixmap.rect()))
    pintor.end()
    return resultado


# El trazado de la nota a tamaño original, ya rasterizado. Se calcula una
# sola vez y se reutiliza: el rasterizado recorre 1024x1024 píxeles uno a
# uno desde Python (más de un millón de iteraciones), así que repetirlo
# cada vez que la nota cambia de tamaño -al entrar y salir del modo
# compacto- congelaría la ventana durante casi un segundo.
_ruta_nota_cacheada = None


def _ruta_nota_original():
    """Rasteriza el SVG de la nota una única vez y devuelve su trazado
    en las coordenadas originales, sin escalar ni centrar."""
    global _ruta_nota_cacheada
    if _ruta_nota_cacheada is not None:
        return _ruta_nota_cacheada

    ruta_svg = ruta_recurso("Nota-musica.svg")
    renderer = QSvgRenderer(ruta_svg)
    if not renderer.isValid():
        _ruta_nota_cacheada = QPainterPath()
        return _ruta_nota_cacheada

    lado = 1024
    imagen = QImage(lado, lado, QImage.Format.Format_ARGB32_Premultiplied)
    imagen.fill(Qt.GlobalColor.transparent)
    painter = QPainter(imagen)
    renderer.render(painter, QRectF(0, 0, lado, lado))
    painter.end()

    # QRegion considera visibles los píxeles a cero de un QBitmap.
    mascara = QImage(lado, lado, QImage.Format.Format_Mono)
    mascara.fill(0)
    for y in range(lado):
        for x in range(lado):
            if imagen.pixelColor(x, y).alpha() == 0:
                mascara.setPixel(x, y, 1)

    ruta = QPainterPath()
    ruta.addRegion(QRegion(QBitmap.fromImage(mascara)))
    _ruta_nota_cacheada = ruta
    return _ruta_nota_cacheada


def construir_nota_musical(ancho, alto):
    """
    Devuelve el trazado de la nota escalado y centrado para un widget de
    'ancho' x 'alto'. La forma procede directamente del trazado vectorial
    del SVG, no de coordenadas dibujadas a mano.
    """
    ruta = _ruta_nota_original()
    if ruta.isEmpty():
        return QPainterPath()

    caja = ruta.boundingRect()
    tamano_objetivo = min(ancho, alto) * 0.62
    escala = tamano_objetivo / max(caja.width(), caja.height())
    desplazamiento_x = -ancho * 0.025

    transformacion_final = QTransform()
    transformacion_final.translate(ancho / 2 + desplazamiento_x, alto / 2)
    transformacion_final.scale(escala, escala)
    transformacion_final.translate(-caja.center().x(), -caja.center().y())

    return transformacion_final.map(ruta)


class OndaAnimada(QWidget):
    """
    Recrea el "orbe" de Siri: varios lóbulos de color translúcidos que
    giran despacio alrededor del centro, deformándose sin parar. No son
    círculos: cada lóbulo es un contorno cerrado cuyo radio varía con el
    ángulo, así que se ven como pétalos/cuchillas alargadas que se
    solapan. Al pintarse en mezcla aditiva, la zona donde se cruzan todos
    (el centro) satura hacia el blanco -que es exactamente el brillo
    característico del orbe de Siri- mientras que los bordes conservan su
    color puro. Un violeta profundo llena la silueta por debajo para que
    nunca se vea el fondo negro entre los lóbulos.
    """

    # Color de relleno de la silueta por debajo de todo. Violeta muy
    # oscuro, del mismo tono que el fondo de la referencia de Siri.
    COLOR_BASE = QColor(26, 14, 54, 255)

    # Cada lóbulo gira a su propio ritmo (unos en sentido contrario) y se
    # deforma con dos armónicos distintos, de modo que nunca se repite la
    # misma composición exacta y el conjunto parece fluido y orgánico.
    #  - "radio_relativo": tamaño base respecto al lado menor del widget.
    #  - "velocidad_giro": vueltas por segundo. Negativo = sentido contrario.
    #  - "deformacion": (amplitud del armónico 2, amplitud del armónico 3).
    #    El armónico 2 alarga el lóbulo (le da forma de pétalo); el 3 lo
    #    vuelve asimétrico para que no parezca una elipse perfecta.
    #  - "orbita": cuánto se desplaza su centro respecto al centro real,
    #    y "velocidad_orbita" a qué ritmo da esa vuelta. Esto es lo que
    #    hace que los cruces entre lóbulos vayan cambiando de sitio.
    LOBULOS = [
        {  # magenta: el lóbulo dominante, arriba a la izquierda en reposo
            "color": QColor(255, 70, 170),
            "radio_relativo": 0.30,
            "velocidad_giro": 0.035,
            "deformacion": (0.30, 0.10),
            "fase": 0.0,
            "orbita": 0.055,
            "velocidad_orbita": 0.028,
        },
        {  # cian: cruza al magenta en sentido contrario
            "color": QColor(60, 215, 255),
            "radio_relativo": 0.29,
            "velocidad_giro": -0.045,
            "deformacion": (0.34, 0.08),
            "fase": 2.1,
            "orbita": 0.060,
            "velocidad_orbita": -0.034,
        },
        {  # verde azulado: aparece por abajo, aporta el matiz frío
            "color": QColor(70, 255, 190),
            "radio_relativo": 0.26,
            "velocidad_giro": 0.052,
            "deformacion": (0.38, 0.12),
            "fase": 4.0,
            "orbita": 0.050,
            "velocidad_orbita": 0.041,
        },
        {  # violeta: el fondo cálido que envuelve al resto
            "color": QColor(150, 100, 255),
            "radio_relativo": 0.32,
            "velocidad_giro": -0.026,
            "deformacion": (0.22, 0.09),
            "fase": 5.4,
            "orbita": 0.045,
            "velocidad_orbita": -0.022,
        },
        {  # núcleo casi blanco: pequeño y centrado, es el que "enciende"
            # el punto de luz del medio cuando los demás pasan por encima
            "color": QColor(225, 240, 255),
            "radio_relativo": 0.14,
            "velocidad_giro": 0.070,
            "deformacion": (0.42, 0.15),
            "fase": 1.2,
            "orbita": 0.022,
            "velocidad_orbita": 0.055,
        },
    ]

    # Respiración global muy lenta: todo el orbe crece y encoge a la vez,
    # apenas un 8%, para que no se quede "congelado" visualmente.
    FRECUENCIA_RESPIRACION = 0.09  # ciclos por segundo (~11 s por ciclo)

    # Cuántos puntos se usan para trazar el contorno de cada lóbulo. 48 es
    # de sobra para que la curva salga suave a este tamaño.
    PUNTOS_CONTORNO = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.tiempo = 0.0
        self.activa = False
        self.mascara = None

        self.nivel = 0.55
        self.objetivo_nivel = 0.55

        # Las formas del último fotograma, para que el brillo ambiental de
        # alrededor de la nota (_GlowNota) pueda repintar exactamente los
        # mismos lóbulos en vez de manchas planas de color.
        self.formas_actuales = []

        self.temporizador = QTimer(self)
        self.temporizador.timeout.connect(self.avanzar_animacion)
        self.temporizador.start(16)  # ~60 fotogramas por segundo

    def establecer_mascara(self, ruta):
        """Si se le da una QPainterPath, la onda solo se dibuja dentro de esa forma."""
        self.mascara = ruta
        self.update()

    def avanzar_animacion(self):
        self.tiempo += 0.016  # el movimiento nunca se detiene, haya o no música

        if random.random() < 0.02:
            self.objetivo_nivel = random.uniform(0.8, 1.0) if self.activa else random.uniform(0.45, 0.6)
        self.nivel += (self.objetivo_nivel - self.nivel) * 0.06  # suavizado, sin saltos

        self.update()

    def establecer_activa(self, activa: bool):
        self.activa = activa

    def construir_lobulo(self, config, ancho, alto):
        """
        Traza el contorno cerrado de un lóbulo. En vez de un círculo de
        radio constante, el radio depende del ángulo: se le suman dos
        ondas (armónicos 2 y 3) cuya fase avanza con el tiempo, lo que
        deforma la silueta continuamente. Todo el lóbulo gira además
        sobre el centro y su centro orbita ligeramente, de modo que los
        cruces entre lóbulos van cambiando de sitio.
        """
        lado_menor = min(ancho, alto)
        respiracion = 1 + 0.08 * math.sin(
            self.tiempo * self.FRECUENCIA_RESPIRACION * 2 * math.pi + config["fase"]
        )
        # Con música sonando el orbe se abre un poco más que en reposo.
        radio_base = lado_menor * config["radio_relativo"] * respiracion * (0.90 + 0.14 * self.nivel)

        giro = self.tiempo * config["velocidad_giro"] * 2 * math.pi + config["fase"]
        amplitud_2, amplitud_3 = config["deformacion"]

        angulo_orbita = self.tiempo * config["velocidad_orbita"] * 2 * math.pi + config["fase"]
        radio_orbita = lado_menor * config["orbita"]
        centro_x = ancho / 2 + math.cos(angulo_orbita) * radio_orbita
        centro_y = alto / 2 + math.sin(angulo_orbita) * radio_orbita

        puntos = []
        for i in range(self.PUNTOS_CONTORNO):
            angulo = 2 * math.pi * i / self.PUNTOS_CONTORNO
            radio = radio_base * (
                1
                + amplitud_2 * math.sin(2 * angulo + giro)
                + amplitud_3 * math.sin(3 * angulo - giro * 1.7 + config["fase"])
            )
            puntos.append((
                centro_x + math.cos(angulo) * radio,
                centro_y + math.sin(angulo) * radio,
            ))

        return curva_cerrada_suave(puntos)

    def calcular_formas(self, ancho, alto):
        """Devuelve la lista de (contorno, color ya con su transparencia)
        de este fotograma. La usan tanto el propio widget como el brillo
        ambiental de alrededor, para que ambos muestren lo mismo."""
        formas = []
        for config in self.LOBULOS:
            ruta = self.construir_lobulo(config, ancho, alto)
            color = QColor(config["color"])
            # Translúcidos a propósito: en mezcla aditiva, es el solape de
            # varios lóbulos -no un color opaco- lo que produce el blanco
            # del centro. Suben algo de opacidad cuando hay música.
            color.setAlpha(int(255 * (0.34 + 0.16 * self.nivel)))
            formas.append((ruta, color))
        return formas

    def paintEvent(self, event):
        ancho, alto = self.width(), self.height()
        if ancho <= 0 or alto <= 0:
            return

        self.formas_actuales = self.calcular_formas(ancho, alto)

        # 1) Pintamos los lóbulos en un lienzo aparte, todavía sin recortar
        #    a la forma de la nota, para poder desenfocarlos juntos y que
        #    se fundan de verdad unos con otros.
        lienzo = QPixmap(ancho, alto)
        lienzo.fill(Qt.GlobalColor.transparent)
        pintor_lienzo = QPainter(lienzo)
        pintor_lienzo.setRenderHint(QPainter.RenderHint.Antialiasing)
        pintor_lienzo.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        for ruta, color in self.formas_actuales:
            pintor_lienzo.fillPath(ruta, color)

        pintor_lienzo.end()

        # 2) Desenfoque real (gaussiano) de todo el lienzo: así el degradado
        #    entre colores queda suave en vez de tener bordes duros.
        #    Menor que antes porque ahora la gracia está en distinguir la
        #    silueta de cada lóbulo, no en fundirlo todo en una mancha.
        radio_blur = max(4.0, alto * 0.055)
        lienzo_difuminado = difuminar_pixmap(lienzo, radio_blur)

        # 3) Recortamos a la silueta de la nota musical y pintamos encima.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.mascara is not None:
            painter.setClipPath(self.mascara)
            # Base continua: la silueta de la nota nunca deja ver el fondo negro.
            painter.fillPath(self.mascara, self.COLOR_BASE)

        painter.drawPixmap(0, 0, lienzo_difuminado)


# ------------------------------------------------------------------
# Botón "semáforo": como el círculo rojo de cerrar de macOS.
# En reposo es un círculo apagado sin icono; al pasar el ratón por
# encima se ilumina de color y aparece el icono.
# ------------------------------------------------------------------

class BotonSemaforo(QPushButton):
    # Gris apagado de macOS cuando la ventana pierde el foco. Es un tono
    # neutro que se distingue del fondo de la tarjeta sin llamar la
    # atención: la ventana de delante es la que debe destacar.
    COLOR_INACTIVO = QColor(86, 86, 90)

    def __init__(self, color_hover, tamano=20, parent=None):
        super().__init__(parent)
        self.color_hover = color_hover
        self.tamano = tamano
        self._hover = False
        self.setFixedSize(tamano, tamano)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0px; }")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        ancho = self.width()
        alto = self.height()

        # Con la ventana en segundo plano el botón se apaga a gris, como
        # en cualquier ventana de macOS. Al pasar el ratón por encima
        # recupera el color aunque la ventana siga inactiva: también así
        # se comporta el semáforo del sistema.
        if self._hover or self.window().isActiveWindow():
            color_circulo = self.color_hover
        else:
            color_circulo = self.COLOR_INACTIVO

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color_circulo)
        painter.drawEllipse(QRectF(0, 0, ancho, alto))

        if self._hover:
            pluma = QPen(QColor(120, 15, 10, 220), max(1.4, min(ancho, alto) * 0.09))
            pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pluma)
            mx = ancho * 0.30
            my = alto * 0.30
            painter.drawLine(QPointF(mx, my), QPointF(ancho - mx, alto - my))
            painter.drawLine(QPointF(ancho - mx, my), QPointF(mx, alto - my))


# ------------------------------------------------------------------
# Barra de título al estilo Windows: solo la "×" arriba a la derecha,
# pegada al borde de la tarjeta como en cualquier ventana de Windows 11.
# Ni franja de fondo ni nombre de la app: se probaron y rompían la
# limpieza de la tarjeta; la cruz flota directamente sobre ella. En
# macOS no existe: allí la tarjeta lleva el semáforo (BotonSemaforo) y
# nada más. Sustituye a la fila superior de macOS, que solo servía para
# alojar el semáforo.
# ------------------------------------------------------------------

ALTO_BARRA_TITULO_WINDOWS = 28
# Hueco entre la barra de título y el panel de la nota. Es más justo que
# el margen superior de macOS porque la propia barra ya hace de aire.
SEPARACION_BARRA_TITULO_WINDOWS = 4


class BotonCerrarWindows(QPushButton):
    """La "×" de la barra de título. En reposo es solo la cruz; al pasar
    el ratón se enciende el fondo rojo de Windows, recortado por la
    esquina redondeada de la tarjeta igual que hace Windows 11 con sus
    ventanas de esquinas curvas."""

    # Rojo de Windows 11 (#E81123) y su versión apagada al mantener pulsado.
    COLOR_HOVER = QColor(232, 17, 35)
    COLOR_PULSADO = QColor(196, 43, 28)

    def __init__(self, ancho, alto, radio_esquina, parent=None):
        super().__init__(parent)
        self.radio_esquina = radio_esquina
        self._hover = False
        self.setFixedSize(ancho, alto)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0px; }")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _forma_fondo(self):
        """Rectángulo cuya esquina superior derecha sigue la curva de la
        tarjeta, un píxel por dentro para no pisar el borde."""
        ancho, alto = self.width(), self.height()
        # El borde de la tarjeta mide 1px, así que por dentro de él el
        # radio efectivo es uno menos.
        radio = self.radio_esquina - 1
        ruta = QPainterPath()
        ruta.moveTo(0, 1)
        ruta.lineTo(ancho - 1 - radio, 1)
        ruta.arcTo(QRectF(ancho - 1 - 2 * radio, 1, 2 * radio, 2 * radio), 90, -90)
        ruta.lineTo(ancho - 1, alto)
        ruta.lineTo(0, alto)
        ruta.closeSubpath()
        return ruta

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ancho, alto = self.width(), self.height()

        if self.isDown() or self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.COLOR_PULSADO if self.isDown() else self.COLOR_HOVER)
            painter.drawPath(self._forma_fondo())

        # Con la ventana en segundo plano la cruz se atenúa, como hace
        # Windows con los botones de una ventana inactiva.
        if self._hover or self.window().isActiveWindow():
            color_cruz = QColor(255, 255, 255, 235)
        else:
            color_cruz = QColor(255, 255, 255, 110)

        pluma = QPen(color_cruz, 1.1)
        pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pluma)
        # Cruz de 8 px, un poco menor que el glifo de Windows (10 px):
        # la tarjeta es pequeña y a tamaño real dominaba la esquina. Va
        # algo a la izquierda del centro porque la esquina redondeada se
        # come la parte derecha del botón.
        lado = 4
        cx, cy = ancho / 2 - 3, alto / 2
        painter.drawLine(QPointF(cx - lado, cy - lado), QPointF(cx + lado, cy + lado))
        painter.drawLine(QPointF(cx + lado, cy - lado), QPointF(cx - lado, cy + lado))


class BarraTituloWindows(QWidget):
    ANCHO_BOTON_CERRAR = 44

    def __init__(self, ancho, radio_esquina, parent=None):
        super().__init__(parent)
        self.setFixedSize(ancho, ALTO_BARRA_TITULO_WINDOWS)

        self.boton_cerrar = BotonCerrarWindows(
            self.ANCHO_BOTON_CERRAR, ALTO_BARRA_TITULO_WINDOWS, radio_esquina, parent=self
        )
        self.boton_cerrar.move(ancho - self.ANCHO_BOTON_CERRAR, 0)

        # No pinta nada ni captura el ratón: los clics y arrastres siguen
        # hasta la ventana, que es quien mueve la tarjeta. Así la franja
        # se puede arrastrar igual que la barra de cualquier ventana de
        # Windows, aunque no se vea.


# ------------------------------------------------------------------
# Botones de transporte (anterior / play-pausa / siguiente).
#
# En macOS son los caracteres "⏮ ▶ ⏸ ⏭" de toda la vida: salen de la
# fuente del sistema y se ven perfectamente. En Windows esos mismos
# caracteres se convierten en emojis de color (cuadrados azules) que
# rompen la interfaz, así que allí los símbolos se dibujan a mano con
# QPainter. Las dos clases comparten la misma interfaz
# ('establecer_simbolo') y el resto del reproductor no distingue cuál
# tiene delante: 'crear_boton_control' elige según la plataforma.
# ------------------------------------------------------------------

SIMBOLOS_CONTROL = ("play", "pausa", "anterior", "siguiente")

# Caracteres de cada símbolo, para la versión de texto (macOS).
CARACTERES_CONTROL = {
    "play": "▶",
    "pausa": "⏸",
    "anterior": "⏮",
    "siguiente": "⏭",
}


def crear_boton_control(simbolo):
    if sys.platform == "win32":
        return BotonControlDibujado(simbolo)
    return BotonControlTexto(simbolo)


class BotonControlTexto(QPushButton):
    """Versión de macOS: el símbolo es un carácter de texto."""

    def __init__(self, simbolo, parent=None):
        super().__init__(CARACTERES_CONTROL[simbolo], parent)

    def establecer_simbolo(self, simbolo):
        self.setText(CARACTERES_CONTROL[simbolo])


class BotonControlDibujado(QPushButton):
    """Versión de Windows: el símbolo se pinta con QPainter."""

    def __init__(self, simbolo, parent=None):
        super().__init__(parent)
        self.simbolo = simbolo
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def establecer_simbolo(self, simbolo):
        if simbolo not in SIMBOLOS_CONTROL:
            raise ValueError(f"Símbolo desconocido: {simbolo}")
        self.simbolo = simbolo
        self.update()

    def paintEvent(self, event):
        # Primero el fondo redondo de la hoja de estilos, encima el símbolo.
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 240))

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        # Tamaño del símbolo relativo al botón, para que escale entre el
        # modo normal y el compacto sin tocar nada más.
        lado = min(w, h) * 0.36

        if self.simbolo == "play":
            # El triángulo se desplaza un poco a la derecha: su centro
            # geométrico no coincide con el óptico, y centrado a secas
            # parece caído hacia la izquierda.
            self._triangulo(painter, cx + lado * 0.1, cy, lado, hacia_derecha=True)
        elif self.simbolo == "pausa":
            grosor = lado * 0.32
            hueco = lado * 0.22
            for x in (cx - hueco - grosor, cx + hueco):
                painter.drawRoundedRect(
                    QRectF(x, cy - lado / 2, grosor, lado), grosor * 0.35, grosor * 0.35
                )
        else:
            # "anterior" = barra + triángulo hacia la izquierda;
            # "siguiente" = triángulo hacia la derecha + barra.
            hacia_derecha = self.simbolo == "siguiente"
            lado_tri = lado * 0.9
            grosor_barra = lado * 0.22
            # Desplazamiento del conjunto para que barra+triángulo queden
            # centrados como un solo bloque.
            ancho_total = lado_tri * 0.9 + grosor_barra + lado * 0.1
            inicio = cx - ancho_total / 2
            if hacia_derecha:
                self._triangulo(
                    painter, inicio + lado_tri * 0.45, cy, lado_tri, hacia_derecha=True
                )
                barra_x = inicio + ancho_total - grosor_barra
            else:
                barra_x = inicio
                self._triangulo(
                    painter,
                    inicio + ancho_total - lado_tri * 0.45,
                    cy,
                    lado_tri,
                    hacia_derecha=False,
                )
            painter.drawRoundedRect(
                QRectF(barra_x, cy - lado_tri / 2, grosor_barra, lado_tri),
                grosor_barra * 0.4,
                grosor_barra * 0.4,
            )

    @staticmethod
    def _triangulo(painter, cx, cy, lado, hacia_derecha):
        """Triángulo equilátero-ish de altura 'lado' centrado en (cx, cy),
        con la punta hacia la derecha o hacia la izquierda."""
        media_altura = lado / 2
        media_base = lado * 0.45
        if hacia_derecha:
            puntos = [
                QPointF(cx - media_base, cy - media_altura),
                QPointF(cx + media_base, cy),
                QPointF(cx - media_base, cy + media_altura),
            ]
        else:
            puntos = [
                QPointF(cx + media_base, cy - media_altura),
                QPointF(cx - media_base, cy),
                QPointF(cx + media_base, cy + media_altura),
            ]
        ruta = QPainterPath()
        ruta.moveTo(puntos[0])
        ruta.lineTo(puntos[1])
        ruta.lineTo(puntos[2])
        ruta.closeSubpath()
        painter.drawPath(ruta)


# ------------------------------------------------------------------
# Botón de lista: tres líneas horizontales dibujadas a mano y centradas
# (el carácter "☰" de texto no queda bien centrado según la fuente).
# ------------------------------------------------------------------

class BotonLista(QPushButton):
    def __init__(self, tamano=30, parent=None):
        super().__init__(parent)
        self.tamano = tamano
        self.setFixedSize(tamano, tamano)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255,255,255,22);
                border-radius: {tamano // 2}px;
                border: none;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,45); }}
            QPushButton:pressed {{ background-color: {COLOR_ACENTO_CSS}; }}
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pluma = QPen(QColor(255, 255, 255, 235), max(1.3, self.tamano * 0.06))
        pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pluma)

        w, h = self.width(), self.height()
        margen_x = w * 0.28
        centro_y = h / 2
        separacion = h * 0.15
        for y in (centro_y - separacion, centro_y, centro_y + separacion):
            painter.drawLine(QPointF(margen_x, y), QPointF(w - margen_x, y))


# ------------------------------------------------------------------
# Botón de reproducción aleatoria: dos cintas curvas que se cruzan,
# cada una terminando en una punta de flecha (estilo Spotify/iOS).
# ------------------------------------------------------------------

class BotonAleatorio(QPushButton):
    def __init__(self, tamano=30, parent=None):
        super().__init__(parent)
        self.tamano = tamano
        self.activo = False
        self.setFixedSize(tamano, tamano)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._actualizar_estilo()

    def establecer_activo(self, activo: bool):
        self.activo = activo
        self._actualizar_estilo()

    def _actualizar_estilo(self):
        r, g, b = COLOR_ACENTO.red(), COLOR_ACENTO.green(), COLOR_ACENTO.blue()
        if self.activo:
            fondo = f"rgba({r},{g},{b},210)"
            hover = f"rgba({r},{g},{b},240)"
        else:
            fondo = "rgba(255,255,255,20)"
            hover = f"rgba({r},{g},{b},90)"
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {fondo};
                border-radius: {self.tamano // 2}px;
                border: none;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor(255, 255, 255, 255) if self.activo else QColor(255, 255, 255, 205)
        escala = self.devicePixelRatioF()
        icono = icono_tenido(
            ARCHIVO_ICONO_ALEATORIO, round(self.tamano * 0.62), color, escala
        )
        if icono.isNull():
            return

        # El pixmap tiene más píxeles que su tamaño en pantalla (Retina),
        # así que hay que centrarlo por su medida lógica, no por la real.
        ancho = icono.width() / escala
        alto = icono.height() / escala
        painter.drawPixmap(
            QPointF((self.width() - ancho) / 2, (self.height() - alto) / 2), icono
        )


# ------------------------------------------------------------------
# Indicador tipo ecualizador: tres barritas blancas que suben y bajan
# junto al título de la canción que está sonando ahora mismo. En reposo
# no dibuja nada (deja el hueco vacío) para no ensuciar el resto de filas.
# ------------------------------------------------------------------

class IndicadorEcualizador(QWidget):
    def __init__(self, tamano=14, parent=None):
        super().__init__(parent)
        self.setFixedSize(tamano, tamano)
        self._es_actual = False
        self._reproduciendo = False
        self._fase = 0.0
        self._temporizador = QTimer(self)
        self._temporizador.setInterval(70)
        self._temporizador.timeout.connect(self._avanzar)
        # Alturas fijas para el estado "en pausa": la canción cargada
        # sigue marcada, pero quieta, sin animar.
        self._alturas_pausa = (6.0, 10.0, 7.5)

    def establecer_estado(self, es_actual: bool, reproduciendo: bool):
        """es_actual: esta es la fila de la canción cargada ahora mismo.
        reproduciendo: y además está sonando de verdad (no en pausa)."""
        if es_actual == self._es_actual and reproduciendo == self._reproduciendo:
            return
        self._es_actual = es_actual
        self._reproduciendo = es_actual and reproduciendo
        if self._reproduciendo:
            self._temporizador.start()
        else:
            self._temporizador.stop()
        self.update()

    def _avanzar(self):
        self._fase += 0.35
        self.update()

    def paintEvent(self, event):
        if not self._es_actual:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 225 if self._reproduciendo else 130))

        w, h = self.width(), self.height()
        ancho_barra = 2.4
        espacio = 2.0
        num_barras = 3
        ancho_total = num_barras * ancho_barra + (num_barras - 1) * espacio
        x0 = (w - ancho_total) / 2
        velocidades = (1.0, 0.75, 1.25)
        desfases = (0.0, 1.4, 2.7)

        for i in range(num_barras):
            if self._reproduciendo:
                t = (math.sin(self._fase * velocidades[i] + desfases[i]) + 1) / 2
                altura = 3 + t * (h - 3)
            else:
                altura = self._alturas_pausa[i]
            x = x0 + i * (ancho_barra + espacio)
            y = (h - altura) / 2
            painter.drawRoundedRect(QRectF(x, y, ancho_barra, altura), 1.2, 1.2)


# ------------------------------------------------------------------
# Lupa de la barra de búsqueda: no es un botón, solo señala que ese campo
# sirve para buscar. Si el módulo opcional de descargas está instalado,
# en su lugar se coloca el interruptor de modo que trae ese módulo.
# ------------------------------------------------------------------

class IconoLupa(QWidget):
    def __init__(self, lado=24, parent=None):
        super().__init__(parent)
        self.setFixedSize(lado, lado)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Mismo fondo y misma intensidad que el interruptor de modo cuando
        # está apagado: así la barra se ve exactamente igual en las dos
        # versiones, y las capturas de la web sirven para ambas.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 20))
        painter.drawEllipse(QRectF(0, 0, self.width(), self.height()))

        pluma = QPen(QColor(255, 255, 255, 235), 1.5)
        pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pluma)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        w, h = self.width(), self.height()
        radio = min(w, h) * 0.22
        centro = QPointF(w * 0.45, h * 0.44)
        painter.drawEllipse(centro, radio, radio)
        desplazamiento = radio * 0.72
        painter.drawLine(
            QPointF(centro.x() + desplazamiento, centro.y() + desplazamiento),
            QPointF(w * 0.76, h * 0.76),
        )


# ------------------------------------------------------------------
# Franja de degradado que difumina el principio/final de la lista de
# canciones al hacer scroll, en vez de cortar las filas en seco.
# ------------------------------------------------------------------

class DesvanecidoBorde(QWidget):
    def __init__(self, arriba: bool, color_fondo: QColor, parent=None):
        super().__init__(parent)
        self.arriba = arriba
        self.color_fondo = QColor(color_fondo)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        gradiente = QLinearGradient(0, 0, 0, self.height())
        transparente = QColor(self.color_fondo)
        transparente.setAlpha(0)
        if self.arriba:
            gradiente.setColorAt(0.0, self.color_fondo)
            gradiente.setColorAt(1.0, transparente)
        else:
            gradiente.setColorAt(0.0, transparente)
            gradiente.setColorAt(1.0, self.color_fondo)
        painter.fillRect(self.rect(), gradiente)


# ------------------------------------------------------------------
# Fila de canción compacta: hueco fijo a la izquierda para el
# indicador de ecualizador (vacío si no es la canción activa) + título.
# ------------------------------------------------------------------

class FilaCancion(QWidget):
    def __init__(self, titulo, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 9, 0)
        layout.setSpacing(6)

        self.indicador = IndicadorEcualizador(tamano=11)
        layout.addWidget(self.indicador)

        self.label = QLabel(titulo)
        self.label.setStyleSheet("""
            font-size: 12px;
            font-weight: 500;
            color: rgba(255, 255, 255, 220);
            background: transparent;
        """)
        layout.addWidget(self.label, 1)


# ------------------------------------------------------------------
# Punto indicador (como el de Spotify bajo el botón de aleatorio).
# Siempre ocupa el mismo espacio; solo cambia de opacidad para no
# desplazar el resto de los controles al activarse/desactivarse.
# ------------------------------------------------------------------

class PuntoIndicador(QWidget):
    def __init__(self, diametro=5, parent=None):
        super().__init__(parent)
        self.diametro = diametro
        self.activo = False
        self.setFixedSize(diametro, diametro)

    def establecer_activo(self, activo: bool):
        self.activo = activo
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = QColor(255, 255, 255, 235) if self.activo else QColor(255, 255, 255, 0)
        painter.setBrush(color)
        painter.drawEllipse(0, 0, self.diametro, self.diametro)


# ------------------------------------------------------------------
# Etiqueta clicable: se comporta como un botón (dispara 'clicked'),
# pero conserva el ajuste de línea de QLabel para títulos largos.
# Al pasar el ratón por encima, se atenúa suavemente para dar a
# entender que es interactiva.
# ------------------------------------------------------------------

class TituloClicable(QLabel):
    clicked = pyqtSignal()

    def __init__(self, texto="", parent=None):
        super().__init__(texto, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._efecto = QGraphicsOpacityEffect(self)
        self._efecto.setOpacity(1.0)
        self.setGraphicsEffect(self._efecto)

        self._animacion = QPropertyAnimation(self._efecto, b"opacity")
        self._animacion.setDuration(160)
        self._animacion.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def _animar_hacia(self, valor_final):
        self._animacion.stop()
        self._animacion.setStartValue(self._efecto.opacity())
        self._animacion.setEndValue(valor_final)
        self._animacion.start()

    def enterEvent(self, event):
        self._animar_hacia(0.55)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animar_hacia(1.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ------------------------------------------------------------------
# Etiqueta clicable: el propio texto del título actúa como botón para
# elegir carpeta. Se atenúa un poco en reposo y recupera su brillo
# completo (con una transición suave) al pasar el ratón por encima,
# para dar a entender que se puede pulsar.
# ------------------------------------------------------------------

class TituloAnimado(QWidget):
    """
    Muestra el título (también hace de botón para elegir carpeta).
    - En pausa: texto estático, centrado, completo.
    - Reproduciendo: si el texto no cabe, se desplaza en bucle sin parar,
      como una marquesina clásica.
    """
    clicked = pyqtSignal()

    def __init__(self, texto_inicial, parent=None):
        super().__init__(parent)
        self.texto = texto_inicial
        self.reproduciendo = False
        self.offset = 0.0
        # En modo normal el título va centrado bajo la portada; en modo
        # compacto se alinea a la izquierda, junto a la miniatura.
        self.centrado = True

        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.fuente = QFont()
        self.fuente.setPointSize(13)
        self.fuente.setBold(True)

        self.efecto_opacidad = QGraphicsOpacityEffect(self)
        self.efecto_opacidad.setOpacity(0.78)
        self.setGraphicsEffect(self.efecto_opacidad)
        self.animacion_hover = QPropertyAnimation(self.efecto_opacidad, b"opacity")
        self.animacion_hover.setDuration(160)
        self.animacion_hover.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.temporizador = QTimer(self)
        self.temporizador.timeout.connect(self._avanzar_scroll)
        self.temporizador.start(30)

    def establecer_texto(self, texto):
        self.texto = texto
        self.offset = 0.0
        self.update()

    def establecer_reproduciendo(self, reproduciendo: bool):
        self.reproduciendo = reproduciendo
        if not reproduciendo:
            self.offset = 0.0
        self.update()

    def _avanzar_scroll(self):
        if not self.reproduciendo:
            return
        metrica = QFontMetrics(self.fuente)
        ancho_texto = metrica.horizontalAdvance(self.texto)
        if ancho_texto <= self.width():
            return  # cabe entero, no hace falta desplazar

        espacio_entre_copias = 46
        self.offset += 0.7
        if self.offset > ancho_texto + espacio_entre_copias:
            self.offset = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.fuente)
        painter.setPen(QColor(255, 255, 255, 240))

        metrica = QFontMetrics(self.fuente)
        ancho_texto = metrica.horizontalAdvance(self.texto)
        y_base = int((self.height() + metrica.ascent() - metrica.descent()) / 2)
        texto_no_cabe = ancho_texto > self.width()

        if not self.reproduciendo or not texto_no_cabe:
            alineacion = (
                Qt.AlignmentFlag.AlignCenter if self.centrado else Qt.AlignmentFlag.AlignLeft
            )
            painter.drawText(self.rect(), alineacion | Qt.AlignmentFlag.AlignVCenter, self.texto)
        else:
            painter.setClipRect(self.rect())
            espacio_entre_copias = 46
            x1 = -self.offset
            painter.drawText(int(x1), y_base, self.texto)
            painter.drawText(int(x1 + ancho_texto + espacio_entre_copias), y_base, self.texto)

        # Si el texto no cabe (esté o no en marcha la marquesina), en vez de
        # cortarlo en seco difuminamos los bordes hacia el color de fondo.
        # Así el corte se ve intencional -tipo Apple Music/Spotify- en vez
        # de un recorte brusco, tanto en movimiento como en una captura fija.
        if texto_no_cabe:
            self._dibujar_difuminado_bordes(painter)

    def _dibujar_difuminado_bordes(self, painter):
        ancho_difuminado = min(24, self.width() / 3)
        color_fondo = QColor(28, 28, 32)  # el mismo tono que el fondo de la tarjeta

        color_opaco = QColor(color_fondo)
        color_opaco.setAlpha(235)
        color_transparente = QColor(color_fondo)
        color_transparente.setAlpha(0)

        gradiente_izq = QLinearGradient(0, 0, ancho_difuminado, 0)
        gradiente_izq.setColorAt(0.0, color_opaco)
        gradiente_izq.setColorAt(1.0, color_transparente)
        painter.fillRect(QRectF(0, 0, ancho_difuminado, self.height()), gradiente_izq)

        gradiente_der = QLinearGradient(self.width() - ancho_difuminado, 0, self.width(), 0)
        gradiente_der.setColorAt(0.0, color_transparente)
        gradiente_der.setColorAt(1.0, color_opaco)
        painter.fillRect(
            QRectF(self.width() - ancho_difuminado, 0, ancho_difuminado, self.height()), gradiente_der
        )

    def _animar_hacia(self, valor_final):
        self.animacion_hover.stop()
        self.animacion_hover.setStartValue(self.efecto_opacidad.opacity())
        self.animacion_hover.setEndValue(valor_final)
        self.animacion_hover.start()

    def enterEvent(self, event):
        self._animar_hacia(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animar_hacia(0.78)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ------------------------------------------------------------------
# Portada del álbum (o la onda, si no hay portada)
# ------------------------------------------------------------------

class _GlowNota(QWidget):
    """
    Brillo ambiental alrededor de la nota musical, con los mismos colores
    que la onda que suena dentro de ella —como la iluminación de borde de
    un televisor, que ilumina la pared con los colores de la pantalla—.
    Se pinta sin recortar a la forma de la nota, y luego se desenfoca
    mucho desde fuera para que el color se "derrame" alrededor.

    Repinta los mismos lóbulos del orbe (no manchas planas), de modo que
    el halo reproduce de verdad su composición de colores en cada momento,
    igual que el resplandor violeta/magenta que rodea al orbe de Siri.

    Importante: los lóbulos se recortan también aquí a la silueta de la
    nota. Sin ese recorte el halo sería un puñado de manchas redondas
    desbordando por los lados, y el conjunto dejaba de leerse como "la
    animación vive dentro de la nota". Recortado, lo único que sale de la
    forma es el desenfoque posterior, que es justo lo que da la sensación
    de que la propia nota ilumina lo que tiene alrededor.
    """

    def __init__(self, onda, ruta_nota, parent=None):
        super().__init__(parent)
        self.onda = onda
        self.ruta_nota = ruta_nota
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(self.ruta_nota)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        # En el primer fotograma la onda aún no ha calculado sus formas.
        for ruta, color in self.onda.formas_actuales:
            color_halo = QColor(color)
            color_halo.setAlpha(int(color.alpha() * 0.85))
            painter.fillPath(ruta, color_halo)


class Portada(QWidget):
    # Un único color de fondo, compartido con la lista de canciones, para
    # que ambas zonas (nota / lista) se vean exactamente iguales y no haya
    # que recordar cambiarlo en dos sitios a la vez.
    COLOR_FONDO_PANEL = QColor(32, 32, 37, 255)

    # Se emite al hacer clic (un clic de verdad, no al arrastrar la
    # ventana agarrándola por aquí). Es lo que alterna el modo compacto.
    clicked = pyqtSignal()

    # Cuánto se puede mover el ratón entre pulsar y soltar para que siga
    # contando como un clic y no como un arrastre de la ventana.
    TOLERANCIA_CLIC = 4

    def __init__(self, ancho=250, alto=250, radio=26, parent=None):
        super().__init__(parent)
        self.pixmap_actual = None
        self.radio = radio
        self._punto_pulsacion = None
        self._hubo_arrastre = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # --- Arrastrar una carpeta hasta aquí ---
        # Dos animaciones independientes: el resalte mientras la carpeta
        # sobrevuela la ventana, y el destello del momento de soltarla.
        # Van por separado porque la primera tiene que poder quedarse
        # quieta indefinidamente y la segunda se dispara y se va sola.
        self._resalte = 0.0
        self._destello = 0.0

        self._anim_resalte = QVariantAnimation(self)
        self._anim_resalte.setDuration(180)
        self._anim_resalte.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim_resalte.valueChanged.connect(self._al_cambiar_resalte)

        self._anim_destello = QVariantAnimation(self)
        self._anim_destello.setDuration(620)
        self._anim_destello.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim_destello.valueChanged.connect(self._al_cambiar_destello)

        self.onda = OndaAnimada(self)
        self.glow_nota = _GlowNota(self.onda, QPainterPath(), self)
        self.glow_nota.lower()

        # Brillo ambiental, detrás de la nota (con .lower()), bien desenfocado
        self.efecto_desenfoque_glow = QGraphicsBlurEffect()
        self.glow_nota.setGraphicsEffect(self.efecto_desenfoque_glow)

        # El brillo se repinta con el mismo ritmo que la propia onda
        self.onda.temporizador.timeout.connect(self.glow_nota.update)

        self.redimensionar(ancho, alto, radio)

    def redimensionar(self, ancho, alto, radio):
        """Cambia el tamaño de la portada y recalcula la silueta de la
        nota para el nuevo tamaño. Lo usa el modo compacto, donde la
        portada pasa de ocupar toda la zona superior a ser una miniatura."""
        self.radio = radio
        self.setFixedSize(ancho, alto)

        ruta_nota = construir_nota_musical(ancho, alto)
        self.onda.setGeometry(0, 0, ancho, alto)
        self.onda.establecer_mascara(ruta_nota)

        self.glow_nota.setGeometry(0, 0, ancho, alto)
        self.glow_nota.ruta_nota = ruta_nota
        self.efecto_desenfoque_glow.setBlurRadius(alto * 0.11)

        self.update()

    def mostrar_portada(self, pixmap):
        self.pixmap_actual = pixmap

    def limpiar_portada(self):
        self.pixmap_actual = None

    # --- Soltar una carpeta encima ------------------------------------

    def _al_cambiar_resalte(self, valor):
        self._resalte = float(valor)
        self.update()

    def _al_cambiar_destello(self, valor):
        self._destello = float(valor)
        self.update()

    def resaltar_arrastre(self, activo: bool):
        """Enciende o apaga el marco de acento mientras hay una carpeta
        sobrevolando la ventana."""
        destino = 1.0 if activo else 0.0
        if self._resalte == destino and self._anim_resalte.state() != QVariantAnimation.State.Running:
            return
        self._anim_resalte.stop()
        self._anim_resalte.setStartValue(self._resalte)
        self._anim_resalte.setEndValue(destino)
        self._anim_resalte.start()

    def celebrar_soltar(self):
        """El acuse de recibo al soltar: un anillo que se abre desde el
        centro y se desvanece. Dura poco a propósito; la gracia está en
        que confirme el gesto, no en que se quede a mirar."""
        self.resaltar_arrastre(False)
        self._anim_destello.stop()
        self._anim_destello.setStartValue(1.0)
        self._anim_destello.setEndValue(0.0)
        self._anim_destello.start()

    def _pintar_arrastre(self, painter, ruta_redondeada):
        """Marco y destello, ya recortados por la forma del panel."""
        if self._resalte > 0.004:
            painter.fillPath(
                ruta_redondeada,
                QColor(COLOR_ACENTO.red(), COLOR_ACENTO.green(), COLOR_ACENTO.blue(),
                       int(30 * self._resalte)),
            )
            pluma = QPen(
                QColor(COLOR_ACENTO.red(), COLOR_ACENTO.green(), COLOR_ACENTO.blue(),
                       int(235 * self._resalte)),
                2,
            )
            painter.setPen(pluma)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(1, 1, self.width() - 2, self.height() - 2),
                self.radio - 1, self.radio - 1,
            )

        if self._destello > 0.004:
            # El valor va de 1 a 0, así que el avance del anillo es su
            # complementario: empieza pequeño y opaco y acaba grande y
            # transparente.
            avance = 1.0 - self._destello
            centro = QPointF(self.width() / 2, self.height() / 2)
            radio = (max(self.width(), self.height()) * 0.75) * avance

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(
                QColor(COLOR_ACENTO.red(), COLOR_ACENTO.green(), COLOR_ACENTO.blue(),
                       int(210 * self._destello)),
                3,
            ))
            painter.drawEllipse(centro, radio, radio)

            # Un baño muy suave de acento que se va antes que el anillo,
            # para que el panel entero acuse el golpe y no solo el borde.
            painter.fillPath(
                ruta_redondeada,
                QColor(COLOR_ACENTO.red(), COLOR_ACENTO.green(), COLOR_ACENTO.blue(),
                       int(42 * self._destello ** 2)),
            )

    # --- Clic vs. arrastre -------------------------------------------
    # La portada ocupa buena parte de la ventana, así que tiene que seguir
    # sirviendo para arrastrarla por la pantalla (la ventana no tiene barra
    # de título). Por eso reenviamos el arrastre a la ventana y solo
    # emitimos 'clicked' si el ratón apenas se movió entre pulsar y soltar.

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._punto_pulsacion = event.globalPosition().toPoint()
        self._hubo_arrastre = False
        self.window().iniciar_arrastre(self._punto_pulsacion)

    def mouseMoveEvent(self, event):
        if self._punto_pulsacion is None:
            return super().mouseMoveEvent(event)
        punto = event.globalPosition().toPoint()
        if (punto - self._punto_pulsacion).manhattanLength() > self.TOLERANCIA_CLIC:
            self._hubo_arrastre = True
        self.window().continuar_arrastre(punto)

    def mouseReleaseEvent(self, event):
        if self._punto_pulsacion is None:
            return super().mouseReleaseEvent(event)
        self._punto_pulsacion = None
        self.window().terminar_arrastre()
        if not self._hubo_arrastre:
            self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        ruta_redondeada = QPainterPath()
        ruta_redondeada.addRoundedRect(
            QRectF(0, 0, self.width(), self.height()), self.radio, self.radio
        )
        painter.setClipPath(ruta_redondeada)

        if self.pixmap_actual is not None:
            self.onda.hide()
            self.glow_nota.hide()
            escalado = self.pixmap_actual.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            x = (self.width() - escalado.width()) / 2
            y = (self.height() - escalado.height()) / 2
            painter.drawPixmap(int(x), int(y), escalado)
        else:
            painter.fillPath(ruta_redondeada, self.COLOR_FONDO_PANEL)
            self.glow_nota.show()
            self.onda.show()

        # Siempre al final, para que el marco y el destello queden por
        # encima tanto de la portada como de la nota animada.
        self._pintar_arrastre(painter, ruta_redondeada)


# ------------------------------------------------------------------
# Barra de progreso sin tirador visible. Conserva el gesto de pulsar o
# arrastrar sobre cualquier punto de la línea, como Spotify o Apple Music.
# ------------------------------------------------------------------

class SliderProgreso(QSlider):
    def _valor_desde_x(self, x):
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(x), max(1, self.width())
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._valor_desde_x(event.position().x()))
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._valor_desde_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._valor_desde_x(event.position().x()))
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------

ANCHO_TARJETA = 300
# La tarjeta se ajusta a lo que ocupa su contenido: si sobra alto, Qt lo
# reparte entre las filas y aparecen franjas vacías en vez de un diseño
# compacto. Si añades o quitas filas, recalcula este número.
ALTO_TARJETA = 369
# Margen superior del layout y alto de la fila del semáforo en macOS. En
# Windows esa fila la sustituye la barra de título, que ocupa unos
# píxeles más: se suman a la tarjeta para que el resto no se apriete.
MARGEN_SUPERIOR_MAC = 10
ALTO_FILA_SEMAFORO = 14
if sys.platform == "win32":
    ALTO_TARJETA += (ALTO_BARRA_TITULO_WINDOWS + SEPARACION_BARRA_TITULO_WINDOWS) - (
        MARGEN_SUPERIOR_MAC + ALTO_FILA_SEMAFORO + 5  # 5 = spacing del layout
    )
RADIO_ESQUINAS = 26
ALTO_ZONA_SUPERIOR = 190
# Los márgenes izquierdo/derecho del layout principal son 16px cada uno
# (ver "layout.setContentsMargins" más abajo): por eso el ancho real
# disponible es ANCHO_TARJETA - 32, ni un píxel más ni menos. Usar esta
# misma constante en la nota Y en la lista es lo que garantiza que
# ocupen exactamente el mismo hueco.
ANCHO_ZONA_SUPERIOR = ANCHO_TARJETA - 32

# Barra de búsqueda, encima de la lista y dentro del mismo panel de
# cristal. Filtra la música que ya tienes; con el módulo opcional de
# descargas instalado, además sirve para buscar canciones nuevas.
ALTO_BARRA_BUSQUEDA = 38
# Alto real que le queda a la lista una vez descontada la barra de abajo
# (ya no hay línea separadora entre ambas).
ALTO_LISTA = ALTO_ZONA_SUPERIOR - ALTO_BARRA_BUSQUEDA
# Alto de cada fila de canción, ahora más compacta para que quepan más
# títulos a la vez sin necesidad de hacer tanto scroll.
ALTO_FILA_CANCION = 28
# Alto de la franja de degradado que difumina la lista al llegar a los
# bordes (arriba solo si has hecho scroll, abajo si queda más contenido).
# Radio de esquina compartido por las filas de la lista y la píldora de
# enlace/búsqueda, para que ambas usen el mismo "lenguaje" de redondeado
# (la tarjeta y la lista usan RADIO_ESQUINAS, mucho mayor, por ser
# elementos grandes; estos son controles pequeños dentro de ella).
RADIO_ELEMENTOS = 8
ALTURA_DESVANECIDO = 18

# Azul de acento: el mismo tono principal de la onda/nota (#4F8CFF), usado
# también como color activo de los botones y del play, para que todo el
# "estado pulsado/activo" de la interfaz hable el mismo idioma visual.
COLOR_ACENTO = QColor(79, 140, 255)
COLOR_ACENTO_CSS = f"rgb({COLOR_ACENTO.red()}, {COLOR_ACENTO.green()}, {COLOR_ACENTO.blue()})"

# --- Modo compacto -------------------------------------------------
# Al pulsar sobre la nota/portada, la ventana se encoge a una tarjeta
# horizontal (miniatura a la izquierda, título y controles a la derecha)
# y se coloca en la esquina superior izquierda de la pantalla, para
# dejarla de fondo mientras se trabaja. Se vuelve al modo normal
# pulsando otra vez sobre la miniatura.
ANCHO_COMPACTO = 296
ALTO_COMPACTO = 114
# Radio menor que el de la tarjeta grande: en una tarjeta tan baja, los
# 26px de RADIO_ESQUINAS se comerían casi medio borde.
RADIO_ESQUINAS_COMPACTO = 18
LADO_PORTADA_COMPACTA = 84
RADIO_PORTADA_COMPACTA = 14
# Separación de la ventana compacta respecto a las esquinas de la pantalla.
MARGEN_PANTALLA_COMPACTO = 16

# Si al pulsar "anterior" la canción lleva sonando más de este tiempo, no
# se salta a la canción previa: se rebobina la actual al principio. Es el
# comportamiento habitual en reproductores (Spotify, Apple Music) y evita
# perder la canción de golpe por un toque de más.
UMBRAL_REBOBINAR_MS = 10_000

# Textos del campo de búsqueda. Cambian con el modo porque son la pista
# principal de qué va a pasar al escribir: filtrar lo que ya tienes o
# descargar algo nuevo.
TEXTO_BUSCAR_LOCAL = "Buscar en tu música…"

# Ajustes que sobreviven al cierre de la app. En macOS, QSettings los
# guarda en ~/Library/Preferences/com.digilogic.Digilogic.plist.
AJUSTES_ORGANIZACION = "Digilogic"
AJUSTES_APLICACION = "Digilogic"
CLAVE_CARPETA_MUSICA = "carpeta_musica"
# Dónde se quedó la escucha la última vez. Al reabrir, la app vuelve a
# esa canción y a ese minuto, pero en pausa: si se cerró sin querer no
# se pierde el sitio, y si se cerró a propósito tampoco arranca sola
# soltando música.
CLAVE_ULTIMA_CANCION = "ultima_cancion"
CLAVE_ULTIMA_POSICION = "ultima_posicion_ms"

# Cada cuánto se anota en disco el minuto por el que va la canción.
# Guardarlo en cada aviso de progreso serían unas diez escrituras por
# segundo; cada cinco segundos es suficiente, porque lo peor que puede
# pasar es reanudar cinco segundos antes de donde lo dejaste.
INTERVALO_GUARDAR_POSICION_MS = 5_000


# ------------------------------------------------------------------
# Centro multimedia de macOS
# ------------------------------------------------------------------

class CentroMultimediaMac:
    """Registra Digilogic en el centro multimedia del sistema.

    Sirve para dos cosas. La primera es que las teclas de reproducción
    del teclado (▶︎❙❙, ⏮, ⏭) lleguen a la app aunque la ventana esté
    detrás de otra, que es justo el caso para el que existe el modo
    miniatura. La segunda es que la canción aparezca en el Centro de
    Control, en la pantalla bloqueada y en los AirPods, como cualquier
    reproductor del sistema.

    macOS solo entrega las teclas de medios a aplicaciones empaquetadas,
    así que ejecutando 'python Digilogic.py' desde el código lo normal es
    que no funcionen; dentro del .app sí.

    Si algo falla al registrarse -otra versión de macOS, pyobjc a medias-
    se desactiva en silencio: son teclas de más, no vale la pena que
    tiren la aplicación por ellas.
    """

    # Lo que espera el sistema de vuelta de cada orden.
    EXITO = 0

    def __init__(self, reproductor):
        self.reproductor = reproductor
        self.activo = False
        # Los manejadores hay que guardarlos: si Python los recolecta, el
        # sistema se queda con punteros muertos y las teclas dejan de
        # responder al rato de arrancar.
        self._manejadores = []

        if MPRemoteCommandCenter is None:
            return

        try:
            self._registrar()
            self.activo = True
        except Exception:
            self.activo = False

    def _registrar(self):
        centro = MPRemoteCommandCenter.sharedCommandCenter()

        def conectar(orden, accion):
            def manejador(evento):
                accion()
                return self.EXITO

            self._manejadores.append(manejador)
            orden.setEnabled_(True)
            orden.addTargetWithHandler_(manejador)

        r = self.reproductor
        conectar(centro.togglePlayPauseCommand(), r.play_pausa)
        conectar(centro.playCommand(), r.play_pausa)
        conectar(centro.pauseCommand(), r.play_pausa)
        conectar(centro.nextTrackCommand(), r.siguiente_cancion)
        conectar(centro.previousTrackCommand(), r.anterior_cancion)

    def actualizar(self, titulo, artista, duracion_ms, posicion_ms, sonando):
        """Refresca lo que el sistema muestra como 'reproduciendo ahora'."""
        if not self.activo:
            return
        try:
            centro = MPNowPlayingInfoCenter.defaultCenter()
            centro.setNowPlayingInfo_({
                MPMediaItemPropertyTitle: titulo,
                MPMediaItemPropertyArtist: artista or "",
                MPMediaItemPropertyPlaybackDuration: duracion_ms / 1000.0,
                MPNowPlayingInfoPropertyElapsedPlaybackTime: posicion_ms / 1000.0,
                # A 0 el sistema entiende que está en pausa y congela el
                # contador; a 1, que avanza a velocidad normal.
                MPNowPlayingInfoPropertyPlaybackRate: 1.0 if sonando else 0.0,
            })
            centro.setPlaybackState_(
                MPNowPlayingPlaybackStatePlaying if sonando
                else MPNowPlayingPlaybackStatePaused
            )
        except Exception:
            # Una actualización perdida no rompe nada: la siguiente
            # canción o el siguiente play vuelven a intentarlo.
            pass


class Reproductor(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setFixedSize(ANCHO_TARJETA, ALTO_TARJETA)

        self.canciones = []
        self.indice_actual = -1
        self.aleatorio_activado = False
        # Historial real de escucha: qué canciones han sonado y en qué
        # orden. Sin esto, en modo aleatorio "anterior" sorteaba otra
        # canción al azar en vez de volver a la que acababa de sonar.
        self.historial = []
        # Canciones de las que se ha vuelto atrás. Permite que "siguiente"
        # rehaga el camino en lugar de sortear una nueva, igual que las
        # flechas atrás/adelante de un navegador.
        self.pila_siguientes = []
        self._arrastrando_slider = False
        self._posicion_click = None
        self.modo_compacto = False
        # Minuto al que hay que saltar en cuanto el archivo esté cargado.
        # No se puede pedir antes: hasta que el reproductor no tiene el
        # medio listo, setPosition() se ignora sin avisar.
        self._posicion_pendiente = None
        self._ultima_posicion_guardada = 0
        # Dónde estaba la ventana antes de encogerse, para devolverla ahí
        # al salir del modo compacto.
        self._posicion_antes_compacto = None

        # Toda la funcionalidad de descarga vive detrás de este objeto. Es
        # None en la versión pública, donde 'descargas.py' no existe, y el
        # resto del código se limita a comprobarlo antes de delegar.
        self.control_descargas = (
            descargas.ControlDescargas(
                ventana=self,
                color_acento=COLOR_ACENTO,
                texto_busqueda_local=TEXTO_BUSCAR_LOCAL,
            )
            if descargas is not None
            else None
        )

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.mediaStatusChanged.connect(self.al_cambiar_estado_medio)
        # El cambio de estado (sonando/pausado) puede llegar con un
        # pequeño retraso tras pedir play() en una canción recién
        # cargada -> nos enganchamos a la señal real en vez de
        # comprobarlo nosotros justo después de llamar a play(),
        # que a veces se adelantaba y dejaba el ecualizador apagado.
        self.player.playbackStateChanged.connect(self._actualizar_indicadores_ecualizador)
        self.player.positionChanged.connect(self.actualizar_progreso)
        self.player.durationChanged.connect(self.actualizar_duracion)

        self.ajustes = QSettings(AJUSTES_ORGANIZACION, AJUSTES_APLICACION)

        # Para que las teclas sueltas (espacio, flechas) lleguen a la
        # ventana en vez de perderse. Los botones se quedan sin foco más
        # abajo, en construir_interfaz.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Soltar una carpeta encima elige esa carpeta.
        self.setAcceptDrops(True)

        self.construir_interfaz()

        # Teclas de medios del teclado y ficha de "reproduciendo ahora".
        # Solo hace algo en macOS y con pyobjc instalado.
        self.centro_multimedia = CentroMultimediaMac(self)

        self._restaurar_carpeta_guardada()

    # ---------------- Construcción visual ----------------

    def construir_interfaz(self):
        self.tarjeta = QWidget(self)
        self.tarjeta.setObjectName("tarjeta")
        self.tarjeta.setFixedSize(ANCHO_TARJETA, ALTO_TARJETA)
        self.tarjeta.move(0, 0)
        self.tarjeta.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        color_panel = Portada.COLOR_FONDO_PANEL
        color_panel_css = f"rgb({color_panel.red()}, {color_panel.green()}, {color_panel.blue()})"

        self._aplicar_estilo_tarjeta(RADIO_ESQUINAS)
        self._quitar_mascara_tarjeta()

        # --- Barra superior: en macOS, una fila con el semáforo a la
        #     izquierda; en Windows, una barra de título pegada al borde
        #     con el nombre y la "×" a la derecha. ---
        if sys.platform == "win32":
            # Va fuera del layout, a ras del borde superior de la
            # tarjeta, como la barra de cualquier ventana del sistema.
            self.barra_titulo = BarraTituloWindows(
                ANCHO_TARJETA, RADIO_ESQUINAS, parent=self.tarjeta
            )
            self.barra_titulo.move(0, 0)
            self.boton_cerrar = self.barra_titulo.boton_cerrar
            self.boton_cerrar.clicked.connect(self.close)
            self.barra_superior = None
        else:
            self.barra_titulo = None
            self.barra_superior = QWidget()
            # Sin alto fijo, esta fila reclamaba 31px para un botón de 14
            # y dejaba una franja vacía enorme sobre la nota. Se ata al
            # alto exacto del botón para que no sobre ni un píxel.
            self.barra_superior.setFixedHeight(ALTO_FILA_SEMAFORO)
            barra_superior = QHBoxLayout(self.barra_superior)
            barra_superior.setContentsMargins(0, 0, 0, 0)

            # Un punto por encima del semáforo real de macOS (12 px): a 16
            # se veía desproporcionado, pero a 12 clavados quedaba
            # demasiado pequeño para esta tarjeta, que es bastante más
            # estrecha que una ventana normal del sistema.
            self.boton_cerrar = BotonSemaforo(color_hover=QColor(255, 95, 86), tamano=14)
            self.boton_cerrar.clicked.connect(self.close)
            barra_superior.addWidget(self.boton_cerrar)
            barra_superior.addStretch()

        # --- Portada / lista (comparten el mismo hueco, se turnan) ---
        self.stack_superior = QStackedLayout()
        self.stack_superior.setContentsMargins(0, 0, 0, 0)

        self.pagina_portada = QWidget()
        pagina_portada = self.pagina_portada
        self.layout_pagina_portada = QHBoxLayout(pagina_portada)
        self.layout_pagina_portada.setContentsMargins(0, 0, 0, 0)
        self.portada = Portada(
            ancho=ANCHO_ZONA_SUPERIOR, alto=ALTO_ZONA_SUPERIOR, radio=RADIO_ESQUINAS
        )
        self.portada.clicked.connect(self.alternar_modo_compacto)
        self.layout_pagina_portada.addWidget(self.portada)
        self.stack_superior.addWidget(pagina_portada)

        self.lista_canciones = QListWidget()
        self.lista_canciones.itemDoubleClicked.connect(self.reproducir_seleccionada)
        self.lista_canciones.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_canciones.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._filas_canciones = []  # widgets FilaCancion, para actualizar el ecualizador

        # --- Degradados arriba/abajo de la lista: viven encima de la
        #     propia lista (no ocupan hueco en el layout) y se muestran u
        #     ocultan según la posición del scroll. ---
        self.desvanecido_arriba = DesvanecidoBorde(arriba=True, color_fondo=color_panel)
        self.desvanecido_arriba.setParent(self.lista_canciones)
        self.desvanecido_arriba.setGeometry(0, 0, ANCHO_ZONA_SUPERIOR, ALTURA_DESVANECIDO)
        self.desvanecido_arriba.hide()  # nada que difuminar al principio

        self.desvanecido_abajo = DesvanecidoBorde(arriba=False, color_fondo=color_panel)
        self.desvanecido_abajo.setParent(self.lista_canciones)
        self.desvanecido_abajo.setGeometry(
            0, ALTO_LISTA - ALTURA_DESVANECIDO, ANCHO_ZONA_SUPERIOR, ALTURA_DESVANECIDO
        )

        barra_scroll = self.lista_canciones.verticalScrollBar()
        barra_scroll.valueChanged.connect(self._actualizar_desvanecidos_lista)
        barra_scroll.rangeChanged.connect(lambda *_: self._actualizar_desvanecidos_lista())

        # --- Barra de búsqueda, arriba del todo (como en MusicPi):
        #     interruptor de modo + campo de texto en una sola píldora ---
        barra_busqueda = QWidget()
        barra_busqueda.setObjectName("barra_busqueda")
        barra_busqueda.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        barra_busqueda.setFixedHeight(ALTO_BARRA_BUSQUEDA)
        barra_busqueda.setStyleSheet(f"""
            #barra_busqueda {{
                background-color: {color_panel_css};
                border-top-left-radius: {RADIO_ESQUINAS}px;
                border-top-right-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout_barra_busqueda = QHBoxLayout(barra_busqueda)
        # Más margen arriba que abajo: así la píldora se separa del borde
        # superior redondeado y queda pegada a la lista que va debajo.
        layout_barra_busqueda.setContentsMargins(8, 7, 8, 3)

        contenedor_busqueda = QWidget()
        contenedor_busqueda.setObjectName("contenedor_busqueda")
        contenedor_busqueda.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        contenedor_busqueda.setFixedHeight(28)
        RADIO_PILDORA = 14  # mitad del alto (28) -> píldora bien redondeada
        contenedor_busqueda.setStyleSheet(f"""
            #contenedor_busqueda {{
                background-color: rgba(255, 255, 255, 20);
                border: none;
                border-radius: {RADIO_PILDORA}px;
            }}
        """)
        layout_contenedor_busqueda = QHBoxLayout(contenedor_busqueda)
        layout_contenedor_busqueda.setContentsMargins(2, 0, 12, 0)
        layout_contenedor_busqueda.setSpacing(6)

        # A la izquierda del campo, donde suele estar la lupa en cualquier
        # buscador. Con el módulo de descargas presente es un interruptor
        # de modo; sin él, una lupa fija que solo indica que ahí se busca.
        if self.control_descargas is not None:
            icono_barra = self.control_descargas.boton
        else:
            icono_barra = IconoLupa(lado=24)
        layout_contenedor_busqueda.addWidget(icono_barra, 0, Qt.AlignmentFlag.AlignVCenter)

        self.campo_busqueda = QLineEdit()
        self.campo_busqueda.setFrame(False)
        self.campo_busqueda.setPlaceholderText(TEXTO_BUSCAR_LOCAL)
        self.campo_busqueda.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: white;
                font-size: 11px;
                padding: 0px;
                selection-background-color: rgba(255,255,255,70);
            }
        """)
        self.campo_busqueda.textChanged.connect(self._al_cambiar_texto_campo_busqueda)
        # Enter solo hace algo si se pueden descargar canciones: al buscar
        # en la música local el filtrado ya es en vivo y no hay nada que
        # confirmar.
        if self.control_descargas is not None:
            self.campo_busqueda.returnPressed.connect(
                self.control_descargas.intentar_descargar
            )
        layout_contenedor_busqueda.addWidget(self.campo_busqueda, 1)

        layout_barra_busqueda.addWidget(contenedor_busqueda, 1, Qt.AlignmentFlag.AlignVCenter)

        # --- Contenedor que agrupa lista + barra, y que lleva la
        #     máscara redondeada real (antes iba solo en la lista) ---
        contenedor_lista = QWidget()
        layout_contenedor_lista = QVBoxLayout(contenedor_lista)
        layout_contenedor_lista.setContentsMargins(0, 0, 0, 0)
        layout_contenedor_lista.setSpacing(0)
        layout_contenedor_lista.addWidget(barra_busqueda)
        layout_contenedor_lista.addWidget(self.lista_canciones, 1)

        ruta_mascara_lista = QPainterPath()
        ruta_mascara_lista.addRoundedRect(
            QRectF(0, 0, ANCHO_ZONA_SUPERIOR, ALTO_ZONA_SUPERIOR), RADIO_ESQUINAS, RADIO_ESQUINAS
        )
        contenedor_lista.setMask(QRegion(ruta_mascara_lista.toFillPolygon().toPolygon()))
        self.stack_superior.addWidget(contenedor_lista)

        self.contenedor_superior = QWidget()
        self.contenedor_superior.setLayout(self.stack_superior)
        self.contenedor_superior.setFixedHeight(ALTO_ZONA_SUPERIOR)

        # --- Título (con marquesina al reproducir) y botón de elegir carpeta a la vez ---
        self.label_titulo = TituloAnimado("Selecciona una carpeta")
        self.label_titulo.clicked.connect(self.elegir_carpeta)

        # --- Progreso ---
        self.slider_progreso = SliderProgreso(Qt.Orientation.Horizontal)
        self._progreso_visible = None
        self.slider_progreso.valueChanged.connect(self.actualizar_estilo_progreso)
        self.actualizar_estilo_progreso(0)
        self.slider_progreso.sliderPressed.connect(self.al_empezar_arrastre)
        self.slider_progreso.sliderReleased.connect(self.al_soltar_arrastre)

        self.fila_tiempos = QWidget()
        fila_tiempos = QHBoxLayout(self.fila_tiempos)
        fila_tiempos.setContentsMargins(0, 0, 0, 0)
        self.label_tiempo_actual = QLabel("0:00")
        self.label_tiempo_total = QLabel("0:00")
        self.label_tiempo_actual.setStyleSheet("color: rgba(255,255,255,140); font-size: 11px;")
        self.label_tiempo_total.setStyleSheet("color: rgba(255,255,255,140); font-size: 11px;")
        fila_tiempos.addWidget(self.label_tiempo_actual)
        fila_tiempos.addStretch()
        fila_tiempos.addWidget(self.label_tiempo_total)

        # --- Controles: lista | anterior | play | siguiente | aleatorio ---
        self.contenedor_controles = QWidget()
        self.layout_controles = QHBoxLayout(self.contenedor_controles)
        self.layout_controles.setContentsMargins(0, 0, 0, 0)
        self.layout_controles.setSpacing(18)
        self.layout_controles.addStretch()

        self.boton_lista = BotonLista(tamano=26)
        self.boton_lista.clicked.connect(self.alternar_lista)
        self.layout_controles.addWidget(self.boton_lista)

        self.boton_anterior = self.crear_boton_icono("anterior", tamano=36)
        self.boton_anterior.clicked.connect(self.anterior_cancion)
        self.layout_controles.addWidget(self.boton_anterior)

        self.boton_play = crear_boton_control("play")
        self._aplicar_estilo_play(50)
        self.boton_play.clicked.connect(self.play_pausa)
        self.layout_controles.addWidget(self.boton_play)

        self.boton_siguiente = self.crear_boton_icono("siguiente", tamano=36)
        self.boton_siguiente.clicked.connect(self.siguiente_cancion)
        self.layout_controles.addWidget(self.boton_siguiente)

        self.boton_aleatorio = BotonAleatorio(tamano=26)
        self.boton_aleatorio.clicked.connect(self.alternar_aleatorio)
        # El botón de aleatorio y su puntito viven en una columna propia,
        # envuelta en un widget para que el layout de controles pueda
        # desmontarse y volverse a montar sin destruirlos (modo compacto).
        self.contenedor_aleatorio = QWidget()
        columna_aleatorio = QVBoxLayout(self.contenedor_aleatorio)
        columna_aleatorio.setSpacing(0)
        columna_aleatorio.setContentsMargins(0, 0, 0, 0)
        # Espacio arriba igual al que ocupan la separación + el punto de abajo,
        # así el botón queda centrado respecto a los demás (no el bloque entero)
        columna_aleatorio.addSpacing(9)
        columna_aleatorio.addWidget(self.boton_aleatorio, alignment=Qt.AlignmentFlag.AlignHCenter)
        columna_aleatorio.addSpacing(4)
        self.punto_aleatorio = PuntoIndicador(diametro=5)
        columna_aleatorio.addWidget(self.punto_aleatorio, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.layout_controles.addWidget(self.contenedor_aleatorio, 0, Qt.AlignmentFlag.AlignVCenter)

        self.layout_controles.addStretch()

        self._montar_layout_normal()

        # Ningún botón se queda el foco al pulsarlo. Si se lo quedara, la
        # barra espaciadora volvería a activar ese botón -por ejemplo el
        # de aleatorio- en vez de pausar la música. El campo de búsqueda
        # sí conserva el suyo, que es donde el espacio debe escribirse.
        #
        # Va después de montar el layout: hasta entonces los botones
        # cuelgan de contenedores todavía sueltos y 'findChildren' no los
        # encuentra.
        for boton in self.findChildren(QPushButton):
            boton.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---------------- Montaje de los dos layouts ----------------

    def _aplicar_estilo_tarjeta(self, radio):
        """Hoja de estilos de la tarjeta. Se regenera al cambiar de modo
        porque el radio de las esquinas no es el mismo en la tarjeta
        grande que en la compacta."""
        color_panel = Portada.COLOR_FONDO_PANEL
        color_panel_css = f"rgb({color_panel.red()}, {color_panel.green()}, {color_panel.blue()})"
        self.tarjeta.setStyleSheet(f"""
            #tarjeta {{
                background-color: rgba(28, 28, 32, 235);
                border-radius: {radio}px;
                border: 1px solid rgba(255, 255, 255, 40);
            }}
            QLabel {{ color: white; }}
            QListWidget {{
                background-color: {color_panel_css};
                border: none;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                border-bottom-left-radius: {RADIO_ESQUINAS}px;
                border-bottom-right-radius: {RADIO_ESQUINAS}px;
                padding: 4px 6px 8px 4px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 0px;
                margin: 1px 2px;
                border-radius: {RADIO_ELEMENTOS}px;
                border: 1px solid transparent;
            }}
            QListWidget::item:hover {{
                background-color: rgba(255, 255, 255, 10);
            }}
            QListWidget::item:selected {{
                background-color: rgba(255, 255, 255, 16);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: {RADIO_ELEMENTOS}px;
            }}
        """)

    def _quitar_mascara_tarjeta(self):
        """La tarjeta no lleva máscara: su forma redondeada la pinta el
        'border-radius' de la hoja de estilos, que sí sale suavizado.

        Antes se recortaba con setMask(). Una máscara es una QRegion, es
        decir, píxeles enteros: o dentro o fuera, sin medias tintas. Eso
        deja las esquinas con dientes de sierra, muy marcados en Windows
        y sutiles pero visibles también en Retina, porque el escalón
        mide un punto lógico (dos píxeles físicos).

        Y no hace falta recortar nada: los hijos quedan todos dentro de
        los márgenes del layout, así que ninguno llega a asomar por las
        esquinas.
        """
        self.tarjeta.clearMask()

    def _vaciar_layout(self, layout):
        """Saca todos los widgets de un layout (incluidos los que estén en
        layouts anidados) y los devuelve a la tarjeta. Sin esto, al tirar
        el layout viejo Qt se llevaría por delante los widgets que
        contiene, y el modo compacto solo funcionaría una vez."""
        while layout.count():
            elemento = layout.takeAt(0)
            widget = elemento.widget()
            if widget is not None:
                widget.setParent(self.tarjeta)
                continue
            sublayout = elemento.layout()
            if sublayout is not None:
                self._vaciar_layout(sublayout)

    def _preparar_layout_tarjeta(self):
        """Deja la tarjeta sin layout, conservando todos sus widgets."""
        layout_viejo = self.tarjeta.layout()
        if layout_viejo is None:
            return
        self._vaciar_layout(layout_viejo)
        # Un widget temporal adopta el layout vacío y se lo lleva consigo:
        # es la forma habitual en Qt de deshacerse de un layout ya puesto.
        QWidget().setLayout(layout_viejo)

    def _montar_layout_normal(self):
        """Tarjeta vertical grande: portada/lista arriba, luego título,
        progreso y la fila completa de controles."""
        self._preparar_layout_tarjeta()

        # La portada vuelve a su hueco dentro del stack portada/lista.
        if self.portada.parent() is not self.pagina_portada:
            self.layout_pagina_portada.addWidget(self.portada)
        self.portada.redimensionar(ANCHO_ZONA_SUPERIOR, ALTO_ZONA_SUPERIOR, RADIO_ESQUINAS)
        self.slider_progreso.setFixedHeight(20)

        layout = QVBoxLayout(self.tarjeta)
        layout.setSpacing(5)
        if sys.platform == "win32":
            # La barra de título no entra en el layout: se reserva su
            # alto en el margen superior y ella se queda pegada arriba.
            layout.setContentsMargins(
                16, ALTO_BARRA_TITULO_WINDOWS + SEPARACION_BARRA_TITULO_WINDOWS, 16, 14
            )
            self.barra_titulo.move(0, 0)
            self.barra_titulo.show()
            self.barra_titulo.raise_()
        else:
            layout.setContentsMargins(16, MARGEN_SUPERIOR_MAC, 16, 14)
            layout.addWidget(self.barra_superior)
            self.barra_superior.show()
        layout.addWidget(self.contenedor_superior)
        layout.addWidget(self.label_titulo)
        layout.addWidget(self.slider_progreso)
        layout.addWidget(self.fila_tiempos)
        layout.addWidget(self.contenedor_controles)

        self.label_titulo.centrado = True
        self.contenedor_superior.show()
        self.boton_lista.show()
        self.contenedor_aleatorio.show()

        self._aplicar_estilo_play(50)
        self._aplicar_estilo_icono(self.boton_anterior, 36)
        self._aplicar_estilo_icono(self.boton_siguiente, 36)
        self.layout_controles.setSpacing(18)

        for etiqueta in (self.label_tiempo_actual, self.label_tiempo_total):
            etiqueta.setStyleSheet("color: rgba(255,255,255,140); font-size: 11px;")

    def _montar_layout_compacto(self):
        """Tarjeta horizontal pequeña: miniatura a la izquierda, y a la
        derecha título, progreso y solo los controles imprescindibles."""
        self._preparar_layout_tarjeta()

        # La portada sale del stack para colocarse a la izquierda del todo.
        self.layout_pagina_portada.removeWidget(self.portada)
        self.portada.setParent(self.tarjeta)
        self.portada.redimensionar(
            LADO_PORTADA_COMPACTA, LADO_PORTADA_COMPACTA, RADIO_PORTADA_COMPACTA
        )
        self.portada.show()
        # Más fino que en modo normal: aquí cada píxel de alto cuenta.
        self.slider_progreso.setFixedHeight(10)

        layout = QHBoxLayout(self.tarjeta)
        layout.setContentsMargins(12, 10, 14, 10)
        layout.setSpacing(11)
        layout.addWidget(self.portada, 0, Qt.AlignmentFlag.AlignVCenter)

        columna = QVBoxLayout()
        columna.setContentsMargins(0, 0, 0, 0)
        columna.setSpacing(1)
        columna.addWidget(self.label_titulo)
        columna.addWidget(self.slider_progreso)
        columna.addWidget(self.fila_tiempos)
        columna.addWidget(self.contenedor_controles)
        layout.addLayout(columna, 1)

        # A este tamaño no caben (ni hacen falta) la lista ni el aleatorio:
        # el título se lee mejor con el hueco entero para él.
        self.label_titulo.centrado = False
        # En compacto no hay fila superior ni barra de título: se cierra
        # volviendo al modo normal, en los dos sistemas.
        if sys.platform == "win32":
            self.barra_titulo.hide()
        else:
            self.barra_superior.hide()
        self.contenedor_superior.hide()
        self.boton_lista.hide()
        self.contenedor_aleatorio.hide()

        self._aplicar_estilo_play(40)
        self._aplicar_estilo_icono(self.boton_anterior, 30)
        self._aplicar_estilo_icono(self.boton_siguiente, 30)
        self.layout_controles.setSpacing(14)

        for etiqueta in (self.label_tiempo_actual, self.label_tiempo_total):
            etiqueta.setStyleSheet("color: rgba(255,255,255,130); font-size: 9px;")

    def alternar_modo_compacto(self):
        """Entra o sale del modo compacto. Se dispara al pulsar sobre la
        nota musical (o sobre la portada del álbum, que ocupan el mismo
        widget), tanto para encoger como para volver al tamaño normal."""
        self.modo_compacto = not self.modo_compacto

        if self.modo_compacto:
            # Al encoger siempre se muestra la portada, nunca la lista.
            self.stack_superior.setCurrentIndex(0)
            self._posicion_antes_compacto = self.pos()
            ancho, alto, radio = ANCHO_COMPACTO, ALTO_COMPACTO, RADIO_ESQUINAS_COMPACTO
        else:
            ancho, alto, radio = ANCHO_TARJETA, ALTO_TARJETA, RADIO_ESQUINAS

        self.setFixedSize(ancho, alto)
        self.tarjeta.setFixedSize(ancho, alto)
        self.tarjeta.move(0, 0)
        self._quitar_mascara_tarjeta()
        self._aplicar_estilo_tarjeta(radio)

        if self.modo_compacto:
            self._montar_layout_compacto()
            zona = self.screen().availableGeometry()
            self.move(
                zona.left() + MARGEN_PANTALLA_COMPACTO,
                zona.top() + MARGEN_PANTALLA_COMPACTO,
            )
        else:
            self._montar_layout_normal()
            if self._posicion_antes_compacto is not None:
                self.move(self._posicion_antes_compacto)

    def _aplicar_estilo_play(self, tamano):
        self.boton_play.setFixedSize(tamano, tamano)
        self.boton_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACENTO_CSS};
                color: white;
                border-radius: {tamano // 2}px;
                font-size: {int(tamano * 0.36)}px;
            }}
            QPushButton:hover {{ background-color: rgb(101, 158, 255); }}
            QPushButton:pressed {{ background-color: rgb(58, 111, 217); }}
        """)

    def _aplicar_estilo_icono(self, boton, tamano):
        boton.setFixedSize(tamano, tamano)
        boton.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255,255,255,22);
                color: white;
                border-radius: {tamano // 2}px;
                font-size: {int(tamano * 0.4)}px;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,45); }}
            QPushButton:pressed {{ background-color: {COLOR_ACENTO_CSS}; }}
        """)

    def actualizar_estilo_progreso(self, posicion):
        """Evita que Qt pinte un pequeño tramo blanco cuando el valor es cero."""
        mostrar_progreso = posicion > self.slider_progreso.minimum()
        if mostrar_progreso == self._progreso_visible:
            return
        self._progreso_visible = mostrar_progreso
        color_progreso = COLOR_ACENTO_CSS if mostrar_progreso else "transparent"
        self.slider_progreso.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,60); border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {color_progreso}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: transparent; width: 0px; margin: 0; border: none; }}
        """)

    def crear_boton_icono(self, simbolo, tamano=32):
        boton = crear_boton_control(simbolo)
        self._aplicar_estilo_icono(boton, tamano)
        return boton

    # ---------------- Arrastrar la ventana (no tiene barra de título) ----------------

    def changeEvent(self, event):
        """Qt avisa del cambio de foco solo a la ventana, no a sus hijos,
        así que hay que repintar el semáforo a mano para que se apague al
        pasar la ventana a segundo plano y se encienda al volver."""
        if event.type() == QEvent.Type.ActivationChange and hasattr(self, "boton_cerrar"):
            self.boton_cerrar.update()
        super().changeEvent(event)

    # Métodos públicos porque la portada también los usa: al ocupar casi
    # toda la ventana, tiene que poder arrastrarla igual que el resto de
    # la tarjeta, aunque ella capture sus propios clics.

    def iniciar_arrastre(self, punto_global):
        self._posicion_click = punto_global - self.pos()

    def continuar_arrastre(self, punto_global):
        if self._posicion_click is not None:
            self.move(punto_global - self._posicion_click)

    def terminar_arrastre(self):
        self._posicion_click = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.iniciar_arrastre(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        self.continuar_arrastre(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event):
        self.terminar_arrastre()

    # ---------------- Lógica del reproductor ----------------

    # ---------------- Teclado ----------------

    # Teclas de reproducción del teclado. Windows las entrega a la app
    # directamente; en macOS las intercepta el sistema y llegan por el
    # centro multimedia (CentroMultimediaMac), no por aquí.
    TECLAS_PLAY_PAUSA = (
        Qt.Key.Key_MediaTogglePlayPause,
        Qt.Key.Key_MediaPlay,
        Qt.Key.Key_MediaPause,
    )

    def keyPressEvent(self, event):
        """Atajos de la ventana.

        Solo llegan aquí las teclas que ningún hijo con el foco se haya
        quedado antes: mientras se escribe en el buscador, el espacio y
        las flechas son suyos y esto ni se entera.
        """
        tecla = event.key()

        if tecla == Qt.Key.Key_Space or tecla in self.TECLAS_PLAY_PAUSA:
            self.play_pausa()
        elif tecla in (Qt.Key.Key_Left, Qt.Key.Key_MediaPrevious):
            self.anterior_cancion()
        elif tecla in (Qt.Key.Key_Right, Qt.Key.Key_MediaNext):
            self.siguiente_cancion()
        else:
            super().keyPressEvent(event)
            return

        event.accept()

    # ---------------- Arrastrar una carpeta hasta la ventana ----------

    @staticmethod
    def _destino_arrastrado(datos):
        """Traduce lo que se está arrastrando a (carpeta, canción).

        Acepta una carpeta -se abre entera- y también un MP3 suelto, en
        cuyo caso se abre la carpeta que lo contiene y se deja esa
        canción seleccionada, que es lo que espera quien arrastra un
        archivo concreto. Cualquier otra cosa se rechaza.
        """
        if not datos.hasUrls():
            return None, None

        for url in datos.urls():
            if not url.isLocalFile():
                continue
            ruta = url.toLocalFile()
            if os.path.isdir(ruta):
                return ruta, None
            if ruta.lower().endswith(".mp3"):
                return os.path.dirname(ruta), ruta

        return None, None

    def dragEnterEvent(self, event):
        carpeta, _ = self._destino_arrastrado(event.mimeData())
        if carpeta is None:
            event.ignore()
            return
        event.acceptProposedAction()
        self.portada.resaltar_arrastre(True)

    def dragMoveEvent(self, event):
        # Sin esto, algunos gestores de archivos retiran la aceptación a
        # mitad del gesto y el cursor pasa a "prohibido" sin soltar nada.
        carpeta, _ = self._destino_arrastrado(event.mimeData())
        event.acceptProposedAction() if carpeta else event.ignore()

    def dragLeaveEvent(self, event):
        self.portada.resaltar_arrastre(False)
        event.accept()

    def dropEvent(self, event):
        carpeta, cancion = self._destino_arrastrado(event.mimeData())
        if carpeta is None:
            event.ignore()
            return

        event.acceptProposedAction()
        self.portada.celebrar_soltar()

        # Si venía una posición guardada de la sesión anterior, ya no
        # vale: estamos abriendo otra carpeta a mano.
        self._posicion_pendiente = None
        self._cargar_mp3_de_carpeta(carpeta, seleccionar_ruta=cancion)

        # Si se arrastró un MP3 concreto, suena; si fue la carpeta
        # entera, se queda esperando a que se le dé al play.
        if cancion and cancion in self.canciones:
            self.play_pausa()

    def _restaurar_carpeta_guardada(self):
        """Al abrir, vuelve a cargar la última carpeta que se usó, para no
        tener que elegirla cada vez. Si ya no existe -un USB desconectado,
        una carpeta movida o borrada- se ignora sin avisar y la app se
        queda como recién instalada, pidiendo que elijas una."""
        carpeta = self.ajustes.value(CLAVE_CARPETA_MUSICA, "", type=str)
        if not (carpeta and os.path.isdir(carpeta)):
            return

        # Hay que leer las dos claves ANTES de cargar la carpeta: cargarla
        # reproduce una canción y eso reescribe los ajustes.
        cancion = self.ajustes.value(CLAVE_ULTIMA_CANCION, "", type=str)
        posicion = self.ajustes.value(CLAVE_ULTIMA_POSICION, 0, type=int)

        if cancion and not os.path.isfile(cancion):
            cancion = ""

        self._cargar_mp3_de_carpeta(carpeta, seleccionar_ruta=cancion or None)

        # El salto al minuto se aplaza: hasta que el archivo no está
        # cargado del todo, setPosition() no tiene efecto. Lo hace
        # 'al_cambiar_estado_medio' en cuanto el medio está listo.
        if cancion and posicion > 0 and self.canciones and \
                0 <= self.indice_actual < len(self.canciones) and \
                self.canciones[self.indice_actual] == cancion:
            self._posicion_pendiente = posicion

    def elegir_carpeta(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, "Selecciona la carpeta con tus MP3 (puede ser tu USB)"
        )
        if not carpeta:
            return
        self._cargar_mp3_de_carpeta(carpeta)

    def _cargar_mp3_de_carpeta(self, carpeta, seleccionar_ruta=None):
        """
        Punto único donde se (re)lee una carpeta y se rellena la lista.
        Lo usan 'elegir_carpeta', la restauración de la carpeta guardada al
        arrancar y, si está instalado, el módulo de descargas, que lo llama
        para refrescar la lista sin perder la carpeta elegida.
        """
        self.carpeta_actual = carpeta
        # Se recuerda para la próxima vez que se abra la app.
        self.ajustes.setValue(CLAVE_CARPETA_MUSICA, carpeta)

        # Si ya había una canción cargada -por ejemplo sonando de fondo
        # mientras se descargaba otra- la recordamos para no cortarla al
        # reconstruir la lista de golpe.
        ruta_reproduciendo_antes = None
        if getattr(self, "canciones", None) and 0 <= getattr(self, "indice_actual", -1) < len(self.canciones):
            ruta_reproduciendo_antes = self.canciones[self.indice_actual]

        self.canciones = [
            os.path.join(carpeta, f)
            for f in sorted(os.listdir(carpeta))
            if f.lower().endswith(".mp3")
        ]

        # El historial guarda posiciones dentro de 'canciones', y esa lista
        # acaba de cambiar: conservarlo apuntaría a canciones equivocadas.
        self.historial.clear()
        self.pila_siguientes.clear()

        self.lista_canciones.clear()
        self._filas_canciones = []
        for ruta in self.canciones:
            titulo = os.path.splitext(os.path.basename(ruta))[0]
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ALTO_FILA_CANCION))
            self.lista_canciones.addItem(item)
            fila = FilaCancion(titulo)
            self.lista_canciones.setItemWidget(item, fila)
            self._filas_canciones.append(fila)

        if not self.canciones:
            self.label_titulo.establecer_texto("No se encontraron MP3")
            return

        if ruta_reproduciendo_antes and ruta_reproduciendo_antes in self.canciones:
            # Había algo sonando: no la recargamos en el reproductor (eso
            # cortaría el audio), solo la volvemos a seleccionar en la
            # lista nueva y refrescamos el ecualizador para que siga
            # marcando la fila correcta.
            self.indice_actual = self.canciones.index(ruta_reproduciendo_antes)
            self.lista_canciones.setCurrentRow(self.indice_actual)
            self._actualizar_indicadores_ecualizador()
        elif seleccionar_ruta and seleccionar_ruta in self.canciones:
            self.indice_actual = self.canciones.index(seleccionar_ruta)
            self.cargar_cancion(self.indice_actual, reproducir=False)
        else:
            self.indice_actual = 0
            self.cargar_cancion(self.indice_actual, reproducir=False)

    def alternar_lista(self):
        actual = self.stack_superior.currentIndex()
        self.stack_superior.setCurrentIndex(1 if actual == 0 else 0)

    def cargar_cancion(self, indice, reproducir=True):
        ruta = self.canciones[indice]
        self.player.setSource(QUrl.fromLocalFile(ruta))

        titulo, artista = extraer_metadatos(ruta)
        texto_completo = f"{titulo} — {artista}" if artista else titulo
        self.label_titulo.establecer_texto(texto_completo)

        portada = extraer_portada(ruta)
        if portada is not None:
            self.portada.mostrar_portada(portada)
        else:
            self.portada.limpiar_portada()
        self.portada.update()

        self.lista_canciones.setCurrentRow(indice)

        # Canción nueva: el punto de escucha vuelve al principio.
        self._guardar_punto_escucha(0)

        if reproducir:
            self.player.play()
            self.boton_play.establecer_simbolo("pausa")
            self.portada.onda.establecer_activa(True)
            self.label_titulo.establecer_reproduciendo(True)

        self._actualizar_indicadores_ecualizador()
        self._refrescar_centro_multimedia()

    def _al_cambiar_texto_campo_busqueda(self, texto):
        """Lo que escribes filtra en vivo la lista de canciones que ya
        tienes. Si el módulo de descargas está disponible y está en modo
        descarga, el texto es una consulta para YouTube, así que entonces
        la lista se deja entera en vez de filtrarse."""
        if self.control_descargas is None:
            self._filtrar_lista_canciones(texto)
            return

        self.control_descargas.al_cambiar_texto(texto)
        modo_descarga = self.control_descargas.modo_descarga
        self._filtrar_lista_canciones("" if modo_descarga else texto)

    def _filtrar_lista_canciones(self, filtro):
        filtro = filtro.strip().lower()
        for i, fila in enumerate(self._filas_canciones):
            item = self.lista_canciones.item(i)
            if item is None:
                continue
            titulo = fila.label.text().lower()
            item.setHidden(bool(filtro) and filtro not in titulo)

    def _actualizar_indicadores_ecualizador(self, *_):
        """Enciende el ecualizador solo en la fila que está sonando
        ahora mismo (y solo si de verdad está reproduciéndose, no en
        pausa), dejando el resto de filas sin indicador."""
        reproduciendo = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        for i, fila in enumerate(self._filas_canciones):
            fila.indicador.establecer_estado(i == self.indice_actual, reproduciendo)

    def _actualizar_desvanecidos_lista(self, *_):
        """El degradado de abajo se ve mientras quede lista por debajo;
        el de arriba solo aparece una vez que has hecho scroll hacia
        abajo (al principio no hay nada que difuminar arriba)."""
        barra = self.lista_canciones.verticalScrollBar()
        self.desvanecido_arriba.setVisible(barra.value() > barra.minimum())
        self.desvanecido_abajo.setVisible(barra.value() < barra.maximum())

    def reproducir_seleccionada(self, item):
        indice = self.lista_canciones.row(item)
        if self.indice_actual >= 0 and indice != self.indice_actual:
            self.historial.append(self.indice_actual)
        # Elegir una canción a mano rompe el camino de "adelante": a
        # partir de aquí, "siguiente" vuelve a decidir por su cuenta.
        self.pila_siguientes.clear()
        self.indice_actual = indice
        self.cargar_cancion(indice)

    def play_pausa(self):
        if not self.canciones:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.boton_play.establecer_simbolo("play")
            self.portada.onda.establecer_activa(False)
            self.label_titulo.establecer_reproduciendo(False)
        else:
            self.player.play()
            self.boton_play.establecer_simbolo("pausa")
            self.portada.onda.establecer_activa(True)
            self.label_titulo.establecer_reproduciendo(True)

        self._actualizar_indicadores_ecualizador()
        self._refrescar_centro_multimedia()

    def alternar_aleatorio(self):
        self.aleatorio_activado = not self.aleatorio_activado
        self.boton_aleatorio.establecer_activo(self.aleatorio_activado)
        self.punto_aleatorio.establecer_activo(self.aleatorio_activado)

    def siguiente_cancion(self):
        if not self.canciones:
            return

        if self.pila_siguientes:
            # Veníamos de retroceder: rehacemos el camino ya escuchado en
            # vez de sortear una canción nueva.
            nuevo_indice = self.pila_siguientes.pop()
        elif self.aleatorio_activado and len(self.canciones) > 1:
            nuevo_indice = self.indice_actual
            while nuevo_indice == self.indice_actual:
                nuevo_indice = random.randint(0, len(self.canciones) - 1)
        else:
            nuevo_indice = (self.indice_actual + 1) % len(self.canciones)

        if self.indice_actual >= 0:
            self.historial.append(self.indice_actual)
        self.indice_actual = nuevo_indice
        self.cargar_cancion(self.indice_actual)

    def anterior_cancion(self):
        if not self.canciones:
            return

        # Si la canción ya lleva un rato sonando, "anterior" se entiende
        # como "vuelve a empezar esta", no como "sáltala".
        if self.player.position() > UMBRAL_REBOBINAR_MS:
            self.player.setPosition(0)
            return

        if self.historial:
            # En aleatorio esto es lo único correcto: volver a la canción
            # que sonó de verdad, no a la anterior de la lista.
            nuevo_indice = self.historial.pop()
            self.pila_siguientes.append(self.indice_actual)
        else:
            nuevo_indice = (self.indice_actual - 1) % len(self.canciones)

        self.indice_actual = nuevo_indice
        self.cargar_cancion(self.indice_actual)

    def al_cambiar_estado_medio(self, estado):
        """Único sitio enganchado a 'mediaStatusChanged'. Hace dos cosas:
        encadenar con la siguiente canción cuando una termina, y aplicar
        el salto al minuto guardado en cuanto el archivo está listo."""
        if estado == QMediaPlayer.MediaStatus.EndOfMedia:
            self.siguiente_cancion()
            return

        if self._posicion_pendiente is not None and estado in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            posicion = self._posicion_pendiente
            self._posicion_pendiente = None
            # Nunca justo al final: reanudar en el último segundo haría
            # saltar de inmediato a la siguiente canción.
            if 0 < posicion < max(0, self.player.duration() - 1500):
                self.player.setPosition(posicion)

    # ---------------- Dónde se quedó la escucha ----------------

    def _guardar_punto_escucha(self, posicion_ms=None):
        """Anota canción y minuto para la próxima vez que se abra."""
        if not (self.canciones and 0 <= self.indice_actual < len(self.canciones)):
            return
        if posicion_ms is None:
            posicion_ms = self.player.position()
        self.ajustes.setValue(CLAVE_ULTIMA_CANCION, self.canciones[self.indice_actual])
        self.ajustes.setValue(CLAVE_ULTIMA_POSICION, int(posicion_ms))
        self._ultima_posicion_guardada = posicion_ms

    def closeEvent(self, event):
        """Al cerrar se anota el punto exacto, sin esperar al temporizador
        de los cinco segundos. Es el caso que de verdad importa: cerrar
        sin querer y volver justo donde estabas."""
        self._guardar_punto_escucha()
        super().closeEvent(event)

    def _refrescar_centro_multimedia(self):
        """Le cuenta al sistema qué está sonando, para el Centro de
        Control y la pantalla bloqueada."""
        if not (self.canciones and 0 <= self.indice_actual < len(self.canciones)):
            return
        titulo, artista = extraer_metadatos(self.canciones[self.indice_actual])
        self.centro_multimedia.actualizar(
            titulo,
            artista,
            self.player.duration(),
            self.player.position(),
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
        )

    # ---------------- Barra de progreso ----------------

    def al_empezar_arrastre(self):
        self._arrastrando_slider = True

    def al_soltar_arrastre(self):
        self._arrastrando_slider = False
        self.player.setPosition(self.slider_progreso.value())

    def actualizar_progreso(self, posicion):
        if not self._arrastrando_slider:
            self.slider_progreso.setValue(posicion)
        self.label_tiempo_actual.setText(formatear_tiempo(posicion))

        # De vez en cuando, no en cada aviso: llegan unos diez por segundo.
        if abs(posicion - self._ultima_posicion_guardada) >= INTERVALO_GUARDAR_POSICION_MS:
            self._guardar_punto_escucha(posicion)

    def actualizar_duracion(self, duracion):
        self.slider_progreso.setRange(0, duracion)
        self.label_tiempo_total.setText(formatear_tiempo(duracion))
        # La duración llega cuando el archivo ya está leído, que es el
        # primer momento en que la ficha del sistema puede estar completa.
        self._refrescar_centro_multimedia()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ventana = Reproductor()
    ventana.show()
    sys.exit(app.exec())
