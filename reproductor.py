"""
Digilogic - Reproductor de música, estilo "iPod + Cristal líquido"
------------------------------------------------------------------
- Elige una carpeta (puede estar en un USB) y reproduce la música directamente desde
  ahí, sin copiarla a ningún sitio. Ver EXTENSIONES_AUDIO para los formatos.
- Muestra la portada del álbum si el archivo la tiene incrustada.
- Si no hay portada, muestra una onda de audio animada estilo Siri (3 ondas RGB que
  se mezclan aditivamente, con desenfoque/glow).
- Ventana sin bordes, con esquinas redondeadas "de verdad" (recortadas con máscara)
  y efecto translúcido tipo "vidrio".
"""

import sys
import os
import base64
import math
import json
import random
import re
import array
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem, QSlider,
    QStackedLayout, QGraphicsOpacityEffect, QGraphicsBlurEffect, QStyle,
    QGraphicsScene, QGraphicsPixmapItem, QLineEdit, QSizePolicy, QGridLayout,
    QScrollArea, QFrame
)
from PyQt6.QtCore import (
    Qt, QUrl, QTimer, QTime, QRect, QRectF, QPointF, QSize, QSettings,
    QLocale, pyqtSignal, QObject, QPropertyAnimation, QEasingCurve,
    QVariantAnimation, QDate, QElapsedTimer, QFileSystemWatcher
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QPainterPath, QPainterPathStroker, QColor, QFont,
    QRegion, QPen, QFontMetrics, QTransform, QImage, QBitmap, QLinearGradient,
    QRadialGradient, QKeySequence, QShortcut
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QAudioDecoder, QAudioFormat
from PyQt6.QtNetwork import (
    QNetworkAccessManager, QNetworkReply, QNetworkRequest, QSslSocket
)

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
ARCHIVO_ICONO_LISTA = "icono lista.png"

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


# Lo que la aplicación considera música. No es solo MP3 aunque la carpeta
# del proyecto se llame así: el motor de audio que ya lleva dentro
# reproduce todo esto sin añadir nada, y dejarlo fuera solo servía para
# que alguien con su biblioteca en m4a -lo que sale de iTunes- abriera
# Digilogic, no viera ni una canción y pensara que la app está rota.
EXTENSIONES_AUDIO = (
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".aiff", ".aif",
    ".ogg", ".oga", ".opus", ".wma",
)


def es_audio(nombre):
    return nombre.lower().endswith(EXTENSIONES_AUDIO)


# Cuánto se espera, tras el último aviso de que la carpeta de música ha
# cambiado, antes de releerla. Copiar un solo archivo ya dispara varios
# avisos seguidos, y soltar un disco entero, decenas: releer con cada uno
# sería reconstruir la lista una y otra vez para nada.
ESPERA_REFRESCO_CARPETA_MS = 800


def _imagen_o_nada(datos):
    pixmap = QPixmap()
    return pixmap if datos and pixmap.loadFromData(datos) else None


def extraer_portada(ruta):
    """Lee la portada incrustada en un archivo de música (si la tiene).

    Cada formato la guarda en su sitio: los MP3 en una etiqueta APIC, los
    m4a en 'covr', los FLAC en una lista aparte de las etiquetas y los
    OGG en un texto en base64. Devuelve QPixmap o None.
    """
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(ruta)
        if audio is None:
            return None

        # FLAC: las imágenes no son etiquetas, van en su propia lista.
        for imagen in getattr(audio, "pictures", None) or ():
            pixmap = _imagen_o_nada(imagen.data)
            if pixmap is not None:
                return pixmap

        etiquetas = audio.tags
        if etiquetas is None:
            return None

        for clave in etiquetas.keys():
            texto = str(clave)
            if texto.startswith("APIC"):          # MP3, y AIFF/WAV con ID3
                pixmap = _imagen_o_nada(etiquetas[clave].data)
            elif texto == "covr":                 # m4a, aac de Apple
                pixmap = _imagen_o_nada(bytes(etiquetas[clave][0]))
            elif texto == "metadata_block_picture":  # OGG Vorbis y Opus
                from mutagen.flac import Picture
                pixmap = _imagen_o_nada(
                    Picture(base64.b64decode(etiquetas[clave][0])).data
                )
            else:
                continue
            if pixmap is not None:
                return pixmap
    except Exception:
        pass
    return None


def extraer_metadatos(ruta):
    """Devuelve (titulo, artista). Si no hay etiquetas, usa el nombre del archivo."""
    titulo = os.path.splitext(os.path.basename(ruta))[0]
    artista = ""
    if mutagen is not None:
        try:
            audio = mutagen.File(ruta, easy=True)
            if audio and audio.tags:
                # str() a propósito: no todos los formatos devuelven
                # cadenas de Python. Los WMA dan un objeto de mutagen que
                # se imprime bien pero revienta en cuanto alguien lo suma
                # o lo compara con un texto.
                if audio.tags.get("title"):
                    titulo = str(audio.tags["title"][0])
                if audio.tags.get("artist"):
                    artista = str(audio.tags["artist"][0])
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


def texto_de_dial(nombre):
    """Lo que se enseña en grande cuando suena una emisora.

    Casi toda emisora se conoce por su número -Cadena 100, LOS 40, 98.3-,
    así que si el nombre lleva uno, ese es el rótulo: es lo que hace que
    la pantalla se lea como el dial de una radio de verdad. Cuando no hay
    número se usa la primera palabra, que funciona como un logotipo. El
    nombre completo no se pierde: sigue debajo, en el título.
    """
    numero = re.search(r"\b(\d{2,3}(?:[.,]\d)?)\b", nombre)
    if numero:
        return numero.group(1).replace(",", ".")
    palabras = nombre.split()
    return (palabras[0] if palabras else nombre).upper()[:9]


def construir_texto_silueta(texto, ancho, alto):
    """El texto convertido en silueta, para rellenarlo con la onda igual
    que se rellena la nota musical.

    Se traza a un tamaño fijo y luego se escala el trazado, en vez de ir
    probando tamaños de fuente hasta dar con el que cabe: así encaja
    exacto a la primera y el resultado no depende de la fuente que tenga
    cada sistema. En negra, que los trazos finos no dejarían ver la onda.
    """
    if not texto:
        return QPainterPath()

    fuente = QFont()
    fuente.setPixelSize(100)
    fuente.setWeight(QFont.Weight.Black)

    ruta = QPainterPath()
    ruta.addText(0, 0, fuente, texto)
    # Imprescindible, y no es un detalle: en una fuente tan gruesa hay
    # letras cuyo propio trazo se cruza consigo mismo -la A, la B, la K,
    # la P, la R-, y con la regla par/impar que Qt trae por defecto el
    # cruce se cancela y la letra sale rota. Con winding, los trazos que
    # van en el mismo sentido se suman y solo los contornos interiores
    # (los huecos de la A o la O, que la fuente traza al revés) quedan
    # vacíos, que es justo lo que se quiere.
    ruta.setFillRule(Qt.FillRule.WindingFill)

    caja = ruta.boundingRect()
    if caja.isEmpty():
        return QPainterPath()

    escala = min(ancho * 0.80 / caja.width(), alto * 0.46 / caja.height())
    transformacion = QTransform()
    transformacion.translate(ancho / 2, alto / 2)
    transformacion.scale(escala, escala)
    transformacion.translate(-caja.center().x(), -caja.center().y())
    silueta = transformacion.map(ruta)
    silueta.setFillRule(Qt.FillRule.WindingFill)
    return silueta


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

        # La curva de volumen de la cancion que suena, un valor cada
        # RESOLUCION_MS, y por donde va la reproduccion. Sin curva -la
        # radio, o mientras se analiza- se vuelve al vaiven al azar de
        # siempre, que es mejor que quedarse quieto.
        self.envolvente = []
        self.resolucion_envolvente = 50
        self.posicion_ms = 0

        # Las formas del último fotograma, para que el brillo ambiental de
        # alrededor de la nota (_GlowNota) pueda repintar exactamente los
        # mismos lóbulos en vez de manchas planas de color.
        self.formas_actuales = []

        self.temporizador = QTimer(self)
        self.temporizador.timeout.connect(self.avanzar_animacion)
        self.temporizador.start(16)  # ~60 fotogramas por segundo

    # Por encima de este nivel se entiende que hay un golpe: 0.6 es el
    # techo del vaiven en reposo, asi que en silencio -y en la radio, que
    # no tiene curva- el empuje vale cero y la nota se ve exactamente
    # igual que siempre.
    NIVEL_REPOSO = 0.6

    def empuje(self):
        """Cuanto sobresale el momento actual por encima del reposo, de 0
        a 1. Es lo que hace que un golpe se vea: el nivel a secas mueve el
        radio de los lobulos un 5%, que no lo nota nadie."""
        if self.nivel <= self.NIVEL_REPOSO:
            return 0.0
        return min(1.0, (self.nivel - self.NIVEL_REPOSO) / (1.0 - self.NIVEL_REPOSO))

    def establecer_mascara(self, ruta):
        """Si se le da una QPainterPath, la onda solo se dibuja dentro de esa forma."""
        self.mascara = ruta
        self.update()

    def avanzar_animacion(self):
        self.tiempo += 0.016  # el movimiento nunca se detiene, haya o no música

        if self.activa and self.envolvente:
            # Entre dos avisos de posicion (llegan diez por segundo) el
            # reloj lo lleva la propia animacion, o la nota iria a
            # tirones en vez de seguir la musica.
            self.posicion_ms += 16
            indice = int(self.posicion_ms / self.resolucion_envolvente)
            if 0 <= indice < len(self.envolvente):
                # El silencio no apaga el orbe del todo: se queda
                # respirando bajo, como cuando no hay musica. El suelo
                # esta puesto para que el brillo medio sea parecido al de
                # antes; lo que cambia es que ahora hay recorrido.
                self.objetivo_nivel = 0.55 + 0.45 * self.envolvente[indice]
            # Sube de golpe y baja despacio, como un vumetro: asi el
            # golpe se ve y no se lo come el suavizado.
            velocidad = 0.45 if self.objetivo_nivel > self.nivel else 0.10
        else:
            if random.random() < 0.02:
                self.objetivo_nivel = (
                    random.uniform(0.8, 1.0) if self.activa else random.uniform(0.45, 0.6)
                )
            velocidad = 0.06  # suavizado, sin saltos

        self.nivel += (self.objetivo_nivel - self.nivel) * velocidad
        self.update()

    def establecer_envolvente(self, envolvente, resolucion_ms):
        self.envolvente = envolvente or []
        self.resolucion_envolvente = max(1, resolucion_ms)

    def establecer_posicion(self, posicion_ms):
        self.posicion_ms = max(0, posicion_ms)

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
        # El empuje va aparte del nivel para que el golpe se note: solo
        # actua por encima del reposo, asi que en silencio esto es
        # exactamente lo que era antes.
        radio_base = (
            lado_menor * config["radio_relativo"] * respiracion
            * (0.90 + 0.14 * self.nivel + 0.18 * self.empuje())
        )

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
            color = tenir_hacia_acento(config["color"])
            # Translúcidos a propósito: en mezcla aditiva, es el solape de
            # varios lóbulos -no un color opaco- lo que produce el blanco
            # del centro. Suben algo de opacidad cuando hay música.
            color.setAlpha(
                min(255, int(255 * (0.34 + 0.16 * self.nivel + 0.15 * self.empuje())))
            )
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
            painter.fillPath(self.mascara, tenir_hacia_acento(self.COLOR_BASE))

        painter.drawPixmap(0, 0, lienzo_difuminado)

        velo = velo_de_acento()
        if velo is not None and self.mascara is not None:
            painter.fillPath(self.mascara, velo)


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
        self.actualizar_estilo()

    def actualizar_estilo(self):
        """Se vuelve a llamar al cambiar el color de acento."""
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255,255,255,22);
                border-radius: {self.tamano // 2}px;
                border: none;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,45); }}
            QPushButton:pressed {{ background-color: {acento_css()}; }}
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        escala = self.devicePixelRatioF()
        icono = icono_tenido(
            ARCHIVO_ICONO_LISTA, round(self.tamano * 0.56),
            QColor(255, 255, 255, 235), escala,
        )
        if icono.isNull():
            # Sin el archivo, tres rayas dibujadas a mano. Es el mismo
            # símbolo de siempre y evita que el botón se quede vacío si
            # el icono no viaja dentro del paquete.
            pluma = QPen(QColor(255, 255, 255, 235), max(1.3, self.tamano * 0.06))
            pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pluma)

            w, h = self.width(), self.height()
            margen_x = w * 0.28
            centro_y = h / 2
            separacion = h * 0.15
            for y in (centro_y - separacion, centro_y, centro_y + separacion):
                painter.drawLine(QPointF(margen_x, y), QPointF(w - margen_x, y))
            return

        # El pixmap tiene más píxeles que su tamaño en pantalla (Retina),
        # así que hay que centrarlo por su medida lógica, no por la real.
        ancho = icono.width() / escala
        alto = icono.height() / escala
        painter.drawPixmap(
            QPointF((self.width() - ancho) / 2, (self.height() - alto) / 2), icono
        )


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

def dibujar_chevron(painter, x, y, lado, color, grosor=1.4, hacia="derecha"):
    """El '>' (o '<') de los menús. Se dibuja con dos trazos en vez de
    usar un carácter para que case con los demás iconos de la app, que
    también están dibujados a mano."""
    pluma = QPen(color, grosor)
    pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
    pluma.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pluma)
    painter.setBrush(Qt.BrushStyle.NoBrush)  # drawPath rellenaría el ángulo
    punta = lado * 0.72 if hacia == "derecha" else -lado * 0.72
    ruta = QPainterPath(QPointF(x - punta / 2, y - lado))
    ruta.lineTo(QPointF(x + punta / 2, y))
    ruta.lineTo(QPointF(x - punta / 2, y + lado))
    painter.drawPath(ruta)


# ------------------------------------------------------------------
# Las pantallas del panel superior (menú, ajustes) comparten cabecera:
# el título centrado, una línea finísima debajo y, si no estamos en la
# raíz, un chevron a la izquierda para volver. Es la cabecera de
# cualquier reproductor físico, y sirve para saber siempre dónde estás.
# ------------------------------------------------------------------

class CabeceraPantalla(QWidget):
    atras = pyqtSignal()
    accion = pyqtSignal()

    ALTO = 30
    ANCHO_ZONA_ATRAS = 44  # zona clicable del chevron, generosa a propósito
    ANCHO_ZONA_ACCION = 44

    def __init__(self, titulo, con_atras=False, accion_icono=None, reloj=False, parent=None):
        super().__init__(parent)
        self.titulo = titulo
        self.con_atras = con_atras
        # 'mas' para crear, 'papelera' para borrar. None: sin acción.
        self.accion_icono = accion_icono
        # Con reloj, el título se va a la izquierda y la hora ocupa el
        # centro, como la barra de estado de un teléfono.
        self.reloj = reloj
        self._hora = ""
        self._raton_en_atras = False
        self._raton_en_accion = False
        self.setFixedHeight(self.ALTO)
        if con_atras or accion_icono:
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if reloj:
            # Se comprueba cada segundo pero solo se repinta cuando el
            # texto cambia de verdad, o sea una vez por minuto. Y solo
            # mientras la cabecera está a la vista: en cuanto se cambia
            # de pantalla, el temporizador para.
            self._temporizador_reloj = QTimer(self)
            self._temporizador_reloj.setInterval(1000)
            self._temporizador_reloj.timeout.connect(self._actualizar_hora)
            self._actualizar_hora()

    def establecer_titulo(self, titulo):
        self.titulo = titulo
        self.update()

    def _actualizar_hora(self):
        # En el formato corto del sistema: en España "17:13" y donde se
        # use el reloj de doce, "5:13 p. m.".
        hora = QLocale.system().toString(
            QTime.currentTime(), QLocale.FormatType.ShortFormat
        )
        if hora != self._hora:
            self._hora = hora
            self.update()

    def showEvent(self, event):
        if self.reloj:
            self._actualizar_hora()
            self._temporizador_reloj.start()
        super().showEvent(event)

    def hideEvent(self, event):
        if self.reloj:
            self._temporizador_reloj.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        fuente = QFont(self.font())
        fuente.setPixelSize(11)
        fuente.setWeight(QFont.Weight.DemiBold)
        painter.setFont(fuente)
        painter.setPen(QColor(255, 255, 255, 220))
        if self.reloj:
            # A ras del texto de las filas de abajo, para que el nombre y
            # "Canciones" arranquen en la misma vertical.
            painter.drawText(
                QRectF(18, 0, self.width() - 36, self.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self.titulo,
            )
            painter.setPen(QColor(255, 255, 255, 200))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._hora)
        else:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.titulo)

        if self.con_atras:
            # Del color de acento, igual que el de la lista de canciones:
            # es la forma de volver y tiene que verse a la primera en
            # todas las pantallas, no solo en unas cuantas.
            color = (
                aclarar(COLOR_ACENTO, 0.25)
                if self._raton_en_atras else QColor(COLOR_ACENTO)
            )
            dibujar_chevron(painter, 18, self.height() / 2, 5, color, 1.8, hacia="izquierda")

        if self.accion_icono:
            cx, cy = self.width() - 19, self.height() / 2
            if self.accion_icono == "mas":
                # El '+' de crear va del color de acento: es la única
                # acción de la pantalla y así se ve a la primera. De paso
                # el color elegido en Ajustes asoma en un sitio más.
                color = (
                    aclarar(COLOR_ACENTO, 0.25)
                    if self._raton_en_accion else QColor(COLOR_ACENTO)
                )
                pluma = QPen(color, 1.8)
            elif self.accion_icono == "apagar":
                # Blanco en reposo y rojo al pasar por encima, como el
                # semáforo de macOS que antes cerraba la ventana: es la
                # única acción de la que no se vuelve, así que conviene
                # que se note antes de pulsarla, no después.
                color = (
                    QColor(255, 95, 86)
                    if self._raton_en_accion else QColor(255, 255, 255, 175)
                )
                pluma = QPen(color, 1.5)
            else:
                pluma = QPen(QColor(255, 255, 255, 255 if self._raton_en_accion else 175), 1.5)
            pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pluma)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self.accion_icono == "mas":
                painter.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
                painter.drawLine(QPointF(cx, cy - 5), QPointF(cx, cy + 5))
            elif self.accion_icono == "apagar":
                # El símbolo de encendido de toda la vida: un círculo
                # abierto por arriba y una raya vertical en el hueco.
                # Los ángulos van en dieciseisavos de grado y crecen en
                # sentido antihorario desde las tres en punto: de 125° a
                # 415° deja abierto justo el trozo de arriba.
                radio = 4.8
                painter.drawArc(
                    QRectF(cx - radio, cy - radio, radio * 2, radio * 2), 125 * 16, 290 * 16
                )
                painter.drawLine(QPointF(cx, cy - 6.6), QPointF(cx, cy - 1))
            else:  # papelera: tapa, cuerpo y dos rayas
                painter.drawLine(QPointF(cx - 5, cy - 3), QPointF(cx + 5, cy - 3))
                painter.drawLine(QPointF(cx - 2, cy - 5), QPointF(cx + 2, cy - 5))
                painter.drawLine(QPointF(cx - 3.5, cy - 3), QPointF(cx - 2.8, cy + 5))
                painter.drawLine(QPointF(cx + 3.5, cy - 3), QPointF(cx + 2.8, cy + 5))
                painter.drawLine(QPointF(cx - 2.8, cy + 5), QPointF(cx + 2.8, cy + 5))

        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        y = self.height() - 0.5
        painter.drawLine(QPointF(0, y), QPointF(self.width(), y))

    def mouseMoveEvent(self, event):
        x = event.position().x()
        en_atras = self.con_atras and x <= self.ANCHO_ZONA_ATRAS
        en_accion = bool(self.accion_icono) and x >= self.width() - self.ANCHO_ZONA_ACCION
        if (en_atras, en_accion) != (self._raton_en_atras, self._raton_en_accion):
            self._raton_en_atras, self._raton_en_accion = en_atras, en_accion
            self.update()

    def leaveEvent(self, event):
        if self._raton_en_atras or self._raton_en_accion:
            self._raton_en_atras = self._raton_en_accion = False
            self.update()

    def mousePressEvent(self, event):
        x = event.position().x()
        if self.con_atras and x <= self.ANCHO_ZONA_ATRAS:
            self.atras.emit()
        elif self.accion_icono and x >= self.width() - self.ANCHO_ZONA_ACCION:
            self.accion.emit()
        else:
            super().mousePressEvent(event)


class BotonAtras(QPushButton):
    """Chevron de volver suelto, para la lista de canciones: su cabecera
    ya es el buscador, así que no puede usar CabeceraPantalla.

    Va del color de acento, y no en blanco como los chevrones de las
    demás pantallas. Ahí el chevron está junto a un título y se entiende
    solo; aquí tiene al lado una píldora de búsqueda que se lleva toda la
    atención, y en blanco quien abre la app por primera vez ni lo ve.
    """

    def __init__(self, lado=22, parent=None):
        super().__init__(parent)
        self.setFixedSize(lado, lado)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = aclarar(COLOR_ACENTO, 0.25) if self.underMouse() else QColor(COLOR_ACENTO)
        dibujar_chevron(
            painter, self.width() / 2, self.height() / 2, 5, color, 1.8, hacia="izquierda",
        )

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


# ------------------------------------------------------------------
# Una entrada del menú: texto a la izquierda y chevron a la derecha.
# Al pasar el ratón se enciende con el color de acento, que es la forma
# más directa de enseñar de qué color va la app en ese momento.
# ------------------------------------------------------------------

class FilaMenu(QPushButton):
    ALTO = 28

    def __init__(self, texto, parent=None):
        super().__init__(parent)
        self.texto = texto
        self.setFixedHeight(self.ALTO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def establecer_texto(self, texto):
        self.texto = texto
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        encendida = self.underMouse() or self.isDown()
        if encendida:
            fondo = QColor(COLOR_ACENTO)
            fondo.setAlpha(255 if self.isDown() else 225)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fondo)
            painter.drawRoundedRect(
                QRectF(6, 2, self.width() - 12, self.height() - 4),
                RADIO_ELEMENTOS, RADIO_ELEMENTOS,
            )

        fuente = QFont(self.font())
        fuente.setPixelSize(12)
        fuente.setWeight(QFont.Weight.Medium)
        painter.setFont(fuente)
        painter.setPen(QColor(255, 255, 255, 245 if encendida else 220))
        painter.drawText(
            QRectF(18, 0, self.width() - 40, self.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.texto,
        )

        dibujar_chevron(
            painter, self.width() - 20, self.height() / 2, 4,
            QColor(255, 255, 255, 200 if encendida else 90),
        )

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


# ------------------------------------------------------------------
# Fila de ajuste con interruptor: texto a la izquierda y una palanca a
# la derecha que enseña de un vistazo si la opción está puesta o no.
# A diferencia de FilaMenu no lleva a ninguna parte, así que no se pinta
# de color al pasar por encima: el color de acento se reserva para la
# propia palanca cuando está encendida.
# ------------------------------------------------------------------

class FilaInterruptor(QPushButton):
    ALTO = 30
    ANCHO_PALANCA = 34
    ALTO_PALANCA = 18
    # Texto y palanca van juntos y centrados, no cada uno en una punta:
    # separados por todo el ancho de la fila parecían dos cosas sueltas
    # en vez de un ajuste y su interruptor.
    HUECO = 10

    def __init__(self, texto, parent=None):
        super().__init__(parent)
        self.texto = texto
        self.activo = False
        # Dónde está la palanca ahora mismo, de 0 (apagada) a 1. Es un
        # número aparte de 'activo' porque durante la animación la
        # opción ya está puesta pero la palanca aún va por el camino.
        self._avance = 0.0
        self.setFixedHeight(self.ALTO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(140)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._al_avanzar)

    def establecer_texto(self, texto):
        self.texto = texto
        self.update()

    def establecer_activo(self, activo, animar=True):
        self.activo = activo
        destino = 1.0 if activo else 0.0
        self.anim.stop()
        if animar and destino != self._avance:
            self.anim.setStartValue(self._avance)
            self.anim.setEndValue(destino)
            self.anim.start()
        else:
            self._avance = destino
            self.update()

    def _al_avanzar(self, valor):
        self._avance = float(valor)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        encendida = self.underMouse() or self.isDown()
        if encendida:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 26 if self.isDown() else 16))
            painter.drawRoundedRect(
                QRectF(6, 2, self.width() - 12, self.height() - 4),
                RADIO_ELEMENTOS, RADIO_ELEMENTOS,
            )

        fuente = QFont(self.font())
        fuente.setPixelSize(12)
        fuente.setWeight(QFont.Weight.Medium)
        painter.setFont(fuente)

        ancho_texto = QFontMetrics(fuente).horizontalAdvance(self.texto)
        izquierda = (self.width() - (ancho_texto + self.HUECO + self.ANCHO_PALANCA)) / 2

        painter.setPen(QColor(255, 255, 255, 245 if encendida else 220))
        painter.drawText(
            QRectF(izquierda, 0, ancho_texto, self.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.texto,
        )

        # --- La palanca ---
        x = izquierda + ancho_texto + self.HUECO
        y = (self.height() - self.ALTO_PALANCA) / 2
        apagado, acento = QColor(255, 255, 255, 40), QColor(COLOR_ACENTO)
        via = QColor(
            *[
                round(a + (b - a) * self._avance)
                for a, b in zip(
                    (apagado.red(), apagado.green(), apagado.blue(), apagado.alpha()),
                    (acento.red(), acento.green(), acento.blue(), 255),
                )
            ]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(via)
        painter.drawRoundedRect(
            QRectF(x, y, self.ANCHO_PALANCA, self.ALTO_PALANCA),
            self.ALTO_PALANCA / 2, self.ALTO_PALANCA / 2,
        )

        radio_bola = self.ALTO_PALANCA / 2 - 2
        recorrido = self.ANCHO_PALANCA - 2 * (radio_bola + 2)
        centro = QPointF(
            x + radio_bola + 2 + recorrido * self._avance, y + self.ALTO_PALANCA / 2
        )
        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawEllipse(centro, radio_bola, radio_bola)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


# ------------------------------------------------------------------
# Círculo de color de la pantalla de ajustes. El elegido lleva un aro
# blanco alrededor, separado del círculo, que es la forma habitual de
# marcar la selección sin tapar el propio color.
# ------------------------------------------------------------------

class MuestraColor(QPushButton):
    LADO = 52
    DIAMETRO = 40

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.elegido = False
        self.setFixedSize(self.LADO, self.LADO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def establecer_elegido(self, elegido):
        if elegido != self.elegido:
            self.elegido = elegido
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centro = QPointF(self.width() / 2, self.height() / 2)

        radio = self.DIAMETRO / 2
        if self.underMouse() and not self.elegido:
            radio += 1  # crece un pelo al pasar por encima
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(centro, radio, radio)

        if self.elegido:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 230), 1.8))
            aro = self.DIAMETRO / 2 + 4
            painter.drawEllipse(centro, aro, aro)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class LienzoTransicion(QWidget):
    """Tapa la ventana entera mientras esta cambia de tamaño y funde la
    tarjeta de antes con la de después.

    Sin esto, encoger o crecer la ventana obligaría a Qt a recolocar en
    cada fotograma una tarjeta llena de widgets de tamaño fijo, que es
    justo lo que no sabe hacer con gracia. Con dos fotos y un fundido,
    la ventana puede cambiar de tamaño libremente.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.antes = None
        self.despues = None
        self.avance = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def preparar(self, antes, despues):
        self.antes = antes
        self.despues = despues
        self.avance = 0.0

    def establecer_avance(self, valor):
        self.avance = float(valor)
        self.update()

    def soltar_fotos(self):
        self.antes = None
        self.despues = None

    def paintEvent(self, event):
        if self.antes is None or self.despues is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        destino = QRectF(self.rect())

        # El fundido va por delante del cambio de tamaño (termina sobre el
        # 70% del recorrido): así el último tramo del movimiento ya se ve
        # nítido, y no se llega al final con dos imágenes superpuestas.
        fundido = min(1.0, self.avance * 1.4)
        painter.setOpacity(1.0 - fundido)
        painter.drawPixmap(destino, self.antes, QRectF(self.antes.rect()))
        painter.setOpacity(fundido)
        painter.drawPixmap(destino, self.despues, QRectF(self.despues.rect()))


class DeslizadorPantallas(QWidget):
    """Anima el cambio de una pantalla del panel a otra.

    Trabaja con fotos: guarda una de la pantalla que se va y otra de la
    que llega, se coloca por encima y las desliza. El QStackedLayout de
    debajo ya ha cambiado de página desde el primer fotograma, así que si
    la animación se interrumpiera por lo que sea no se quedaría nada a
    medias: lo único que hay que hacer es esconder este widget.

    La que llega entra desde el borde y la que se va se aparta solo un
    tercio, que es el efecto de profundidad de iOS: da la sensación de
    que una pantalla se apila sobre la otra en vez de que las dos
    desfilen a la vez.
    """

    DURACION_MS = 260
    APARTADO = 0.32  # cuánto se desplaza la que se queda detrás
    OPACIDAD_MINIMA = 0.55

    def __init__(self, radio, color_fondo, parent=None):
        super().__init__(parent)
        self.radio = radio
        self.color_fondo = QColor(color_fondo)
        self.antes = None
        self.despues = None
        self.avance = 0.0
        self.hacia_dentro = True
        self.hide()

        self.animacion = QVariantAnimation(self)
        self.animacion.setDuration(self.DURACION_MS)
        self.animacion.setStartValue(0.0)
        self.animacion.setEndValue(1.0)
        self.animacion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animacion.valueChanged.connect(self._al_avanzar)
        self.animacion.finished.connect(self._al_terminar)

    def deslizar(self, antes, despues, hacia_dentro):
        self.antes = antes
        self.despues = despues
        self.hacia_dentro = hacia_dentro
        self.avance = 0.0
        padre = self.parentWidget()
        if padre is not None:
            self.setGeometry(padre.rect())
        self.show()
        self.raise_()
        self.animacion.stop()
        self.animacion.start()

    def _al_avanzar(self, valor):
        self.avance = float(valor)
        self.update()

    def _al_terminar(self):
        self.hide()
        # Las fotos pueden ocupar bastante en pantallas Retina; no tiene
        # sentido conservarlas hasta el siguiente cambio de pantalla.
        self.antes = None
        self.despues = None

    def paintEvent(self, event):
        if self.antes is None or self.despues is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        ruta = QPainterPath()
        ruta.addRoundedRect(QRectF(self.rect()), self.radio, self.radio)
        painter.setClipPath(ruta)
        # Debajo de todo, el color del panel: durante el deslizamiento
        # siempre hay una franja que no cubre ninguna de las dos fotos.
        painter.fillPath(ruta, self.color_fondo)

        ancho = self.width()
        signo = 1 if self.hacia_dentro else -1

        x_antes = -signo * ancho * self.APARTADO * self.avance
        painter.setOpacity(1.0 - (1.0 - self.OPACIDAD_MINIMA) * self.avance)
        painter.drawPixmap(QPointF(x_antes, 0), self.antes)

        x_despues = signo * ancho * (1.0 - self.avance)
        painter.setOpacity(1.0)
        painter.drawPixmap(QPointF(x_despues, 0), self.despues)


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

class BotonAnadirALista(QPushButton):
    """El '+' que aparece al pasar el ratón por una canción, para meterla
    en una lista. Es el mismo gesto que en MusicPi."""

    LADO = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.LADO, self.LADO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setToolTip(TEXTOS["elige_lista"])
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centro = QPointF(self.width() / 2, self.height() / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        # Opaco, no translúcido: va justo encima del hueco del ecualizador
        # y si dejara ver las barritas de la canción que suena quedaría un
        # revoltijo. El tono es el del panel, un punto más claro.
        painter.setBrush(QColor(72, 72, 80) if self.underMouse() else QColor(54, 54, 61))
        painter.drawEllipse(centro, self.LADO / 2, self.LADO / 2)

        pluma = QPen(QColor(255, 255, 255, 230), 1.4)
        pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pluma)
        brazo = self.LADO * 0.24
        painter.drawLine(
            QPointF(centro.x() - brazo, centro.y()), QPointF(centro.x() + brazo, centro.y())
        )
        painter.drawLine(
            QPointF(centro.x(), centro.y() - brazo), QPointF(centro.x(), centro.y() + brazo)
        )

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class FilaCancion(QWidget):
    anadir = pyqtSignal()

    def __init__(self, titulo, con_anadir=False, parent=None):
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

        # El '+' va por encima de la fila y fuera del layout: si entrara
        # en él movería el título de sitio, y la lista de canciones tiene
        # que seguir viéndose exactamente igual que siempre.
        #
        # Y va a la izquierda, encima del hueco del ecualizador, no a la
        # derecha: a la derecha se comía el final de los títulos largos,
        # que son justo los que peor se leen ya de por sí. Ese hueco está
        # reservado desde siempre y casi todo el tiempo está vacío.
        self.boton_anadir = None
        if con_anadir:
            self.boton_anadir = BotonAnadirALista(self)
            self.boton_anadir.clicked.connect(self.anadir.emit)
            self.boton_anadir.hide()

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        self._colocar_boton_anadir()

    def _colocar_boton_anadir(self):
        if self.boton_anadir is None:
            return
        hueco = self.indicador.geometry()
        self.boton_anadir.move(
            hueco.center().x() - self.boton_anadir.width() // 2 + 1,
            hueco.center().y() - self.boton_anadir.height() // 2,
        )

    def enterEvent(self, evento):
        if self.boton_anadir is not None:
            self._colocar_boton_anadir()
            self.boton_anadir.show()
            self.boton_anadir.raise_()
        super().enterEvent(evento)

    def leaveEvent(self, evento):
        if self.boton_anadir is not None:
            self.boton_anadir.hide()
        super().leaveEvent(evento)


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
        # Si hace de botón para elegir carpeta. Cuando no, el clic no
        # hace nada aquí y sube a la tarjeta, que es la que mueve la
        # ventana; y ni el cursor ni el brillo invitan a pulsar.
        self.clicable = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.fuente = QFont()
        self.fuente.setPointSize(13)
        self.fuente.setBold(True)

        self.OPACIDAD_REPOSO = 0.78
        self.efecto_opacidad = QGraphicsOpacityEffect(self)
        self.efecto_opacidad.setOpacity(self.OPACIDAD_REPOSO)
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

    def establecer_clicable(self, clicable):
        if clicable == self.clicable:
            return
        self.clicable = clicable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clicable else Qt.CursorShape.ArrowCursor
        )
        if not clicable:
            # Puede que el ratón esté encima ahora mismo: se apaga el brillo.
            self._animar_hacia(self.OPACIDAD_REPOSO)

    def enterEvent(self, event):
        if self.clicable:
            self._animar_hacia(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.clicable:
            self._animar_hacia(self.OPACIDAD_REPOSO)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self.clicable and event.button() == Qt.MouseButton.LeftButton:
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
        self._fondo_portada = None
        self._caratula = None
        # Con qué silueta se recorta la onda: la nota musical, o el rótulo
        # de la emisora mientras suena la radio.
        self.texto_dial = None
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
        self.efecto_desenfoque_glow.setBlurRadius(alto * 0.11)
        self._aplicar_silueta()

    def _aplicar_silueta(self):
        """Recalcula la forma que recorta la onda -y el halo que la
        rodea- para el tamaño actual. Es el único sitio que decide entre
        la nota y el rótulo de la emisora."""
        ancho, alto = self.width(), self.height()
        if self.texto_dial:
            silueta = construir_texto_silueta(self.texto_dial, ancho, alto)
        else:
            silueta = construir_nota_musical(ancho, alto)

        self.onda.setGeometry(0, 0, ancho, alto)
        self.onda.establecer_mascara(silueta)

        self.glow_nota.setGeometry(0, 0, ancho, alto)
        self.glow_nota.ruta_nota = silueta

        self._preparar_portada()
        self.update()

    def mostrar_dial(self, texto):
        """Modo radio: la onda pasa a vivir dentro del rótulo."""
        if texto == self.texto_dial:
            return
        self.texto_dial = texto
        self._aplicar_silueta()

    def mostrar_nota(self):
        if self.texto_dial is None:
            return
        self.texto_dial = None
        self._aplicar_silueta()

    def mostrar_portada(self, pixmap):
        self.pixmap_actual = pixmap
        self._preparar_portada()

    def limpiar_portada(self):
        self.pixmap_actual = None
        self._fondo_portada = None
        self._caratula = None

    def _preparar_portada(self):
        """Deja listas las dos piezas de la portada: el fondo ambiental
        (la propia imagen, ampliada y muy difuminada) y la caratula al
        tamano de la nota. Se calculan aqui y no al pintar porque
        difuminar en cada fotograma no tendria ningun sentido."""
        self._fondo_portada = None
        self._caratula = None
        if self.pixmap_actual is None or self.pixmap_actual.isNull():
            return

        ancho, alto = self.width(), self.height()
        if ancho <= 0 or alto <= 0:
            return

        # Fondo: la imagen recortada a la forma del panel y difuminada a
        # conciencia. Queda un degradado de sus propios colores, asi que
        # la pantalla siempre pega con la portada que este sonando.
        expandida = self.pixmap_actual.scaled(
            QSize(ancho, alto),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        recorte = expandida.copy(
            (expandida.width() - ancho) // 2, (expandida.height() - alto) // 2,
            ancho, alto,
        )
        self._fondo_portada = difuminar_pixmap(recorte, max(8.0, min(ancho, alto) * 0.22))

        # Caratula: a la resolucion real de la pantalla, que en un Retina
        # es el doble, o se veria blanda.
        escala = self.devicePixelRatioF()
        lado = round(min(ancho, alto) * PROPORCION_CARATULA)
        self._caratula = self.pixmap_actual.scaled(
            QSize(round(lado * escala), round(lado * escala)),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._caratula.setDevicePixelRatio(escala)

    def _pintar_portada(self, painter, ruta_redondeada):
        if self._fondo_portada is None or self._caratula is None:
            self._preparar_portada()
        if self._fondo_portada is None or self._caratula is None:
            painter.fillPath(ruta_redondeada, self.COLOR_FONDO_PANEL)
            return

        painter.drawPixmap(0, 0, self._fondo_portada)
        # Un velo oscuro por encima del fondo: sin el, una portada clara
        # deja el panel tan luminoso que el titulo y la caratula ya no
        # destacan sobre nada.
        painter.fillPath(ruta_redondeada, QColor(0, 0, 0, 95))

        lado = round(min(self.width(), self.height()) * PROPORCION_CARATULA)
        destino = QRectF(
            (self.width() - lado) / 2, (self.height() - lado) / 2, lado, lado
        )
        marco = QPainterPath()
        marco.addRoundedRect(destino, RADIO_CARATULA, RADIO_CARATULA)

        painter.save()
        painter.setClipPath(marco, Qt.ClipOperation.IntersectClip)
        painter.drawPixmap(destino, self._caratula, QRectF(self._caratula.rect()))
        painter.restore()

        # Filo tenue: separa la caratula del fondo cuando los dos tienen
        # un color parecido, que es lo normal al venir del mismo sitio.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        painter.drawPath(marco)

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
            self._pintar_portada(painter, ruta_redondeada)
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
# El juego escondido.
#
# No sale en ningún menú: se llega tocando los seis colores de Ajustes
# en orden inverso, del último hasta el azul de la marca. Tres carriles
# y tres botones -atrás, play y adelante-, que son justo los que tiene
# el reproductor debajo de la pantalla.
#
# La partitura no se inventa: sale de la curva de volumen que ya se
# calcula para que la nota lata con la música, así que el juego no añade
# ni un análisis más y cada canción se juega distinta. Sin curva -la
# radio, o música en pausa- cae un metrónomo de reserva.
# ------------------------------------------------------------------

class JuegoRitmo(QWidget):
    puntos_cambiados = pyqtSignal(int)
    record_batido = pyqtSignal(int)

    CARRILES = 3
    # Distancia de la línea de golpe al borde de abajo. Debajo de ella
    # van los tres símbolos que dicen qué botón es cada carril.
    MARGEN_LINEA = 40
    RADIO_NOTA = 8
    MS_CAIDA = 1500          # lo que tarda una nota en bajar hasta la línea
    MS_PERFECTO = 90
    MS_BIEN = 175
    VIDAS = 5
    MS_METRONOMO = 520       # el compás de reserva cuando no hay curva
    # Compases que se buscan al medir el tempo: de 60 a 200 pulsaciones
    # por minuto, que es donde cae prácticamente toda la música.
    MS_PULSO_MINIMO = 300
    MS_PULSO_MAXIMO = 1000
    # Tempo al que se parece más la música en general. No decide nada por
    # sí solo: solo desempata cuando la canción encaja igual de bien con
    # un compás y con su mitad o su doble, que es el error clásico de
    # medir el tempo a ciegas.
    MS_PULSO_TIPICO = 500
    # Por debajo de este volumen no se pone nota aunque toque pulso: son
    # los silencios de verdad, el principio y el final. Está bajo a
    # propósito: con 0.18 una intro suave de treinta segundos -las hay-
    # se quedaba sin una sola nota y el juego parecía roto.
    UMBRAL_SILENCIO = 0.06
    # Si el compás sale más rápido que esto, se pone una nota de cada
    # dos: a tres botones, más de dos notas por segundo no se juega.
    MS_PULSO_MINIMO_JUGABLE = 420

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.envolvente = []
        self.resolucion_ms = 50
        self.notas = []
        self.estado = "espera"   # espera | jugando | fin
        self.puntos = 0
        self.combo = 0
        self.mejor_combo = 0
        self.vidas = self.VIDAS
        self.record = 0
        self.tiempo_ms = 0
        self.con_musica = False
        # Destellos de los aciertos: (carril, avance de 0 a 1, perfecto).
        self.destellos = []
        self._pulso = 0.0

        self.temporizador = QTimer(self)
        self.temporizador.setInterval(16)   # ~60 fotogramas por segundo
        self.temporizador.timeout.connect(self._avanzar)
        # El reloj del juego se lleva con el tiempo real que ha pasado
        # entre fotograma y fotograma, no sumando los 16 ms de rigor: un
        # temporizador de Qt llega tarde a menudo, y contar fotogramas
        # como si fueran exactos desviaba la partitura de la canción.
        self._reloj = QElapsedTimer()

    # ---------------- Partitura ----------------

    def establecer_partitura(self, envolvente, resolucion_ms, posicion_ms=0):
        """La curva de la canción que suena. Si no hay, metrónomo."""
        self.envolvente = list(envolvente or [])
        self.resolucion_ms = max(1, resolucion_ms)
        self.con_musica = bool(self.envolvente)
        self.tiempo_ms = posicion_ms if self.con_musica else 0
        self.notas = self._construir_notas()
        if self.estado == "jugando":
            # Canción nueva a media partida: se sigue jugando con la
            # partitura nueva, pero las notas de la anterior ya no valen.
            self._descartar_pasadas()
        self.update()

    def _ataques(self):
        """Cuánto sube el volumen en cada paso.

        Los golpes están en las subidas, no en los momentos altos: un
        estribillo entero suena fuerte de principio a fin y ahí no hay
        nada que pulsar, mientras que la entrada de la batería es una
        subida de golpe aunque no sea el pico más alto de la canción.
        """
        envolvente = self.envolvente
        return [0.0] + [
            max(0.0, envolvente[i] - envolvente[i - 1])
            for i in range(1, len(envolvente))
        ]

    def _compas(self, ataques):
        """Cada cuántos pasos se repiten los ataques: el tempo.

        Se mide comparando la canción consigo misma desplazada. El
        desplazamiento en el que más coinciden los golpes es el compás.
        """
        minimo = max(1, round(self.MS_PULSO_MINIMO / self.resolucion_ms))
        maximo = max(minimo + 1, round(self.MS_PULSO_MAXIMO / self.resolucion_ms))
        if len(ataques) <= maximo + 1:
            return None

        mejor, mejor_valor = None, 0.0
        for salto in range(minimo, maximo + 1):
            suma = sum(
                ataques[i] * ataques[i - salto] for i in range(salto, len(ataques))
            ) / (len(ataques) - salto)
            # Una canción encaja igual de bien con su compás que con la
            # mitad o el doble. Este peso inclina el empate hacia el
            # tempo de andar por casa y evita partituras al doble de
            # velocidad de lo que suena.
            ms = salto * self.resolucion_ms
            suma *= 1 / (1 + abs(math.log(ms / self.MS_PULSO_TIPICO)) * 1.6)
            if suma > mejor_valor:
                mejor, mejor_valor = salto, suma
        return mejor

    def _seguir_el_compas(self, ataques, compas):
        """Un pulso cada 'compas' pasos, pegado al golpe real más cercano.

        Ir de golpe en golpe a secas daba huecos de 450 a 800 ms: notas
        sueltas que nadie lee como un ritmo. Y un metrónomo perfecto se
        despegaría de la canción a los pocos compases. Esto es lo de en
        medio: el compás manda, pero cada pulso se engancha al ataque de
        verdad que tenga al lado, así que no se va y suena a música.
        """
        envolvente = self.envolvente
        margen = max(1, round(compas * 0.16))

        def vecino_mas_fuerte(centro):
            desde, hasta = max(0, centro - margen), min(len(ataques), centro + margen + 1)
            if desde >= hasta:
                return None
            mejor = max(range(desde, hasta), key=lambda k: ataques[k])
            return mejor if ataques[mejor] > 0 else centro

        # El ancla es el golpe más fuerte de toda la canción, y desde ahí
        # se va hacia delante y hacia atrás.
        ancla = max(range(len(ataques)), key=lambda i: ataques[i])
        pasos = set()
        for sentido in (1, -1):
            indice = ancla
            while 0 <= indice < len(envolvente):
                if envolvente[indice] >= self.UMBRAL_SILENCIO:
                    pasos.add(indice)
                siguiente = vecino_mas_fuerte(indice + sentido * compas)
                if siguiente is None or siguiente == indice:
                    break
                indice = siguiente
        return sorted(p * self.resolucion_ms for p in pasos)

    def _construir_notas(self):
        """La partitura: un pulso por compás, repartido en tres carriles."""
        tiempos = []
        if self.con_musica:
            ataques = self._ataques()
            compas = self._compas(ataques)
            if compas:
                # A más de dos notas por segundo no hay manos: en ese
                # caso se va a contratiempo, una de cada dos.
                if compas * self.resolucion_ms < self.MS_PULSO_MINIMO_JUGABLE:
                    compas *= 2
                tiempos = self._seguir_el_compas(ataques, compas)
        if not tiempos:
            self.con_musica = False
            tiempos = [i * self.MS_METRONOMO for i in range(1, 4000)]

        # El carril se sortea con una cuenta fija y no con random: la
        # misma canción da siempre la misma partitura, que es la mitad
        # de la gracia de intentar mejorar la marca.
        notas, semilla, anterior = [], 7, -1
        for tiempo in tiempos:
            semilla = (semilla * 1103515245 + 12345) & 0x7FFFFFFF
            carril = semilla % self.CARRILES
            if carril == anterior and len(tiempos) > 1:
                carril = (carril + 1) % self.CARRILES
            anterior = carril
            notas.append({"ms": tiempo, "carril": carril, "estado": "pendiente"})
        return notas

    def _descartar_pasadas(self):
        for nota in self.notas:
            if nota["ms"] < self.tiempo_ms - self.MS_BIEN:
                nota["estado"] = "fallada"

    # ---------------- Partida ----------------

    def preparar(self, record):
        """Al entrar en la pantalla: todo a cero y a esperar el play."""
        self.record = record
        self.estado = "espera"
        self.puntos = 0
        self.combo = 0
        self.mejor_combo = 0
        self.vidas = self.VIDAS
        self.destellos = []
        self.puntos_cambiados.emit(0)
        self.update()

    def showEvent(self, event):
        # El juego solo corre mientras se ve. Si se sale a la nota o a
        # cualquier otra pantalla, se queda como estaba hasta la vuelta.
        self._reloj.start()
        self.temporizador.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.temporizador.stop()
        super().hideEvent(event)

    def empezar(self):
        for nota in self.notas:
            nota["estado"] = "pendiente"
        self.puntos = 0
        self.combo = 0
        self.mejor_combo = 0
        self.vidas = self.VIDAS
        self.destellos = []
        self.estado = "jugando"
        # Las notas que ya han pasado no cuentan: se empieza en el punto
        # por el que va la canción, no desde el principio del archivo.
        self._descartar_pasadas()
        self.puntos_cambiados.emit(0)

    # Lo que tarda en oírse lo que el reproductor ya da por sonado: entre
    # que suelta el audio y sale por los altavoces hay una cola de por
    # medio. Sin descontarlo, las notas llegan a la línea un pelín antes
    # que el golpe, que es justo lo que hace que no parezca que va al
    # ritmo. Si alguna vez se nota adelantado o atrasado, es este número.
    MS_RETARDO_AUDIO = 80

    def sincronizar(self, posicion_ms):
        """El minuto real de la canción. Entre aviso y aviso el reloj lo
        lleva la animación, o el juego iría a tirones."""
        if not self.con_musica:
            return
        posicion_ms -= self.MS_RETARDO_AUDIO
        # Antes se daba por bueno hasta un desfase de 120 ms, que es
        # justo el tamaño de la ventana de acierto: se podía jugar toda
        # una canción con la partitura corrida y sin saber por qué.
        if abs(posicion_ms - self.tiempo_ms) > 45:
            self.tiempo_ms = posicion_ms

    def pulsar(self, carril):
        """Uno de los tres botones del reproductor."""
        if self.estado != "jugando":
            if self.estado == "fin" and carril != 1:
                return   # solo el play reinicia, para no volver sin querer
            self.empezar()
            return

        mejor, mejor_error = None, None
        for nota in self.notas:
            if nota["estado"] != "pendiente" or nota["carril"] != carril:
                continue
            error = abs(nota["ms"] - self.tiempo_ms)
            if error > self.MS_BIEN:
                continue
            if mejor_error is None or error < mejor_error:
                mejor, mejor_error = nota, error

        if mejor is None:
            # Botón al aire: rompe la racha pero no quita vida. Fallar
            # por pulsar de más ya escuece bastante.
            self.combo = 0
            self.update()
            return

        perfecto = mejor_error <= self.MS_PERFECTO
        mejor["estado"] = "acertada"
        self.combo += 1
        self.mejor_combo = max(self.mejor_combo, self.combo)
        # La racha multiplica hasta cinco veces: es lo que engancha.
        multiplicador = 1 + min(self.combo // 8, 4)
        self.puntos += (100 if perfecto else 50) * multiplicador
        self.destellos.append([carril, 0.0, perfecto])
        self.puntos_cambiados.emit(self.puntos)
        self.update()

    def _avanzar(self):
        transcurrido = self._reloj.restart() if self._reloj.isValid() else 16
        self._pulso += transcurrido / 1000
        if self.estado == "jugando":
            self.tiempo_ms += transcurrido
            for nota in self.notas:
                if nota["estado"] != "pendiente":
                    continue
                if self.tiempo_ms - nota["ms"] > self.MS_BIEN:
                    nota["estado"] = "fallada"
                    self.combo = 0
                    self.vidas -= 1
                    if self.vidas <= 0:
                        self._terminar()
                        break
            if self.estado == "jugando" and self.con_musica and not any(
                n["estado"] == "pendiente" for n in self.notas
            ):
                self._terminar()   # se acabó la canción: se acabó la partida

        for destello in self.destellos:
            destello[1] += 0.09
        self.destellos = [d for d in self.destellos if d[1] < 1.0]
        self.update()

    def _terminar(self):
        self.estado = "fin"
        if self.puntos > self.record:
            self.record = self.puntos
            # Se avisa en el momento para que quede guardado aunque se
            # salga de la pantalla por cualquier otro sitio.
            self.record_batido.emit(self.record)

    # ---------------- Pintura ----------------

    def _y_linea(self):
        return self.height() - self.MARGEN_LINEA

    def _centro_carril(self, carril):
        return self.width() * (carril + 0.5) / self.CARRILES

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        y_linea = self._y_linea()
        acento = QColor(COLOR_ACENTO)

        # Separadores de carril, apenas visibles: sitúan sin estorbar.
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for i in range(1, self.CARRILES):
            x = self.width() * i / self.CARRILES
            painter.drawLine(QPointF(x, 6), QPointF(x, y_linea))

        # La línea de golpe.
        linea = QColor(acento)
        linea.setAlpha(110)
        painter.setPen(QPen(linea, 1.4))
        painter.drawLine(QPointF(4, y_linea), QPointF(self.width() - 4, y_linea))

        self._pintar_dianas(painter, y_linea, acento)
        self._pintar_notas(painter, y_linea, acento)
        self._pintar_destellos(painter, y_linea, acento)
        self._pintar_simbolos(painter, y_linea)
        self._pintar_marcador(painter, y_linea, acento)

    def _pintar_dianas(self, painter, y_linea, acento):
        for carril in range(self.CARRILES):
            centro = QPointF(self._centro_carril(carril), y_linea)
            aro = QColor(acento)
            aro.setAlpha(150)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(aro, 1.4))
            painter.drawEllipse(centro, self.RADIO_NOTA + 2, self.RADIO_NOTA + 2)

    def _pintar_notas(self, painter, y_linea, acento):
        if self.estado != "jugando":
            # Antes de empezar y al terminar, la pantalla se queda limpia
            # para el récord y el marcador.
            return
        painter.setPen(Qt.PenStyle.NoPen)
        for nota in self.notas:
            if nota["estado"] != "pendiente":
                continue
            restante = nota["ms"] - self.tiempo_ms
            avance = 1 - restante / self.MS_CAIDA
            if avance < -0.05 or avance > 1.2:
                continue
            y = y_linea * avance
            color = QColor(acento)
            # Las de lejos llegan apagadas y se encienden al acercarse:
            # así se ve de un vistazo cuál toca ya.
            color.setAlpha(int(90 + 165 * max(0.0, min(1.0, avance))))
            painter.setBrush(color)
            painter.drawEllipse(
                QPointF(self._centro_carril(nota["carril"]), y),
                self.RADIO_NOTA, self.RADIO_NOTA,
            )

    def _pintar_destellos(self, painter, y_linea, acento):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for carril, avance, perfecto in self.destellos:
            color = QColor(255, 255, 255) if perfecto else QColor(acento)
            color.setAlpha(int(200 * (1 - avance)))
            painter.setPen(QPen(color, 2.0 * (1 - avance) + 0.6))
            radio = self.RADIO_NOTA + 2 + 14 * avance
            painter.drawEllipse(
                QPointF(self._centro_carril(carril), y_linea), radio, radio
            )

    def _pintar_simbolos(self, painter, y_linea):
        """Debajo de la línea, los mismos símbolos que tienen los botones
        del reproductor: es todo el manual de instrucciones que hay."""
        y = (y_linea + self.height()) / 2 + 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 120))
        for carril, simbolo in enumerate(("anterior", "play", "siguiente")):
            cx = self._centro_carril(carril)
            if simbolo == "play":
                BotonControlDibujado._triangulo(painter, cx + 0.8, y, 9, True)
                continue
            hacia_derecha = simbolo == "siguiente"
            lado, grosor = 8, 2.0
            ancho = lado * 0.9 + grosor + 1
            inicio = cx - ancho / 2
            if hacia_derecha:
                BotonControlDibujado._triangulo(
                    painter, inicio + lado * 0.45, y, lado, True
                )
                x_barra = inicio + ancho - grosor
            else:
                x_barra = inicio
                BotonControlDibujado._triangulo(
                    painter, inicio + ancho - lado * 0.45, y, lado, False
                )
            painter.drawRoundedRect(
                QRectF(x_barra, y - lado / 2, grosor, lado), 0.8, 0.8
            )

    def _pintar_marcador(self, painter, y_linea, acento):
        # Vidas: tres puntitos arriba a la izquierda que se van apagando.
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(self.VIDAS):
            vivo = i < self.vidas
            painter.setBrush(
                QColor(acento) if vivo else QColor(255, 255, 255, 40)
            )
            painter.drawEllipse(QPointF(12 + i * 11, 12), 3.2, 3.2)

        fuente = QFont(self.font())
        if self.estado == "jugando":
            if self.combo >= 4:
                fuente.setPixelSize(20)
                fuente.setWeight(QFont.Weight.Bold)
                painter.setFont(fuente)
                painter.setPen(QColor(255, 255, 255, 45))
                painter.drawText(
                    QRectF(0, y_linea / 2 - 16, self.width(), 32),
                    Qt.AlignmentFlag.AlignCenter,
                    f"×{self.combo}",
                )
            return

        # En espera y al terminar: el récord, y el play como invitación.
        fuente.setPixelSize(10)
        fuente.setWeight(QFont.Weight.DemiBold)
        painter.setFont(fuente)
        painter.setPen(QColor(255, 255, 255, 150))
        painter.drawText(
            QRectF(0, y_linea / 2 - 26, self.width(), 14),
            Qt.AlignmentFlag.AlignCenter,
            "{} {}".format(TEXTOS["record"], QLocale.system().toString(self.record)),
        )
        if self.estado == "fin":
            fuente.setPixelSize(26)
            fuente.setWeight(QFont.Weight.Bold)
            painter.setFont(fuente)
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(
                QRectF(0, y_linea / 2 - 10, self.width(), 32),
                Qt.AlignmentFlag.AlignCenter,
                QLocale.system().toString(self.puntos),
            )
        else:
            # Un play que respira, para que se entienda que hay que
            # pulsarlo para empezar.
            latido = 0.5 + 0.5 * math.sin(self._pulso * 3.4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(70 + 110 * latido)))
            BotonControlDibujado._triangulo(
                painter, self.width() / 2 + 1.5, y_linea / 2 + 14, 22, True
            )


# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------

ANCHO_TARJETA = 300
# La tarjeta se ajusta a lo que ocupa su contenido: si sobra alto, Qt lo
# reparte entre las filas y aparecen franjas vacías en vez de un diseño
# compacto. Si añades o quitas filas, recalcula este número.
ALTO_TARJETA = 348

# Alto de la fila con el minuto actual y la duración. Si se cambia, hay
# que mover ALTO_TARJETA en la misma cantidad: las filas suman justo el
# alto de la tarjeta, así que un píxel que se quite aquí y no se quite
# allí reaparece como hueco en otra fila.
ALTO_FILA_TIEMPOS = 16
# Margen superior del layout. Vale lo mismo que los laterales para que el
# panel quede centrado dentro de la tarjeta: antes aquí había una fila
# con el botón de cerrar (el semáforo en macOS, la "×" en Windows) y el
# margen podía ser más justo porque esa fila ya hacía de aire.
MARGEN_SUPERIOR_TARJETA = 16
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

# La caratula se dibuja del mismo tamano que la nota musical (esa misma
# proporcion esta en construir_nota_musical) y no llenando el panel. Dos
# razones: una portada incrustada en un MP3 suele venir en mala calidad y
# estirada a 268 px se le ven los dientes, y ademas asi la pantalla se ve
# igual de compuesta haya portada o no.
PROPORCION_CARATULA = 0.62
RADIO_CARATULA = 10
ALTURA_DESVANECIDO = 18

# Azul de acento: el mismo tono principal de la onda/nota (#4F8CFF), usado
# también como color activo de los botones y del play, para que todo el
# "estado pulsado/activo" de la interfaz hable el mismo idioma visual.
#
# Los otros cinco son los de MusicPi (PROFILE_COLORS en su app.js), para
# que las dos aplicaciones ofrezcan exactamente la misma paleta. El azul
# sí es el de Digilogic y no el de MusicPi: es el color de la marca, está
# en la web y en el icono, y sigue siendo el de fábrica.
COLORES_ACENTO = [
    QColor(79, 140, 255),   # #4F8CFF azul Digilogic (por defecto)
    QColor(160, 107, 239),  # #A06BEF morado
    QColor(239, 107, 160),  # #EF6BA0 rosa
    QColor(79, 209, 160),   # #4FD1A0 verde
    QColor(255, 179, 92),   # #FFB35C ámbar
    QColor(239, 107, 107),  # #EF6B6B rojo
]
COLOR_ACENTO_POR_DEFECTO = COLORES_ACENTO[0]

# Este objeto se modifica en el sitio (setRgb) y nunca se reasigna: hay
# código que se queda con una referencia a él -entre otros el módulo
# privado de descargas, que lo recibe al construirse- y así todos ven el
# color nuevo sin tener que volver a pedirlo.
COLOR_ACENTO = QColor(COLOR_ACENTO_POR_DEFECTO)


def acento_css():
    """El acento como 'rgb(...)' para las hojas de estilo. Es una función
    y no una constante justamente porque el color puede cambiar mientras
    la app está abierta."""
    return f"rgb({COLOR_ACENTO.red()}, {COLOR_ACENTO.green()}, {COLOR_ACENTO.blue()})"


# Aclarar/oscurecer con las mismas proporciones que usa MusicPi para
# derivar sus tonos de hover y pulsado a partir del color elegido.
def aclarar(color, cantidad=0.2):
    return QColor(
        round(color.red() + (255 - color.red()) * cantidad),
        round(color.green() + (255 - color.green()) * cantidad),
        round(color.blue() + (255 - color.blue()) * cantidad),
    )


def oscurecer(color, cantidad=0.22):
    return QColor(
        round(color.red() * (1 - cantidad)),
        round(color.green() * (1 - cantidad)),
        round(color.blue() * (1 - cantidad)),
    )


def css(color):
    return f"rgb({color.red()}, {color.green()}, {color.blue()})"


# Cuánto arrastra el acento a los colores de la nota. MusicPi pinta su
# nota entera con el color elegido, pero aquí los lóbulos son la seña de
# identidad de la app -el orbe de Siri- y repintarlos del todo sería
# perderla. En vez de eso se les gira el matiz una fracción de lo que se
# ha movido el acento respecto al azul de fábrica: el orbe "se contagia"
# del color sin dejar de ser el mismo. Con el azul de siempre el giro es
# cero y la nota queda exactamente igual que antes.
TENIDO_NOTA = 0.25

# Encima de eso, un velo del color elegido sobre la nota entera. El giro
# de matiz solo la desplaza; el velo es lo que hace que se reconozca "el
# verde" o "el ámbar" de un vistazo. Con el azul de fábrica no hay velo.
VELO_NOTA = 30


def velo_de_acento():
    if VELO_NOTA <= 0 or COLOR_ACENTO.rgb() == COLOR_ACENTO_POR_DEFECTO.rgb():
        return None
    color = QColor(COLOR_ACENTO)
    color.setAlpha(VELO_NOTA)
    return color


def tenir_hacia_acento(color, fraccion=TENIDO_NOTA):
    matiz_acento = COLOR_ACENTO.hue()
    matiz_fabrica = COLOR_ACENTO_POR_DEFECTO.hue()
    if matiz_acento == matiz_fabrica:
        return QColor(color)

    h, s, v, a = color.getHsv()
    if h < 0:
        return QColor(color)  # un gris no tiene matiz que girar

    # Por el camino corto: del azul al rojo se va hacia atrás, no dando
    # toda la vuelta a la rueda de color.
    giro = (matiz_acento - matiz_fabrica + 540) % 360 - 180
    return QColor.fromHsv(round(h + giro * fraccion) % 360, s, v, a)

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
# El tope de tamano de un widget en Qt (QWIDGETSIZE_MAX). Hace falta para
# soltarle el tamano fijo a la ventana mientras dura la animacion.
LADO_MAXIMO_VENTANA = 16_777_215
# Lo que tarda la ventana en encoger o crecer al entrar y salir del modo
# compacto. Corto a proposito: es un gesto que se repite mucho, y una
# animacion que se hace notar acaba estorbando.
DURACION_CAMBIO_TAMANO_MS = 210

# Si al pulsar "anterior" la canción lleva sonando más de este tiempo, no
# se salta a la canción previa: se rebobina la actual al principio. Es el
# comportamiento habitual en reproductores (Spotify, Apple Music) y evita
# perder la canción de golpe por un toque de más.
UMBRAL_REBOBINAR_MS = 10_000

# Textos del campo de búsqueda. Cambian con el modo porque son la pista
# principal de qué va a pasar al escribir: filtrar lo que ya tienes o
# descargar algo nuevo.
# ------------------------------------------------------------------
# Idioma de la interfaz
# ------------------------------------------------------------------
#
# Digilogic tiene cuatro frases y ningún menú de idiomas: coge el del
# sistema al arrancar y se acabó. Un selector de idioma sería un control
# más en una ventana que se quiere mínima, y además nadie lo usaría —
# quien tiene el Mac en alemán quiere la app en alemán, no quiere
# elegirla.
#
# Si el sistema está en un idioma que no está aquí, se usa el inglés,
# que es el que más gente entiende.
#
# No hay árabe ni hebreo a propósito: se escriben de derecha a izquierda
# y eso obliga a dar la vuelta a toda la interfaz, que está dibujada a
# mano. Traducir las frases sin girar el diseño quedaría peor que
# dejarlo en inglés.

IDIOMAS = {
    "en": {
        "elige_carpeta": "Choose a folder",
        "buscar": "Search your music…",
        "dialogo_carpeta": "Choose the folder with your music (it can be a USB drive)",
        "sin_musica": "No music found",
        "canciones": "Songs",
        "ajustes": "Settings",
        "radio": "Radio",
        "buscar_emisoras": "Search stations…",
        "en_directo": "LIVE",
        "sin_emisoras": "No stations found",
        "sin_conexion": "No connection",
        "radio_no_disponible": "Radio unavailable on this system",
        "estadisticas": "Statistics",
        "tiempo_escuchado": "Time listened",
        "canciones_reproducidas": "Songs played",
        "cancion_mas_escuchada": "Most played song",
        "tiempo_radio": "Time on radio",
        "emisora_mas_escuchada": "Most played station",
        "escuchando_desde": "Listening since",
        "media_diaria": "Daily average",
        "unidad_horas": "h",
        "unidad_minutos": "min",
        "veces": "times",
        "listas": "Playlists",
        "todas": "All songs",
        "nueva_lista": "New playlist",
        "sin_listas": "No playlists yet",
        "elige_lista": "Add to…",
        "borrar_lista": "Delete playlist",
        "siempre_visible": "Always on top",
        "salir": "Quit",
        "record": "Best",
    },
    "es": {
        "elige_carpeta": "Selecciona una carpeta",
        "buscar": "Buscar en tu música…",
        "dialogo_carpeta": "Selecciona la carpeta con tu música (puede ser tu USB)",
        "sin_musica": "No se encontró música",
        "canciones": "Canciones",
        "ajustes": "Ajustes",
        "radio": "Radio",
        "buscar_emisoras": "Buscar emisoras…",
        "en_directo": "EN DIRECTO",
        "sin_emisoras": "No se encontraron emisoras",
        "sin_conexion": "Sin conexión",
        "radio_no_disponible": "Radio no disponible en este equipo",
        "estadisticas": "Estadísticas",
        "tiempo_escuchado": "Tiempo escuchado",
        "canciones_reproducidas": "Canciones reproducidas",
        "cancion_mas_escuchada": "Canción más escuchada",
        "tiempo_radio": "Tiempo de radio",
        "emisora_mas_escuchada": "Emisora más escuchada",
        "escuchando_desde": "Escuchando desde",
        "media_diaria": "Media diaria",
        "unidad_horas": "h",
        "unidad_minutos": "min",
        "veces": "veces",
        "listas": "Listas",
        "todas": "Todas",
        "nueva_lista": "Nueva lista",
        "sin_listas": "Aún no tienes listas",
        "elige_lista": "Añadir a…",
        "borrar_lista": "Borrar lista",
        "siempre_visible": "Siempre visible",
        "salir": "Salir",
        "record": "Récord",
    },
    "pt": {
        "elige_carpeta": "Escolhe uma pasta",
        "buscar": "Procurar na tua música…",
        "dialogo_carpeta": "Escolhe a pasta com a tua música (pode ser uma unidade USB)",
        "sin_musica": "Nenhuma música encontrada",
        "canciones": "Músicas",
        "ajustes": "Ajustes",
        "radio": "Rádio",
        "buscar_emisoras": "Procurar estações…",
        "en_directo": "EM DIRETO",
        "sin_emisoras": "Nenhuma estação encontrada",
        "sin_conexion": "Sem ligação",
        "radio_no_disponible": "Rádio indisponível neste equipamento",
        "estadisticas": "Estatísticas",
        "tiempo_escuchado": "Tempo ouvido",
        "canciones_reproducidas": "Músicas reproduzidas",
        "cancion_mas_escuchada": "Música mais ouvida",
        "tiempo_radio": "Tempo de rádio",
        "emisora_mas_escuchada": "Estação mais ouvida",
        "escuchando_desde": "A ouvir desde",
        "media_diaria": "Média diária",
        "unidad_horas": "h",
        "unidad_minutos": "min",
        "veces": "vezes",
        "listas": "Listas",
        "todas": "Todas",
        "nueva_lista": "Nova lista",
        "sin_listas": "Ainda não tens listas",
        "elige_lista": "Adicionar a…",
        "borrar_lista": "Apagar lista",
        "siempre_visible": "Sempre visível",
        "salir": "Sair",
        "record": "Recorde",
    },
    "fr": {
        "elige_carpeta": "Choisir un dossier",
        "buscar": "Rechercher dans votre musique…",
        "dialogo_carpeta": "Choisissez le dossier contenant votre musique (ce peut être une clé USB)",
        "sin_musica": "Aucune musique trouvée",
        "canciones": "Morceaux",
        "ajustes": "Réglages",
        "radio": "Radio",
        "buscar_emisoras": "Rechercher des stations…",
        "en_directo": "EN DIRECT",
        "sin_emisoras": "Aucune station trouvée",
        "sin_conexion": "Pas de connexion",
        "radio_no_disponible": "Radio indisponible sur cet appareil",
        "estadisticas": "Statistiques",
        "tiempo_escuchado": "Temps d'écoute",
        "canciones_reproducidas": "Morceaux écoutés",
        "cancion_mas_escuchada": "Morceau le plus écouté",
        "tiempo_radio": "Temps de radio",
        "emisora_mas_escuchada": "Station la plus écoutée",
        "escuchando_desde": "À l'écoute depuis",
        "media_diaria": "Moyenne quotidienne",
        "unidad_horas": "h",
        "unidad_minutos": "min",
        "veces": "fois",
        "listas": "Listes",
        "todas": "Tout",
        "nueva_lista": "Nouvelle liste",
        "sin_listas": "Aucune liste",
        "elige_lista": "Ajouter à…",
        "borrar_lista": "Supprimer la liste",
        "siempre_visible": "Toujours visible",
        "salir": "Quitter",
        "record": "Record",
    },
    "de": {
        "elige_carpeta": "Ordner auswählen",
        "buscar": "Deine Musik durchsuchen…",
        "dialogo_carpeta": "Wähle den Ordner mit deiner Musik (auch ein USB-Stick)",
        "sin_musica": "Keine Musik gefunden",
        "canciones": "Titel",
        "ajustes": "Einstellungen",
        "radio": "Radio",
        "buscar_emisoras": "Sender suchen…",
        "en_directo": "LIVE",
        "sin_emisoras": "Keine Sender gefunden",
        "sin_conexion": "Keine Verbindung",
        "radio_no_disponible": "Radio auf diesem Gerät nicht verfügbar",
        "estadisticas": "Statistiken",
        "tiempo_escuchado": "Hörzeit",
        "canciones_reproducidas": "Gespielte Titel",
        "cancion_mas_escuchada": "Meistgespielter Titel",
        "tiempo_radio": "Radiozeit",
        "emisora_mas_escuchada": "Meistgehörter Sender",
        "escuchando_desde": "Dabei seit",
        "media_diaria": "Tagesdurchschnitt",
        "unidad_horas": "Std.",
        "unidad_minutos": "Min.",
        "veces": "mal",
        "listas": "Listen",
        "todas": "Alle",
        "nueva_lista": "Neue Liste",
        "sin_listas": "Noch keine Listen",
        "elige_lista": "Hinzufügen zu…",
        "borrar_lista": "Liste löschen",
        "siempre_visible": "Immer im Vordergrund",
        "salir": "Beenden",
        "record": "Bestwert",
    },
    "it": {
        "elige_carpeta": "Scegli una cartella",
        "buscar": "Cerca nella tua musica…",
        "dialogo_carpeta": "Scegli la cartella con la tua musica (può essere una chiavetta USB)",
        "sin_musica": "Nessuna musica trovata",
        "canciones": "Brani",
        "ajustes": "Impostazioni",
        "radio": "Radio",
        "buscar_emisoras": "Cerca stazioni…",
        "en_directo": "IN DIRETTA",
        "sin_emisoras": "Nessuna stazione trovata",
        "sin_conexion": "Nessuna connessione",
        "radio_no_disponible": "Radio non disponibile su questo dispositivo",
        "estadisticas": "Statistiche",
        "tiempo_escuchado": "Tempo di ascolto",
        "canciones_reproducidas": "Brani riprodotti",
        "cancion_mas_escuchada": "Brano più ascoltato",
        "tiempo_radio": "Tempo in radio",
        "emisora_mas_escuchada": "Stazione più ascoltata",
        "escuchando_desde": "All'ascolto da",
        "media_diaria": "Media giornaliera",
        "unidad_horas": "h",
        "unidad_minutos": "min",
        "veces": "volte",
        "listas": "Liste",
        "todas": "Tutte",
        "nueva_lista": "Nuova lista",
        "sin_listas": "Nessuna lista",
        "elige_lista": "Aggiungi a…",
        "borrar_lista": "Elimina lista",
        "siempre_visible": "Sempre in primo piano",
        "salir": "Esci",
        "record": "Record",
    },
    "nl": {
        "elige_carpeta": "Kies een map",
        "buscar": "Zoek in je muziek…",
        "dialogo_carpeta": "Kies de map met je muziek (dit kan een USB-stick zijn)",
        "sin_musica": "Geen muziek gevonden",
        "canciones": "Nummers",
        "ajustes": "Instellingen",
        "radio": "Radio",
        "buscar_emisoras": "Zenders zoeken…",
        "en_directo": "LIVE",
        "sin_emisoras": "Geen zenders gevonden",
        "sin_conexion": "Geen verbinding",
        "radio_no_disponible": "Radio niet beschikbaar op dit apparaat",
        "estadisticas": "Statistieken",
        "tiempo_escuchado": "Luistertijd",
        "canciones_reproducidas": "Afgespeelde nummers",
        "cancion_mas_escuchada": "Meest gespeelde nummer",
        "tiempo_radio": "Radiotijd",
        "emisora_mas_escuchada": "Meest beluisterde zender",
        "escuchando_desde": "Luistert sinds",
        "media_diaria": "Daggemiddelde",
        "unidad_horas": "u",
        "unidad_minutos": "min",
        "veces": "keer",
        "listas": "Lijsten",
        "todas": "Alle",
        "nueva_lista": "Nieuwe lijst",
        "sin_listas": "Nog geen lijsten",
        "elige_lista": "Toevoegen aan…",
        "borrar_lista": "Lijst verwijderen",
        "siempre_visible": "Altijd op voorgrond",
        "salir": "Afsluiten",
        "record": "Record",
    },
    "pl": {
        "elige_carpeta": "Wybierz folder",
        "buscar": "Szukaj w swojej muzyce…",
        "dialogo_carpeta": "Wybierz folder z muzyką (może to być pendrive)",
        "sin_musica": "Nie znaleziono muzyki",
        "canciones": "Utwory",
        "ajustes": "Ustawienia",
        "radio": "Radio",
        "buscar_emisoras": "Szukaj stacji…",
        "en_directo": "NA ŻYWO",
        "sin_emisoras": "Nie znaleziono stacji",
        "sin_conexion": "Brak połączenia",
        "radio_no_disponible": "Radio niedostępne na tym urządzeniu",
        "estadisticas": "Statystyki",
        "tiempo_escuchado": "Czas słuchania",
        "canciones_reproducidas": "Odtworzone utwory",
        "cancion_mas_escuchada": "Najczęściej słuchany utwór",
        "tiempo_radio": "Czas radia",
        "emisora_mas_escuchada": "Najczęściej słuchana stacja",
        "escuchando_desde": "Słuchasz od",
        "media_diaria": "Średnia dzienna",
        "unidad_horas": "godz.",
        "unidad_minutos": "min",
        "veces": "razy",
        "listas": "Listy",
        "todas": "Wszystkie",
        "nueva_lista": "Nowa lista",
        "sin_listas": "Brak list",
        "elige_lista": "Dodaj do…",
        "borrar_lista": "Usuń listę",
        "siempre_visible": "Zawsze na wierzchu",
        "salir": "Zakończ",
        "record": "Rekord",
    },
    "tr": {
        "elige_carpeta": "Bir klasör seç",
        "buscar": "Müziğinde ara…",
        "dialogo_carpeta": "Müziğinin bulunduğu klasörü seç (USB bellek de olabilir)",
        "sin_musica": "Müzik bulunamadı",
        "canciones": "Şarkılar",
        "ajustes": "Ayarlar",
        "radio": "Radyo",
        "buscar_emisoras": "İstasyon ara…",
        "en_directo": "CANLI",
        "sin_emisoras": "İstasyon bulunamadı",
        "sin_conexion": "Bağlantı yok",
        "radio_no_disponible": "Bu cihazda radyo kullanılamıyor",
        "estadisticas": "İstatistikler",
        "tiempo_escuchado": "Dinlenen süre",
        "canciones_reproducidas": "Çalınan şarkılar",
        "cancion_mas_escuchada": "En çok çalınan şarkı",
        "tiempo_radio": "Radyo süresi",
        "emisora_mas_escuchada": "En çok dinlenen istasyon",
        "escuchando_desde": "Dinliyor",
        "media_diaria": "Günlük ortalama",
        "unidad_horas": "sa",
        "unidad_minutos": "dk",
        "veces": "kez",
        "listas": "Listeler",
        "todas": "Tümü",
        "nueva_lista": "Yeni liste",
        "sin_listas": "Henüz liste yok",
        "elige_lista": "Şuraya ekle…",
        "borrar_lista": "Listeyi sil",
        "siempre_visible": "Her zaman üstte",
        "salir": "Çık",
        "record": "Rekor",
    },
    "ru": {
        "elige_carpeta": "Выберите папку",
        "buscar": "Поиск в вашей музыке…",
        "dialogo_carpeta": "Выберите папку с музыкой (можно USB-накопитель)",
        "sin_musica": "Музыка не найдена",
        "canciones": "Песни",
        "ajustes": "Настройки",
        "radio": "Радио",
        "buscar_emisoras": "Поиск станций…",
        "en_directo": "В ЭФИРЕ",
        "sin_emisoras": "Станции не найдены",
        "sin_conexion": "Нет подключения",
        "radio_no_disponible": "Радио недоступно на этом устройстве",
        "estadisticas": "Статистика",
        "tiempo_escuchado": "Время прослушивания",
        "canciones_reproducidas": "Прослушано треков",
        "cancion_mas_escuchada": "Самый частый трек",
        "tiempo_radio": "Время радио",
        "emisora_mas_escuchada": "Любимая станция",
        "escuchando_desde": "Слушает с",
        "media_diaria": "В среднем за день",
        "unidad_horas": "ч",
        "unidad_minutos": "мин",
        "veces": "раз",
        "listas": "Списки",
        "todas": "Все",
        "nueva_lista": "Новый список",
        "sin_listas": "Списков пока нет",
        "elige_lista": "Добавить в…",
        "borrar_lista": "Удалить список",
        "siempre_visible": "Поверх окон",
        "salir": "Выход",
        "record": "Рекорд",
    },
    "hi": {
        "elige_carpeta": "फ़ोल्डर चुनें",
        "buscar": "अपना संगीत खोजें…",
        "dialogo_carpeta": "अपने संगीत वाला फ़ोल्डर चुनें (USB ड्राइव भी चल सकती है)",
        "sin_musica": "कोई संगीत नहीं मिला",
        "canciones": "गाने",
        "ajustes": "सेटिंग्स",
        "radio": "रेडियो",
        "buscar_emisoras": "स्टेशन खोजें…",
        "en_directo": "लाइव",
        "sin_emisoras": "कोई स्टेशन नहीं मिला",
        "sin_conexion": "कनेक्शन नहीं",
        "radio_no_disponible": "इस डिवाइस पर रेडियो उपलब्ध नहीं",
        "estadisticas": "आंकड़े",
        "tiempo_escuchado": "सुनने का समय",
        "canciones_reproducidas": "चलाए गए गाने",
        "cancion_mas_escuchada": "सबसे ज़्यादा सुना गाना",
        "tiempo_radio": "रेडियो समय",
        "emisora_mas_escuchada": "सबसे ज़्यादा सुना स्टेशन",
        "escuchando_desde": "से सुन रहे हैं",
        "media_diaria": "दैनिक औसत",
        "unidad_horas": "घं",
        "unidad_minutos": "मि",
        "veces": "बार",
        "listas": "सूचियाँ",
        "todas": "सभी",
        "nueva_lista": "नई सूची",
        "sin_listas": "अभी कोई सूची नहीं",
        "elige_lista": "इसमें जोड़ें…",
        "borrar_lista": "सूची हटाएँ",
        "siempre_visible": "हमेशा ऊपर",
        "salir": "बंद करें",
        "record": "सर्वश्रेष्ठ",
    },
    "zh": {
        "elige_carpeta": "选择文件夹",
        "buscar": "搜索你的音乐…",
        "dialogo_carpeta": "选择存放音乐的文件夹（可以是 U 盘）",
        "sin_musica": "未找到音乐",
        "canciones": "歌曲",
        "ajustes": "设置",
        "radio": "电台",
        "buscar_emisoras": "搜索电台…",
        "en_directo": "直播",
        "sin_emisoras": "未找到电台",
        "sin_conexion": "无网络连接",
        "radio_no_disponible": "此设备无法使用广播",
        "estadisticas": "统计",
        "tiempo_escuchado": "收听时长",
        "canciones_reproducidas": "播放歌曲数",
        "cancion_mas_escuchada": "最常播放的歌曲",
        "tiempo_radio": "电台时长",
        "emisora_mas_escuchada": "最常收听的电台",
        "escuchando_desde": "开始使用于",
        "media_diaria": "日均时长",
        "unidad_horas": "小时",
        "unidad_minutos": "分钟",
        "veces": "次",
        "listas": "播放列表",
        "todas": "全部",
        "nueva_lista": "新建列表",
        "sin_listas": "还没有列表",
        "elige_lista": "添加到…",
        "borrar_lista": "删除列表",
        "siempre_visible": "总在最前",
        "salir": "退出",
        "record": "最高分",
    },
    "ja": {
        "elige_carpeta": "フォルダを選択",
        "buscar": "音楽を検索…",
        "dialogo_carpeta": "音楽の入ったフォルダを選択（USB メモリでも可）",
        "sin_musica": "音楽が見つかりません",
        "canciones": "曲",
        "ajustes": "設定",
        "radio": "ラジオ",
        "buscar_emisoras": "放送局を検索…",
        "en_directo": "ライブ",
        "sin_emisoras": "放送局が見つかりません",
        "sin_conexion": "接続がありません",
        "radio_no_disponible": "この端末ではラジオを利用できません",
        "estadisticas": "統計",
        "tiempo_escuchado": "再生時間",
        "canciones_reproducidas": "再生した曲数",
        "cancion_mas_escuchada": "最も再生した曲",
        "tiempo_radio": "ラジオ時間",
        "emisora_mas_escuchada": "最も聴いた放送局",
        "escuchando_desde": "利用開始日",
        "media_diaria": "1日の平均",
        "unidad_horas": "時間",
        "unidad_minutos": "分",
        "veces": "回",
        "listas": "リスト",
        "todas": "すべて",
        "nueva_lista": "新規リスト",
        "sin_listas": "リストがありません",
        "elige_lista": "追加先…",
        "borrar_lista": "リストを削除",
        "siempre_visible": "常に手前に表示",
        "salir": "終了",
        "record": "ベスト",
    },
    "ko": {
        "elige_carpeta": "폴더 선택",
        "buscar": "음악 검색…",
        "dialogo_carpeta": "음악이 있는 폴더를 선택하세요 (USB도 가능)",
        "sin_musica": "음악을 찾을 수 없습니다",
        "canciones": "노래",
        "ajustes": "설정",
        "radio": "라디오",
        "buscar_emisoras": "방송국 검색…",
        "en_directo": "라이브",
        "sin_emisoras": "방송국을 찾을 수 없습니다",
        "sin_conexion": "연결 없음",
        "radio_no_disponible": "이 기기에서는 라디오를 사용할 수 없습니다",
        "estadisticas": "통계",
        "tiempo_escuchado": "감상 시간",
        "canciones_reproducidas": "재생한 곡",
        "cancion_mas_escuchada": "가장 많이 들은 곡",
        "tiempo_radio": "라디오 시간",
        "emisora_mas_escuchada": "가장 많이 들은 방송국",
        "escuchando_desde": "사용 시작일",
        "media_diaria": "일일 평균",
        "unidad_horas": "시간",
        "unidad_minutos": "분",
        "veces": "회",
        "listas": "목록",
        "todas": "전체",
        "nueva_lista": "새 목록",
        "sin_listas": "목록이 없습니다",
        "elige_lista": "추가할 목록…",
        "borrar_lista": "목록 삭제",
        "siempre_visible": "항상 위에",
        "salir": "종료",
        "record": "최고 기록",
    },
}


def _idioma_del_sistema():
    """Código del idioma de la interfaz, o 'en' si no lo tenemos.

    Se mira 'uiLanguages' y no solo el nombre del locale porque son cosas
    distintas: un Mac puede estar en inglés con formatos de España, y lo
    que importa es en qué idioma quiere leer la persona, no cómo escribe
    las fechas.
    """
    candidatos = list(QLocale.system().uiLanguages()) + [QLocale.system().name()]
    for etiqueta in candidatos:
        codigo = etiqueta.replace("_", "-").split("-")[0].lower()
        if codigo in IDIOMAS:
            return codigo
    return "en"


TEXTOS = IDIOMAS[_idioma_del_sistema()]

TEXTO_BUSCAR_LOCAL = TEXTOS["buscar"]

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
# --- Estadisticas ---
# Se guardan desde la primera vez que se abre la app y no se borran nunca
# solas. Son de este ordenador: no viajan a ningun sitio ni se envian.
CLAVE_ESTAD_DESDE = "estadisticas_desde"
CLAVE_ESTAD_MS_MUSICA = "estadisticas_ms_musica"
CLAVE_ESTAD_MS_RADIO = "estadisticas_ms_radio"
CLAVE_ESTAD_CANCIONES = "estadisticas_canciones_reproducidas"
CLAVE_ESTAD_CUENTAS_CANCIONES = "estadisticas_cuentas_canciones"
CLAVE_ESTAD_CUENTAS_EMISORAS = "estadisticas_cuentas_emisoras"

# Cuanto hay que escuchar algo para que cuente como "reproducido". Sin un
# minimo, pasar de cancion veinte veces seguidas contaria como veinte
# reproducciones y el numero no significaria nada. Treinta segundos es lo
# que usa la industria.
MS_PARA_CONTAR_REPRODUCCION = 30_000
# Cada cuanto tiempo escuchado se pasa a disco lo acumulado. Los avisos
# de progreso llegan diez veces por segundo; escribir en cada uno seria
# absurdo.
MS_ENTRE_VOLCADOS_ESTADISTICAS = 15_000

# Color de acento elegido en Ajustes, guardado como '#rrggbb'. Si no hay
# nada guardado -o lo guardado ya no está en la paleta- se usa el azul.
CLAVE_COLOR_ACENTO = "color_acento"
# Listas creadas dentro de la app, como JSON {nombre: [rutas]}. Las que
# salen de subcarpetas no se guardan: se leen del disco cada vez, que
# para eso estan ahi.
CLAVE_LISTAS = "listas"
# Si la ventana se queda por encima de las demás. Es lo que sustituye a
# tener la app siempre a mano: la tarjeta es pequeña y cabe en una
# esquina mientras se trabaja con otra cosa delante.
CLAVE_SIEMPRE_VISIBLE = "siempre_visible"
# La mejor marca del juego escondido. Se guarda porque es lo único que
# hace volver a él una segunda vez.
CLAVE_RECORD_JUEGO = "record_juego"
# Cómo se llega al juego: tocar los seis colores de Ajustes del último
# al primero, o sea de derecha a izquierda empezando por la fila de
# abajo. Nadie da con eso sin querer, y acaba en el azul de la marca.
COMBINACION_JUEGO = (5, 4, 3, 2, 1, 0)

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


class AnalizadorOnda(QObject):
    """Saca la curva de volumen de una cancion para que la nota lata con
    ella de verdad.

    Hasta ahora el orbe se movia al azar: subia y bajaba solo, sin oir
    nada. Qt 6 quito el mecanismo que daba el audio en directo, asi que
    lo que se hace es descodificar el archivo entero en segundo plano
    nada mas cargarlo y quedarse con un valor de volumen cada 50 ms.

    Sale practicamente gratis: descodificar una cancion de cuatro
    minutos cuesta dos decimas de segundo, la curva ocupa unas decenas
    de kilobytes, y QAudioDecoder viene dentro de QtMultimedia, que ya
    viajaba en el paquete por el propio reproductor. Ni una dependencia
    nueva ni un megabyte de mas.
    """

    RESOLUCION_MS = 50
    # De cada cuantas muestras se mira una. Para una envolvente no hace
    # falta verlas todas, y asi el calculo se queda en centesimas de
    # segundo en vez de segundos.
    PASO_MUESTRAS = 16
    # El volumen se normaliza contra este percentil y no contra el maximo:
    # un solo chasquido fuerte dejaria el resto de la cancion planchado.
    PERCENTIL_REFERENCIA = 0.95

    listo = pyqtSignal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.decodificador = None
        self.ruta_en_curso = None
        self._envolvente = []

    def analizar(self, ruta):
        # Un descodificador nuevo por cancion, y el viejo desenganchado y
        # a la basura. Reutilizando uno solo, el aviso de "he terminado"
        # que Qt lanza al pararlo llegaba ya empezado el analisis
        # siguiente y lo daba por bueno con dos datos.
        self.cancelar()
        self.ruta_en_curso = ruta
        self._envolvente = []
        self.decodificador = QAudioDecoder(self)
        self.decodificador.bufferReady.connect(self._leer_buffers)
        self.decodificador.finished.connect(self._al_terminar)
        self.decodificador.error.connect(lambda *_: self.cancelar())
        self.decodificador.setSource(QUrl.fromLocalFile(ruta))
        self.decodificador.start()

    def cancelar(self):
        if self.decodificador is not None:
            viejo, self.decodificador = self.decodificador, None
            for senal in (viejo.bufferReady, viejo.finished, viejo.error):
                try:
                    senal.disconnect()
                except TypeError:
                    pass
            viejo.stop()
            viejo.deleteLater()
        self.ruta_en_curso = None
        self._envolvente = []

    @staticmethod
    def _como_leerlo(formato_muestra):
        """Cómo leer las muestras que entrega el descodificador.

        Devuelve (código de array, valor del silencio, escala hasta 1.0),
        o None si el formato no se conoce. Hace falta porque cada archivo
        se descodifica en el formato que lleva dentro: los comprimidos
        salen en coma flotante, pero un FLAC, un WAV o un AIFF salen en
        enteros. Dando por hecho que todo era coma flotante, esos tres se
        descartaban enteros y la nota se quedaba sin latir justo con los
        formatos de más calidad.
        """
        tipos = QAudioFormat.SampleFormat
        entero_32 = "i" if array.array("i").itemsize == 4 else "l"
        return {
            tipos.UInt8: ("B", 128.0, 128.0),      # sin signo: el silencio es 128
            tipos.Int16: ("h", 0.0, 32768.0),
            tipos.Int32: (entero_32, 0.0, 2147483648.0),
            tipos.Float: ("f", 0.0, 1.0),
        }.get(formato_muestra)

    def _leer_buffers(self):
        if self.decodificador is None:
            return
        while self.decodificador.bufferAvailable():
            buffer = self.decodificador.read()
            if not buffer.isValid():
                continue
            lectura = self._como_leerlo(buffer.format().sampleFormat())
            if lectura is None:
                continue  # un formato de muestra que no conocemos
            codigo, centro, escala = lectura

            crudo = buffer.constData()
            crudo.setsize(buffer.byteCount())
            muestras = array.array(codigo)
            muestras.frombytes(bytes(crudo))
            recortadas = muestras[::self.PASO_MUESTRAS]
            if not recortadas:
                continue

            # El pico absoluto del tramo. max() y min() sobre un array son
            # de una sola pasada en C; recorrerlo en Python no lo seria. Se
            # normaliza solo el pico, no muestra a muestra, que seria
            # volver a recorrerlo en Python.
            pico = max(max(recortadas) - centro, centro - min(recortadas)) / escala
            indice = int(buffer.startTime() / 1000 / self.RESOLUCION_MS)
            if indice < 0:
                continue
            while len(self._envolvente) <= indice:
                self._envolvente.append(0.0)
            if pico > self._envolvente[indice]:
                self._envolvente[indice] = pico

    def _al_terminar(self):
        self._leer_buffers()  # puede quedar algo sin recoger
        ruta, envolvente = self.ruta_en_curso, self._envolvente
        self.ruta_en_curso = None
        self._envolvente = []
        if ruta is None or not envolvente:
            return
        self.listo.emit(ruta, self._normalizar(envolvente))

    @classmethod
    def _normalizar(cls, envolvente):
        ordenada = sorted(envolvente)
        referencia = ordenada[min(len(ordenada) - 1,
                                  int(len(ordenada) * cls.PERCENTIL_REFERENCIA))]
        if referencia <= 0.0001:
            return envolvente
        return [min(1.0, valor / referencia) for valor in envolvente]


# Se resuelve una sola vez, la primera que se pregunta. Guardarlo no es
# por ahorrar: es que la respuesta deja de poder cambiar en cuanto Qt
# fija el motor, así que preguntar dos veces daría lo mismo igualmente.
_hay_cifrado = None


# El motor de cifrado que se usa en Windows. Es el del propio sistema:
# viene con Windows, se actualiza con él y no necesita nada al lado.
MOTOR_TLS_WINDOWS = "schannel"


def asegurar_tls():
    """Deja activo un motor de cifrado que de verdad funcione.

    Importa por la radio, que pide las emisoras por HTTPS. Qt trae varios
    motores y elige uno al arrancar, normalmente el de OpenSSL. El
    problema es la aplicación empaquetada para Windows: viaja el conector
    de OpenSSL pero no las bibliotecas de OpenSSL, porque PyQt no las
    distribuye. Qt se queda con un motor incapaz de cifrar y la radio
    falla como si no hubiera internet, sin más explicación y solo dentro
    del .exe.

    En Windows se usa siempre 'schannel', el cifrado del propio sistema.
    Siempre, y no solo cuando falta OpenSSL: mirar si hay un OpenSSL a
    mano y usarlo si lo hay significaría depender de la biblioteca que
    otro programa haya dejado en el PATH del usuario -la de Git, la de
    una base de datos-, de la versión que sea y sin garantía de que el
    conector de Qt se entienda con ella. El del sistema está en todos los
    Windows y es el que se ha probado.

    Solo en Windows: cambiar de motor por si acaso es peligroso, y está
    comprobado. En macOS, forzar el motor de Apple en lugar del que Qt
    elige deja la radio sin responder, porque SecureTransport está
    abandonado desde hace años. Ahí no se toca nada.

    OJO: hay que llamarla al arrancar, antes de que nadie toque la red.
    En cuanto se usa el cifrado por primera vez -y basta con preguntar
    'supportsSsl()'- Qt fija el motor y ya no admite cambios.
    """
    global _hay_cifrado
    if _hay_cifrado is not None:
        return _hay_cifrado

    if sys.platform == "win32" and MOTOR_TLS_WINDOWS in QSslSocket.availableBackends():
        if QSslSocket.activeBackend() != MOTOR_TLS_WINDOWS:
            QSslSocket.setActiveBackend(MOTOR_TLS_WINDOWS)
    _hay_cifrado = QSslSocket.supportsSsl()
    return _hay_cifrado


class ClienteRadio(QObject):
    """Busca emisoras en Radio Browser (https://api.radio-browser.info).

    Es el único directorio de radio con API pública de verdad: sin clave,
    sin registro y con los datos en dominio público. TuneIn y Radio Garden
    no tienen API abierta -lo que se usa de ellas son endpoints internos
    sin documentar-, y apoyarse en eso habría dejado la función a merced
    de que las cortaran cualquier día.

    Solo hay una petición en vuelo a la vez: al empezar una nueva se
    cancela la anterior, que es lo que hace falta cuando alguien escribe
    una búsqueda tras otra.
    """

    SERVIDOR = "https://all.api.radio-browser.info"
    # El directorio pide identificarse; sin esto las peticiones pueden
    # rechazarse.
    AGENTE = b"Digilogic/1.0 (https://digilogic-app.github.io)"
    LIMITE = 60
    ESPERA_MS = 12_000

    resultados = pyqtSignal(list)
    fallo = pyqtSignal()
    # El equipo no sabe cifrar: no es culpa de la conexión de nadie y no
    # sirve de nada reintentar, así que va por su propia señal.
    sin_cifrado = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hay_tls = asegurar_tls()
        self.red = QNetworkAccessManager(self)
        self.respuesta_en_curso = None
        # Para saber si una lista vacía viene de haber filtrado por país,
        # que es el único caso en el que merece la pena reintentar.
        self._ultima_fue_del_pais = False

    def emisoras_del_pais(self):
        """La primera pantalla: lo más escuchado del país del sistema. Es
        lo que casi todo el mundo busca, y evita abrir con una lista de
        emisoras alemanas que no le dicen nada a nadie.

        El país se le pregunta a Qt en vez de recortar la cadena del
        idioma: un sistema configurado solo como 'en', sin región, daría
        'EN' por las bravas, que no es ningún país y devolvería una lista
        vacía. Qt deduce el país probable (ahí, Estados Unidos).
        """
        pais = QLocale.territoryToCode(QLocale.system().territory())
        if len(pais) != 2:
            self.top_mundial()
            return
        self._ultima_fue_del_pais = True
        self._pedir(
            "stations/search?countrycode=%s&order=votes&reverse=true&limit=%d"
            % (pais, self.LIMITE)
        )

    def top_mundial(self):
        self._ultima_fue_del_pais = False
        self._pedir("stations/topvote/%d" % self.LIMITE)

    def buscar(self, texto):
        consulta = QUrl.toPercentEncoding(texto.strip()).data().decode()
        if not consulta:
            self.emisoras_del_pais()
            return
        self._ultima_fue_del_pais = False
        self._pedir(
            "stations/search?name=%s&order=votes&reverse=true&limit=%d"
            % (consulta, self.LIMITE)
        )

    def cancelar(self):
        if self.respuesta_en_curso is not None:
            respuesta, self.respuesta_en_curso = self.respuesta_en_curso, None
            respuesta.abort()

    def _pedir(self, camino):
        self.cancelar()
        if not self.hay_tls:
            # Sin cifrado la petición saldría y volvería con un error de
            # red corriente, y el usuario leería "sin conexión" mientras
            # el resto de su internet funciona perfectamente.
            self.sin_cifrado.emit()
            return
        separador = "&" if "?" in camino else "?"
        peticion = QNetworkRequest(
            QUrl(f"{self.SERVIDOR}/json/{camino}{separador}hidebroken=true")
        )
        peticion.setRawHeader(b"User-Agent", self.AGENTE)
        peticion.setTransferTimeout(self.ESPERA_MS)
        respuesta = self.red.get(peticion)
        self.respuesta_en_curso = respuesta
        respuesta.finished.connect(lambda: self._al_responder(respuesta))

    def _al_responder(self, respuesta):
        if respuesta is not self.respuesta_en_curso:
            respuesta.deleteLater()  # una búsqueda cancelada: no interesa
            return
        self.respuesta_en_curso = None
        try:
            if respuesta.error() != QNetworkReply.NetworkError.NoError:
                self.fallo.emit()
                return
            crudo = bytes(respuesta.readAll()).decode("utf-8", "replace")
            emisoras = self._limpiar(json.loads(crudo))
            # En un país con pocas emisoras en el directorio, la lista
            # puede volver vacía. Antes de decirle a nadie que no hay
            # nada, se prueba con lo más escuchado del mundo.
            if not emisoras and self._ultima_fue_del_pais:
                self.top_mundial()
                return
            self.resultados.emit(emisoras)
        except (ValueError, UnicodeDecodeError):
            self.fallo.emit()
        finally:
            respuesta.deleteLater()

    @staticmethod
    def _limpiar(datos):
        """Deja solo lo que la interfaz necesita, y tira lo que no se
        puede reproducir o viene sin nombre."""
        emisoras = []
        vistas = set()
        for entrada in datos if isinstance(datos, list) else []:
            url = (entrada.get("url_resolved") or entrada.get("url") or "").strip()
            nombre = " ".join((entrada.get("name") or "").split())
            if not url or not nombre or url in vistas:
                continue
            vistas.add(url)
            emisoras.append({
                "nombre": nombre,
                "url": url,
                "pais": (entrada.get("countrycode") or "").upper(),
                "bitrate": entrada.get("bitrate") or 0,
            })
        return emisoras


# ------------------------------------------------------------------
# Fila de la lista de emisoras: el nombre y, a la derecha, el país y la
# calidad. Misma altura y mismo aire que las filas de canciones.
# ------------------------------------------------------------------

class EtiquetaElidida(QLabel):
    """Etiqueta que corta el texto con puntos suspensivos cuando no cabe,
    en vez de dejarlo cortado a hachazos contra lo que tenga al lado."""

    def __init__(self, texto, parent=None):
        super().__init__(texto, parent)
        self.texto_entero = texto
        # 'Ignored' es lo que hace que el nombre largo no ensanche la
        # fila: se conforma con el ancho que le deje el layout.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        metricas = QFontMetrics(self.font())
        self.setText(
            metricas.elidedText(self.texto_entero, Qt.TextElideMode.ElideRight, self.width())
        )


def formatear_duracion_larga(milisegundos):
    """'142 h 32 min'. Para totales de meses enteros, donde un 8:34:12 no
    se lee de un vistazo."""
    minutos_totales = max(0, milisegundos) // 60_000
    horas, minutos = divmod(minutos_totales, 60)
    if horas:
        return f"{horas} {TEXTOS['unidad_horas']} {minutos} {TEXTOS['unidad_minutos']}"
    return f"{minutos} {TEXTOS['unidad_minutos']}"


class FilaConCapa(QWidget):
    """Un widget corriente con un hijo -la capa- que lo cubre entero,
    siempre. Lo usa la fila de tiempos para el aviso de la radio: el
    aviso no entra en el layout (ver dónde se crea) y por eso hay que
    estirarlo a mano cada vez que la fila cambia de tamaño, que pasa al
    entrar y salir del modo compacto. Sin esto se quedaba con el ancho
    de la tarjeta grande y su centro caía a la derecha de la pequeña."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.capa = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.capa is not None:
            self.capa.setGeometry(self.rect())


class FilaEstadistica(QWidget):
    """Un dato: el rotulo pequeño arriba y la cifra grande debajo."""

    ALTO = 42

    def __init__(self, titulo, valor, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(0)

        etiqueta = EtiquetaElidida(titulo)
        etiqueta.setStyleSheet(
            "font-size: 10px; color: rgba(255,255,255,110); background: transparent;"
        )
        layout.addWidget(etiqueta)

        cifra = EtiquetaElidida(valor)
        cifra.setStyleSheet(
            "font-size: 15px; font-weight: 600;"
            "color: rgba(255,255,255,235); background: transparent;"
        )
        layout.addWidget(cifra)


# Miniaturas de portada ya escaladas, para no releer el mismo MP3 cada
# vez que se pinta una tarjeta de lista. La clave es (ruta, lado).
_CACHE_MINIATURAS = {}


def miniatura_portada(ruta, lado, escala=1.0):
    """La portada de un MP3 recortada en cuadrado y escalada. None si no
    la tiene."""
    clave = (ruta, lado, round(escala, 2))
    if clave in _CACHE_MINIATURAS:
        return _CACHE_MINIATURAS[clave]

    portada = extraer_portada(ruta)
    if portada is None or portada.isNull():
        _CACHE_MINIATURAS[clave] = None
        return None

    pixeles = max(1, round(lado * escala))
    recortada = portada.scaled(
        QSize(pixeles, pixeles),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    if recortada.width() != pixeles or recortada.height() != pixeles:
        recortada = recortada.copy(
            (recortada.width() - pixeles) // 2,
            (recortada.height() - pixeles) // 2,
            pixeles, pixeles,
        )
    recortada.setDevicePixelRatio(escala)
    _CACHE_MINIATURAS[clave] = recortada
    return recortada


class TarjetaLista(QPushButton):
    """Una lista, dibujada como las carpetas de iOS: un cuadrado con las
    portadas de sus cuatro primeras canciones en mosaico, y debajo el
    nombre y cuantas canciones tiene. Es la misma figura que usa MusicPi
    para sus playlists, para que las dos apps se parezcan.
    """

    LADO_CUBIERTA = 76
    ALTO_TEXTO = 28
    RADIO_CUBIERTA = 16
    RADIO_CELDA = 5
    MARGEN = 7   # el 8% de MusicPi, redondeado a pixeles enteros
    HUECO = 5    # su 6%

    def __init__(self, nombre, rutas, parent=None):
        super().__init__(parent)
        self.nombre = nombre
        self.rutas = rutas
        self.setFixedSize(self.LADO_CUBIERTA, self.LADO_CUBIERTA + self.ALTO_TEXTO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        encogida = 0.94 if self.isDown() else 1.0
        lado = self.LADO_CUBIERTA * encogida
        x = (self.width() - lado) / 2
        y = (self.LADO_CUBIERTA - lado) / 2

        painter.save()
        painter.translate(x, y)
        painter.scale(encogida, encogida)
        self._pintar_cubierta(painter)
        painter.restore()

        fuente = QFont(self.font())
        fuente.setPixelSize(11)
        fuente.setWeight(QFont.Weight.DemiBold)
        painter.setFont(fuente)
        painter.setPen(QColor(255, 255, 255, 190 if self.isDown() else 225))
        metricas = QFontMetrics(fuente)
        nombre = metricas.elidedText(self.nombre, Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(
            QRectF(0, self.LADO_CUBIERTA + 3, self.width(), 14),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), nombre,
        )

        fuente.setPixelSize(10)
        fuente.setWeight(QFont.Weight.Normal)
        painter.setFont(fuente)
        painter.setPen(QColor(255, 255, 255, 90))
        painter.drawText(
            QRectF(0, self.LADO_CUBIERTA + 16, self.width(), 12),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            QLocale.system().toString(len(self.rutas)),
        )

    def _pintar_cubierta(self, painter):
        lado = self.LADO_CUBIERTA
        marco = QPainterPath()
        marco.addRoundedRect(
            QRectF(0, 0, lado, lado), self.RADIO_CUBIERTA, self.RADIO_CUBIERTA
        )
        painter.fillPath(marco, QColor(255, 255, 255, 16))
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(marco)

        lado_celda = (lado - 2 * self.MARGEN - self.HUECO) / 2
        escala = self.devicePixelRatioF()
        for i in range(4):
            fila, columna = divmod(i, 2)
            celda = QRectF(
                self.MARGEN + columna * (lado_celda + self.HUECO),
                self.MARGEN + fila * (lado_celda + self.HUECO),
                lado_celda, lado_celda,
            )
            recorte = QPainterPath()
            recorte.addRoundedRect(celda, self.RADIO_CELDA, self.RADIO_CELDA)

            miniatura = (
                miniatura_portada(self.rutas[i], round(lado_celda), escala)
                if i < len(self.rutas) else None
            )
            painter.save()
            painter.setClipPath(recorte, Qt.ClipOperation.IntersectClip)
            if miniatura is not None:
                painter.drawPixmap(celda, miniatura, QRectF(miniatura.rect()))
            elif i < len(self.rutas):
                # Sin portada: el mismo degradado del acento que pone
                # MusicPi en las celdas vacias de sus playlists.
                brillo = QRadialGradient(
                    celda.left() + celda.width() * 0.3,
                    celda.top() + celda.height() * 0.25,
                    celda.width(),
                )
                brillo.setColorAt(0.0, aclarar(COLOR_ACENTO, 0.25))
                brillo.setColorAt(0.55, oscurecer(COLOR_ACENTO, 0.22))
                brillo.setColorAt(1.0, QColor(28, 35, 51))
                painter.fillPath(recorte, brillo)
            else:
                painter.fillPath(recorte, QColor(255, 255, 255, 10))
            painter.restore()


class FilaEmisora(QWidget):
    def __init__(self, emisora, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 9, 0)
        layout.setSpacing(6)

        self.indicador = IndicadorEcualizador(tamano=11)
        layout.addWidget(self.indicador)

        self.label = EtiquetaElidida(emisora["nombre"])
        self.label.setStyleSheet("""
            font-size: 12px;
            font-weight: 500;
            color: rgba(255, 255, 255, 220);
            background: transparent;
        """)
        layout.addWidget(self.label, 1)

        detalle = " · ".join(
            parte for parte in (emisora["pais"],
                                f"{emisora['bitrate']}k" if emisora["bitrate"] else "")
            if parte
        )
        etiqueta = QLabel(detalle)
        etiqueta.setStyleSheet(
            "font-size: 10px; color: rgba(255,255,255,90); background: transparent;"
        )
        layout.addWidget(etiqueta, 0)


class Reproductor(QWidget):
    # Las cuatro caras del panel de arriba, en el orden en que se añaden
    # al QStackedLayout.
    PANTALLA_PORTADA = 0
    PANTALLA_LISTA = 1
    PANTALLA_MENU = 2
    PANTALLA_AJUSTES = 3
    PANTALLA_RADIO = 4
    PANTALLA_ESTADISTICAS = 5
    PANTALLA_LISTAS = 6
    PANTALLA_DETALLE_LISTA = 7
    PANTALLA_JUEGO = 8

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
        # Vigila la carpeta elegida: si se le añade o quita una canción
        # con la app abierta, la lista se pone al día sola en vez de
        # obligar a volver a elegir la misma carpeta. Lo que había en
        # ella la última vez que se leyó se guarda aparte para saber si
        # lo que cambió fue la música o cualquier otro archivo.
        self._archivos_carpeta = []
        self.vigilante_carpeta = QFileSystemWatcher(self)
        self.vigilante_carpeta.directoryChanged.connect(self._al_cambiar_carpeta_en_disco)
        self._temporizador_refresco_carpeta = QTimer(self)
        self._temporizador_refresco_carpeta.setSingleShot(True)
        self._temporizador_refresco_carpeta.setInterval(ESPERA_REFRESCO_CARPETA_MS)
        self._temporizador_refresco_carpeta.timeout.connect(self._refrescar_carpeta)
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
        self.player.errorOccurred.connect(self._al_fallar_reproduccion)
        self.player.positionChanged.connect(self.actualizar_progreso)
        self.player.durationChanged.connect(self.actualizar_duracion)

        self.ajustes = QSettings(AJUSTES_ORGANIZACION, AJUSTES_APLICACION)

        # Si la ventana quedó puesta por encima de las demás, se aplica
        # aquí, antes de enseñar nada: cambiar una bandera de ventana con
        # la ventana ya en pantalla obliga a Qt a recrearla.
        self.siempre_visible = self.ajustes.value(CLAVE_SIEMPRE_VISIBLE, False, type=bool)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.siempre_visible)

        self._iniciar_estadisticas()

        # El color de acento se recupera antes de construir nada: así la
        # interfaz nace ya con el color elegido y no hay que repintarla
        # entera nada más abrir.
        self._tamanos_icono = {}  # botón -> tamaño, para poder reestilarlos
        COLOR_ACENTO.setRgb(*self._color_acento_guardado().getRgb()[:3])

        # A qué pantalla del panel vuelve el botón de la lista. Arranca en
        # las canciones, que es lo que hacía antes de existir el menú.
        self._pantalla_panel = self.PANTALLA_LISTA

        # Cambio de tamano animado entre la tarjeta grande y la compacta.
        self._geometria_origen = None
        self._geometria_destino = None
        self.lienzo_transicion = LienzoTransicion(self)
        self.anim_tamano = QVariantAnimation(self)
        self.anim_tamano.setDuration(DURACION_CAMBIO_TAMANO_MS)
        self.anim_tamano.setStartValue(0.0)
        self.anim_tamano.setEndValue(1.0)
        # De arranque suave y frenada suave, como el encoger de macOS.
        self.anim_tamano.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim_tamano.valueChanged.connect(self._al_avanzar_tamano)
        self.anim_tamano.finished.connect(self._al_terminar_tamano)

        # Listas. 'modo_elegir_lista' se enciende cuando se llega a la
        # rejilla desde el "+" de una cancion: entonces tocar una tarjeta
        # no la abre, sino que mete esa cancion dentro.
        # Colores tocados seguidos en Ajustes, por si forman la clave del
        # juego escondido.
        self._secuencia_color = []

        self.modo_elegir_lista = None   # ruta de la cancion a anadir, o None
        self.lista_abierta = None       # (nombre, rutas, es_propia)

        # Radio. 'emisora_actual' es lo que distingue los dos modos de
        # reproducción: mientras no sea None, lo que suena es un directo y
        # no un archivo de la carpeta.
        self.emisoras = []
        self._filas_emisoras = []
        self.indice_emisora = -1
        self.emisora_actual = None
        self.analizador_onda = AnalizadorOnda(self)
        self.analizador_onda.listo.connect(self._al_analizar_onda)
        self.cliente_radio = ClienteRadio(self)
        self.cliente_radio.resultados.connect(self._al_llegar_emisoras)
        self.cliente_radio.fallo.connect(self._al_fallar_radio)
        self.cliente_radio.sin_cifrado.connect(self._al_faltar_cifrado)

        # Para que las teclas sueltas (espacio, flechas) lleguen a la
        # ventana en vez de perderse. Los botones se quedan sin foco más
        # abajo, en construir_interfaz.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Soltar una carpeta encima elige esa carpeta.
        self.setAcceptDrops(True)

        self.construir_interfaz()
        self._crear_atajos()

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

        # La tarjeta no tiene botón de cerrar ni barra de título: encima
        # del panel no hay nada más que aire. Se sale desde el botón de
        # apagado del menú, o con el atajo de siempre (⌘Q / Alt+F4).

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
        #
        # El chevron sube al menú. La lista es la pantalla a la que se
        # entra directamente al pulsar el botón de la lista -eso no ha
        # cambiado-, y desde aquí se sale hacia arriba, como en un
        # reproductor físico.
        #
        # A la izquierda del campo, donde suele estar la lupa en cualquier
        # buscador. Con el módulo de descargas presente es un interruptor
        # de modo; sin él, una lupa fija que solo indica que ahí se busca.
        if self.control_descargas is not None:
            icono_barra = self.control_descargas.boton
        else:
            icono_barra = IconoLupa(lado=24)

        barra_busqueda, self.boton_atras_lista, self.campo_busqueda = (
            self._crear_barra_busqueda(color_panel_css, icono_barra, TEXTO_BUSCAR_LOCAL)
        )
        self.boton_atras_lista.clicked.connect(lambda: self.ir_a_pantalla(self.PANTALLA_MENU))
        self.campo_busqueda.textChanged.connect(self._al_cambiar_texto_campo_busqueda)
        # Enter solo hace algo si se pueden descargar canciones: al buscar
        # en la música local el filtrado ya es en vivo y no hay nada que
        # confirmar.
        if self.control_descargas is not None:
            self.campo_busqueda.returnPressed.connect(
                self.control_descargas.intentar_descargar
            )

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

        # --- Menú y ajustes: las otras dos pantallas del panel ---
        self.stack_superior.addWidget(self._construir_pantalla_menu(color_panel_css))
        self.stack_superior.addWidget(self._construir_pantalla_ajustes(color_panel_css))
        self.stack_superior.addWidget(
            self._construir_pantalla_radio(color_panel_css, color_panel)
        )
        self.stack_superior.addWidget(
            self._construir_pantalla_estadisticas(color_panel_css, color_panel)
        )
        self.stack_superior.addWidget(self._construir_pantalla_listas(color_panel_css))
        self.stack_superior.addWidget(
            self._construir_pantalla_detalle_lista(color_panel_css, color_panel)
        )
        self.stack_superior.addWidget(self._construir_pantalla_juego(color_panel_css))

        self.contenedor_superior = QWidget()
        self.contenedor_superior.setLayout(self.stack_superior)
        self.contenedor_superior.setFixedHeight(ALTO_ZONA_SUPERIOR)

        # Va por encima del stack, sin entrar en ningún layout: solo se
        # muestra mientras dura el deslizamiento entre pantallas.
        self.deslizador = DeslizadorPantallas(
            RADIO_ESQUINAS, color_panel, parent=self.contenedor_superior
        )

        # --- Título (con marquesina al reproducir) y botón de elegir carpeta a la vez ---
        self.label_titulo = TituloAnimado(TEXTOS["elige_carpeta"])
        self.label_titulo.clicked.connect(self.elegir_carpeta)
        self._actualizar_titulo_clicable()

        # --- Progreso ---
        self.slider_progreso = SliderProgreso(Qt.Orientation.Horizontal)
        self._progreso_visible = None
        self.slider_progreso.valueChanged.connect(self.actualizar_estilo_progreso)
        self.actualizar_estilo_progreso(0)
        self.slider_progreso.sliderPressed.connect(self.al_empezar_arrastre)
        self.slider_progreso.sliderReleased.connect(self.al_soltar_arrastre)

        self.fila_tiempos = FilaConCapa()
        # Sin alto fijo esta fila reclamaba 24 px para dos textos que solo
        # piden 13, y esos 11 px de más salían como una franja vacía entre
        # la barra de progreso y los controles. Se ata a 16: lo justo para
        # el texto, con un par de píxeles de holgura porque las fuentes de
        # Windows piden algo más de alto que las de macOS.
        self.fila_tiempos.setFixedHeight(ALTO_FILA_TIEMPOS)
        fila_tiempos = QHBoxLayout(self.fila_tiempos)
        fila_tiempos.setContentsMargins(0, 0, 0, 0)
        self.label_tiempo_actual = QLabel("0:00")
        self.label_tiempo_total = QLabel("0:00")
        self.label_tiempo_actual.setStyleSheet("color: rgba(255,255,255,140); font-size: 11px;")
        self.label_tiempo_total.setStyleSheet("color: rgba(255,255,255,140); font-size: 11px;")
        fila_tiempos.addWidget(self.label_tiempo_actual)
        fila_tiempos.addStretch()
        fila_tiempos.addWidget(self.label_tiempo_total)

        # El aviso de la radio ("EN DIRECTO" / "SIN CONEXIÓN") no entra en
        # el layout: se pone por encima de la fila entera y se centra. Así
        # la fila de la música se queda exactamente como estaba -cambiar
        # los pesos del layout movía el "8:40" un píxel- y el aviso puede
        # ir centrado de verdad, no en el hueco que dejen los otros dos.
        self.label_estado_radio = QLabel(self.fila_tiempos)
        self.label_estado_radio.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_estado_radio.hide()
        self.fila_tiempos.capa = self.label_estado_radio

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
        self.boton_anterior.clicked.connect(self._pulsar_anterior)
        self.layout_controles.addWidget(self.boton_anterior)

        self.boton_play = crear_boton_control("play")
        self._aplicar_estilo_play(50)
        self.boton_play.clicked.connect(self._pulsar_play)
        self.layout_controles.addWidget(self.boton_play)

        self.boton_siguiente = self.crear_boton_icono("siguiente", tamano=36)
        self.boton_siguiente.clicked.connect(self._pulsar_siguiente)
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
        layout.setContentsMargins(16, MARGEN_SUPERIOR_TARJETA, 16, 14)
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
        self.label_estado_radio.setStyleSheet(
            "color: rgba(255,255,255,140); font-size: 11px; background: transparent;"
        )

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
        self.contenedor_superior.hide()
        self.boton_lista.hide()
        self.contenedor_aleatorio.hide()

        self._aplicar_estilo_play(40)
        self._aplicar_estilo_icono(self.boton_anterior, 30)
        self._aplicar_estilo_icono(self.boton_siguiente, 30)
        self.layout_controles.setSpacing(14)

        for etiqueta in (self.label_tiempo_actual, self.label_tiempo_total):
            etiqueta.setStyleSheet("color: rgba(255,255,255,130); font-size: 9px;")
        # Un punto menos que en la tarjeta grande: en mayúsculas y a este
        # tamaño, 11px pesaba demasiado sobre el botón de play.
        self.label_estado_radio.setStyleSheet(
            "color: rgba(255,255,255,130); font-size: 10px; background: transparent;"
        )

    def alternar_modo_compacto(self):
        """Entra o sale del modo compacto. Se dispara al pulsar sobre la
        nota musical (o sobre la portada del álbum, que ocupan el mismo
        widget), tanto para encoger como para volver al tamaño normal.

        El cambio va animado: la ventana viaja de una geometría a la otra
        mientras una foto de la tarjeta de antes se funde con la de
        después. Mientras dura, la tarjeta real está escondida."""
        if self.anim_tamano.state() == QVariantAnimation.State.Running:
            return  # un clic a media animación no debe encadenar otra

        geometria_antes = self.geometry()
        foto_antes = self.tarjeta.grab()

        self.modo_compacto = not self.modo_compacto
        self._actualizar_titulo_clicable()

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

        # La tarjeta ya está montada en su nueva forma, pero todavía sin
        # recolocar: hay que forzar el layout antes de retratarla.
        layout_tarjeta = self.tarjeta.layout()
        if layout_tarjeta is not None:
            layout_tarjeta.activate()
        self._animar_cambio_de_tamano(geometria_antes, foto_antes)

    def _animar_cambio_de_tamano(self, geometria_antes, foto_antes):
        geometria_despues = self.geometry()
        foto_despues = self.tarjeta.grab()

        self.lienzo_transicion.preparar(foto_antes, foto_despues)
        self.tarjeta.hide()
        self.lienzo_transicion.show()
        self.lienzo_transicion.raise_()

        # La ventana tiene tamaño fijo; para poder moverla de una medida a
        # otra hay que soltarle las amarras y volver a atarlas al final.
        self.setMinimumSize(0, 0)
        self.setMaximumSize(LADO_MAXIMO_VENTANA, LADO_MAXIMO_VENTANA)
        self.setGeometry(geometria_antes)
        self.lienzo_transicion.setGeometry(0, 0, self.width(), self.height())

        self._geometria_origen = geometria_antes
        self._geometria_destino = geometria_despues
        self.anim_tamano.stop()
        self.anim_tamano.start()

    def _al_avanzar_tamano(self, valor):
        avance = float(valor)
        origen, destino = self._geometria_origen, self._geometria_destino
        if origen is None or destino is None:
            return

        def entre(a, b):
            return round(a + (b - a) * avance)

        self.setGeometry(
            entre(origen.x(), destino.x()),
            entre(origen.y(), destino.y()),
            entre(origen.width(), destino.width()),
            entre(origen.height(), destino.height()),
        )
        self.lienzo_transicion.setGeometry(0, 0, self.width(), self.height())
        self.lienzo_transicion.establecer_avance(avance)

    def _al_terminar_tamano(self):
        destino = self._geometria_destino
        if destino is not None:
            self.setGeometry(destino)
            self.setFixedSize(destino.size())
        self.lienzo_transicion.hide()
        self.lienzo_transicion.soltar_fotos()
        self.tarjeta.show()
        self._geometria_origen = None
        self._geometria_destino = None

    def _aplicar_estilo_play(self, tamano):
        # El tamaño se recuerda porque los dos layouts (normal y compacto)
        # usan uno distinto, y al cambiar de color hay que repintar el
        # botón con el que tenga puesto en ese momento.
        self._tamano_play = tamano
        self.boton_play.setFixedSize(tamano, tamano)
        self.boton_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {acento_css()};
                color: white;
                border-radius: {tamano // 2}px;
                font-size: {int(tamano * 0.36)}px;
            }}
            QPushButton:hover {{ background-color: {css(aclarar(COLOR_ACENTO, 0.14))}; }}
            QPushButton:pressed {{ background-color: {css(oscurecer(COLOR_ACENTO))}; }}
        """)

    def _aplicar_estilo_icono(self, boton, tamano):
        self._tamanos_icono[boton] = tamano
        boton.setFixedSize(tamano, tamano)
        boton.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255,255,255,22);
                color: white;
                border-radius: {tamano // 2}px;
                font-size: {int(tamano * 0.4)}px;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,45); }}
            QPushButton:pressed {{ background-color: {acento_css()}; }}
        """)

    def actualizar_estilo_progreso(self, posicion):
        """Evita que Qt pinte un pequeño tramo blanco cuando el valor es cero."""
        mostrar_progreso = posicion > self.slider_progreso.minimum()
        if mostrar_progreso == self._progreso_visible:
            return
        self._progreso_visible = mostrar_progreso
        color_progreso = acento_css() if mostrar_progreso else "transparent"
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

    def _crear_atajos(self):
        """Los atajos de cerrar, a mano.

        Sin barra de menús, macOS no cablea ⌘Q por su cuenta, y ahora
        que la tarjeta no tiene botón de cerrar hace falta que el atajo
        de toda la vida funcione seguro. En Windows, Qt deja 'Quit' sin
        teclas: allí cierra Alt+F4, que lo lleva el sistema.
        """
        for estandar in (QKeySequence.StandardKey.Quit, QKeySequence.StandardKey.Close):
            secuencia = QKeySequence(estandar)
            if secuencia.isEmpty():
                continue
            QShortcut(secuencia, self).activated.connect(self.close)

    def keyPressEvent(self, event):
        """Atajos de la ventana.

        Solo llegan aquí las teclas que ningún hijo con el foco se haya
        quedado antes: mientras se escribe en el buscador, el espacio y
        las flechas son suyos y esto ni se entera.
        """
        tecla = event.key()

        if tecla == Qt.Key.Key_Space or tecla in self.TECLAS_PLAY_PAUSA:
            self._pulsar_play()
        elif tecla in (Qt.Key.Key_Left, Qt.Key.Key_MediaPrevious):
            self._pulsar_anterior()
        elif tecla in (Qt.Key.Key_Right, Qt.Key.Key_MediaNext):
            self._pulsar_siguiente()
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
            if es_audio(ruta):
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
        self._cargar_musica_de_carpeta(carpeta, seleccionar_ruta=cancion)

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

        self._cargar_musica_de_carpeta(carpeta, seleccionar_ruta=cancion or None)

        # El salto al minuto se aplaza: hasta que el archivo no está
        # cargado del todo, setPosition() no tiene efecto. Lo hace
        # 'al_cambiar_estado_medio' en cuanto el medio está listo.
        if cancion and posicion > 0 and self.canciones and \
                0 <= self.indice_actual < len(self.canciones) and \
                self.canciones[self.indice_actual] == cancion:
            self._posicion_pendiente = posicion

    def _actualizar_titulo_clicable(self):
        """El título hace de botón de elegir carpeta solo cuando tiene
        sentido: escuchando música y con la tarjeta grande. En la radio
        no pinta nada. Y en el modo compacto tampoco, aunque suene
        música: es una tarjeta pequeña que se agarra para moverla o para
        agrandarla, y el título ocupa justo donde uno la coge; acababa
        abriéndose el selector de carpetas sin querer."""
        self.label_titulo.establecer_clicable(
            not self.modo_compacto and self.emisora_actual is None
        )

    def elegir_carpeta(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, TEXTOS["dialogo_carpeta"]
        )
        if not carpeta:
            return
        self._cargar_musica_de_carpeta(carpeta)

    def _cargar_musica_de_carpeta(self, carpeta, seleccionar_ruta=None):
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

        self.canciones = self._leer_canciones_de(carpeta)
        self._archivos_carpeta = list(self.canciones)
        self._vigilar_carpeta(carpeta)

        # El historial guarda posiciones dentro de 'canciones', y esa lista
        # acaba de cambiar: conservarlo apuntaría a canciones equivocadas.
        self.historial.clear()
        self.pila_siguientes.clear()

        self._rellenar_lista_canciones()

        if not self.canciones:
            self.label_titulo.establecer_texto(TEXTOS["sin_musica"])
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

    @staticmethod
    def _leer_canciones_de(carpeta):
        """Las rutas de la música que hay en la carpeta, en orden."""
        return [
            os.path.join(carpeta, f)
            for f in sorted(os.listdir(carpeta))
            if es_audio(f)
        ]

    def _vigilar_carpeta(self, carpeta):
        """Deja al vigilante mirando solo esta carpeta."""
        anteriores = self.vigilante_carpeta.directories()
        if anteriores:
            self.vigilante_carpeta.removePaths(anteriores)
        self.vigilante_carpeta.addPath(carpeta)

    def _al_cambiar_carpeta_en_disco(self, _ruta):
        # Se espera a que amaine: ver ESPERA_REFRESCO_CARPETA_MS.
        self._temporizador_refresco_carpeta.start()

    def _refrescar_carpeta(self):
        """La carpeta ha cambiado en disco: se relee y, si lo que cambió
        fue la música, la lista se pone al día sin cortar lo que suena."""
        carpeta = self.carpeta_actual
        if not (carpeta and os.path.isdir(carpeta)):
            return
        try:
            archivos = self._leer_canciones_de(carpeta)
        except OSError:
            return
        if archivos == self._archivos_carpeta:
            return  # cambió otra cosa: una carátula, un .txt, una subcarpeta

        # Hay que decidirlo ANTES de actualizar la foto de la carpeta: la
        # cola es la carpeta si coincide con lo que había en ella, y si
        # no es que se está escuchando una lista.
        cola_era_la_carpeta = self.canciones == self._archivos_carpeta
        self._archivos_carpeta = archivos

        # La rejilla de listas se lee del disco cada vez que se pinta,
        # así que basta con repintarla si es lo que se está viendo.
        if self.stack_superior.currentIndex() == self.PANTALLA_LISTAS:
            self._pintar_listas()

        if cola_era_la_carpeta:
            self._sustituir_cola(archivos)
        # Y si no, la cola es de una lista y no se toca: "siguiente" debe
        # seguir andando por la lista, no por la carpeta.

    def _sustituir_cola(self, nuevas):
        """Cambia la cola por 'nuevas' conservando todo lo que se pueda:
        la canción cargada sigue cargada, el historial sigue apuntando a
        las mismas canciones -aunque hayan cambiado de sitio- y el filtro
        del buscador sigue aplicado."""
        antes = self.canciones
        ruta_actual = (
            antes[self.indice_actual]
            if 0 <= self.indice_actual < len(antes) else None
        )

        # El historial guarda posiciones, y la lista se ha reordenado: se
        # traducen a través de las rutas, tirando las que ya no existen.
        posicion_nueva = {ruta: i for i, ruta in enumerate(nuevas)}

        def remapear(indices):
            return [
                posicion_nueva[antes[i]]
                for i in indices
                if 0 <= i < len(antes) and antes[i] in posicion_nueva
            ]

        self.historial = remapear(self.historial)
        self.pila_siguientes = remapear(self.pila_siguientes)

        self.canciones = nuevas
        self._rellenar_lista_canciones()
        self._filtrar_lista_canciones(self._filtro_del_buscador())

        if not nuevas:
            self.indice_actual = -1
            self.label_titulo.establecer_texto(TEXTOS["sin_musica"])
            return

        if ruta_actual in posicion_nueva:
            # Sigue estando: solo se vuelve a marcar en la lista nueva,
            # sin recargarla en el reproductor, que cortaría el audio.
            self.indice_actual = posicion_nueva[ruta_actual]
            self.lista_canciones.setCurrentRow(self.indice_actual)
            self._actualizar_indicadores_ecualizador()
            return

        # La canción cargada ya no está en la carpeta (o no había ninguna,
        # porque la carpeta estaba vacía): se deja preparada la más
        # cercana, sin que arranque sola.
        self.indice_actual = min(max(self.indice_actual, 0), len(nuevas) - 1)
        if self.emisora_actual is not None:
            # En la radio no se carga nada: eso sacaría de la emisora.
            self.lista_canciones.setCurrentRow(self.indice_actual)
            return
        self.cargar_cancion(self.indice_actual, reproducir=False)

    def _filtro_del_buscador(self):
        """Lo que hay escrito en el buscador, si está filtrando la lista.
        En modo descarga el texto es una consulta a YouTube, no un filtro."""
        if self.control_descargas is not None and self.control_descargas.modo_descarga:
            return ""
        return self.campo_busqueda.text()

    def _rellenar_lista_canciones(self):
        """Vuelca 'self.canciones' en la lista de la pantalla."""
        self.lista_canciones.clear()
        self._filas_canciones = []
        for ruta in self.canciones:
            titulo = os.path.splitext(os.path.basename(ruta))[0]
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ALTO_FILA_CANCION))
            self.lista_canciones.addItem(item)
            fila = FilaCancion(titulo, con_anadir=True)
            fila.anadir.connect(lambda r=ruta: self.elegir_lista_para(r))
            self.lista_canciones.setItemWidget(item, fila)
            self._filas_canciones.append(fila)

    def cargar_cola(self, rutas, indice=0, reproducir=True):
        """Cambia lo que hay en la cola -lo que recorren 'siguiente' y
        'anterior'- sin tocar la carpeta elegida. Es lo que pasa al
        reproducir desde una lista: a partir de ahi se anda por la lista
        y no por toda la carpeta."""
        self.canciones = list(rutas)
        # El historial guarda posiciones dentro de 'canciones', y esa
        # lista acaba de cambiar entera.
        self.historial.clear()
        self.pila_siguientes.clear()
        self._rellenar_lista_canciones()
        if not self.canciones:
            self.indice_actual = -1
            return
        self.indice_actual = max(0, min(indice, len(self.canciones) - 1))
        self.cargar_cancion(self.indice_actual, reproducir=reproducir)

    # ---------------- Las pantallas del panel superior ----------------
    # El panel de arriba es "la pantalla" del aparato. Enseña la nota
    # mientras suena la música y, al pulsar el botón de la lista, se
    # convierte en las canciones. Desde ahí se sube al menú con el
    # chevron, y del menú se entra a los ajustes: se navega hacia dentro
    # y hacia fuera, como en un reproductor físico.

    def _crear_barra_busqueda(self, color_panel_css, icono_izquierda, marcador):
        """La cabecera de las pantallas que son una lista: chevron para
        volver y una píldora de búsqueda. La usan tanto las canciones como
        la radio, para que las dos se vean exactamente igual.

        Devuelve (barra, botón de volver, campo de texto); quien la pide
        engancha las señales que necesite.
        """
        barra = QWidget()
        barra.setObjectName("barra_busqueda")
        barra.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        barra.setFixedHeight(ALTO_BARRA_BUSQUEDA)
        barra.setStyleSheet(f"""
            #barra_busqueda {{
                background-color: {color_panel_css};
                border-top-left-radius: {RADIO_ESQUINAS}px;
                border-top-right-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout_barra = QHBoxLayout(barra)
        # Más margen arriba que abajo: así la píldora se separa del borde
        # superior redondeado y queda pegada a la lista que va debajo.
        layout_barra.setContentsMargins(4, 7, 8, 3)
        layout_barra.setSpacing(2)

        boton_atras = BotonAtras()
        layout_barra.addWidget(boton_atras, 0, Qt.AlignmentFlag.AlignVCenter)

        pildora = QWidget()
        pildora.setObjectName("contenedor_busqueda")
        pildora.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pildora.setFixedHeight(28)
        RADIO_PILDORA = 14  # mitad del alto (28) -> píldora bien redondeada
        pildora.setStyleSheet(f"""
            #contenedor_busqueda {{
                background-color: rgba(255, 255, 255, 20);
                border: none;
                border-radius: {RADIO_PILDORA}px;
            }}
        """)
        layout_pildora = QHBoxLayout(pildora)
        layout_pildora.setContentsMargins(2, 0, 12, 0)
        layout_pildora.setSpacing(6)
        layout_pildora.addWidget(icono_izquierda, 0, Qt.AlignmentFlag.AlignVCenter)

        campo = QLineEdit()
        campo.setFrame(False)
        campo.setPlaceholderText(marcador)
        campo.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: white;
                font-size: 11px;
                padding: 0px;
                selection-background-color: rgba(255,255,255,70);
            }
        """)
        layout_pildora.addWidget(campo, 1)
        layout_barra.addWidget(pildora, 1, Qt.AlignmentFlag.AlignVCenter)

        return barra, boton_atras, campo

    def _construir_pantalla_menu(self, color_panel_css):
        pagina = QWidget()
        pagina.setObjectName("pantalla_menu")
        pagina.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Las esquinas se redondean con la hoja de estilo y no con una
        # máscara: la máscara recorta por píxeles enteros y deja el borde
        # con dientes de sierra, cosa que ya nos pasó en la tarjeta.
        pagina.setStyleSheet(f"""
            #pantalla_menu {{
                background-color: {color_panel_css};
                border-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # El botón de apagado vive aquí, en la portada del menú, y no en
        # la tarjeta: la app ya no tiene botón de cerrar, y este es el
        # sitio donde uno va a buscar lo que no es reproducir música.
        self.cabecera_menu = CabeceraPantalla(
            "Digilogic", accion_icono="apagar", reloj=True
        )
        self.cabecera_menu.setToolTip(TEXTOS["salir"])
        self.cabecera_menu.accion.connect(self.close)
        layout.addWidget(self.cabecera_menu)

        self.fila_menu_canciones = FilaMenu(TEXTOS["canciones"])
        self.fila_menu_canciones.clicked.connect(
            lambda: self.ir_a_pantalla(self.PANTALLA_LISTA)
        )
        layout.addWidget(self.fila_menu_canciones)

        self.fila_menu_listas = FilaMenu(TEXTOS["listas"])
        self.fila_menu_listas.clicked.connect(self.entrar_en_listas)
        layout.addWidget(self.fila_menu_listas)

        self.fila_menu_radio = FilaMenu(TEXTOS["radio"])
        self.fila_menu_radio.clicked.connect(self.entrar_en_radio)
        layout.addWidget(self.fila_menu_radio)

        self.fila_menu_estadisticas = FilaMenu(TEXTOS["estadisticas"])
        self.fila_menu_estadisticas.clicked.connect(self.entrar_en_estadisticas)
        layout.addWidget(self.fila_menu_estadisticas)

        self.fila_menu_ajustes = FilaMenu(TEXTOS["ajustes"])
        self.fila_menu_ajustes.clicked.connect(
            lambda: self.ir_a_pantalla(self.PANTALLA_AJUSTES)
        )
        layout.addWidget(self.fila_menu_ajustes)

        layout.addStretch()
        return pagina

    def _construir_pantalla_ajustes(self, color_panel_css):
        pagina = QWidget()
        pagina.setObjectName("pantalla_ajustes")
        pagina.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pagina.setStyleSheet(f"""
            #pantalla_ajustes {{
                background-color: {color_panel_css};
                border-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cabecera_ajustes = CabeceraPantalla(TEXTOS["ajustes"], con_atras=True)
        self.cabecera_ajustes.atras.connect(lambda: self.ir_a_pantalla(self.PANTALLA_MENU))
        layout.addWidget(self.cabecera_ajustes)

        # Sin rótulo: seis círculos de colores debajo de "Ajustes" ya
        # dicen lo que son sin necesidad de que nadie los presente.
        layout.addStretch()

        # Dos filas de tres, y grandes: en una sola fila los seis círculos
        # se quedaban pequeños y dejaban media pantalla vacía.
        rejilla_colores = QWidget()
        layout_colores = QGridLayout(rejilla_colores)
        layout_colores.setContentsMargins(0, 0, 0, 0)
        layout_colores.setHorizontalSpacing(14)
        layout_colores.setVerticalSpacing(4)
        self.muestras_color = []
        for i, color in enumerate(COLORES_ACENTO):
            muestra = MuestraColor(color)
            muestra.establecer_elegido(color.rgb() == COLOR_ACENTO.rgb())
            # 'c=color' congela el color de esta vuelta del bucle; sin eso
            # los seis botones acabarían apuntando al último.
            muestra.clicked.connect(
                lambda _=False, c=color, n=i: (
                    self.aplicar_color_acento(c), self._registrar_toque_color(n)
                )
            )
            layout_colores.addWidget(muestra, i // 3, i % 3)
            self.muestras_color.append(muestra)
        layout.addWidget(rejilla_colores, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch()

        self.fila_siempre_visible = FilaInterruptor(TEXTOS["siempre_visible"])
        self.fila_siempre_visible.establecer_activo(self.siempre_visible, animar=False)
        self.fila_siempre_visible.clicked.connect(self.alternar_siempre_visible)
        layout.addWidget(self.fila_siempre_visible)
        # Aire hasta el borde: pegada abajo del todo, la esquina redonda
        # del panel se comería una punta de la fila al encenderse.
        layout.addSpacing(10)
        return pagina

    def _construir_pantalla_juego(self, color_panel_css):
        pagina = QWidget()
        pagina.setObjectName("pantalla_juego")
        pagina.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pagina.setStyleSheet(f"""
            #pantalla_juego {{
                background-color: {color_panel_css};
                border-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # El título de la cabecera es el marcador: así no hace falta
        # traducir la palabra "puntos" a catorce idiomas para enseñar un
        # número que se entiende solo.
        self.cabecera_juego = CabeceraPantalla("0", con_atras=True)
        self.cabecera_juego.atras.connect(self._salir_del_juego)
        layout.addWidget(self.cabecera_juego)

        self.juego = JuegoRitmo()
        self.juego.puntos_cambiados.connect(
            lambda puntos: self.cabecera_juego.establecer_titulo(
                QLocale.system().toString(puntos)
            )
        )
        self.juego.record_batido.connect(
            lambda record: self.ajustes.setValue(CLAVE_RECORD_JUEGO, record)
        )
        layout.addWidget(self.juego, 1)
        return pagina

    # ---------------- El juego escondido ----------------

    def _registrar_toque_color(self, indice):
        """Cada vez que se toca un color en Ajustes, por si es la clave."""
        esperado = COMBINACION_JUEGO[len(self._secuencia_color)]
        if indice == esperado:
            self._secuencia_color.append(indice)
            if len(self._secuencia_color) == len(COMBINACION_JUEGO):
                self._secuencia_color = []
                self._entrar_en_el_juego()
        else:
            # Se empieza de cero, salvo que este mismo toque valga ya
            # como primer paso de la clave.
            self._secuencia_color = [indice] if indice == COMBINACION_JUEGO[0] else []

    def jugando(self):
        """Si el juego está en pantalla, los tres botones son suyos."""
        return self.stack_superior.currentIndex() == self.PANTALLA_JUEGO

    def _entrar_en_el_juego(self):
        onda = self.portada.onda
        self.juego.establecer_partitura(
            onda.envolvente, onda.resolucion_envolvente, self.player.position()
        )
        self.juego.preparar(self.ajustes.value(CLAVE_RECORD_JUEGO, 0, type=int))
        self.ir_a_pantalla(self.PANTALLA_JUEGO)

    def _salir_del_juego(self):
        self.ir_a_pantalla(self.PANTALLA_AJUSTES)

    # Qué pantalla está "más adentro" que cuál. Marca el sentido del
    # deslizamiento: hacia dentro entra por la derecha, hacia fuera por la
    # izquierda, igual que en el menú de cualquier reproductor o teléfono.
    PROFUNDIDAD = {
        PANTALLA_MENU: 0,
        PANTALLA_LISTA: 1,
        PANTALLA_AJUSTES: 1,
        PANTALLA_RADIO: 1,
        PANTALLA_ESTADISTICAS: 1,
        PANTALLA_LISTAS: 1,
        PANTALLA_DETALLE_LISTA: 2,
        PANTALLA_JUEGO: 2,
    }

    def _construir_pantalla_listas(self, color_panel_css):
        pagina = QWidget()
        pagina.setObjectName("pantalla_listas")
        pagina.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pagina.setStyleSheet(f"""
            #pantalla_listas {{
                background-color: {color_panel_css};
                border-radius: {RADIO_ESQUINAS}px;
            }}
            QScrollArea {{ background: transparent; border: none; }}
        """)
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cabecera_listas = CabeceraPantalla(
            TEXTOS["listas"], con_atras=True, accion_icono="mas"
        )
        self.cabecera_listas.atras.connect(self._salir_de_listas)
        self.cabecera_listas.accion.connect(self._pedir_nombre_de_lista)
        layout.addWidget(self.cabecera_listas)

        # Campo para bautizar una lista nueva. Vive escondido hasta que
        # se pulsa el "+": un diálogo del sistema aquí desentonaría.
        self.campo_nueva_lista = QLineEdit()
        self.campo_nueva_lista.setPlaceholderText(TEXTOS["nueva_lista"])
        self.campo_nueva_lista.setFixedHeight(26)
        self.campo_nueva_lista.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,20);
                border: none;
                border-radius: 13px;
                color: white;
                font-size: 11px;
                margin: 4px 10px 0px 10px;
                padding: 0px 10px;
                selection-background-color: rgba(255,255,255,70);
            }
        """)
        self.campo_nueva_lista.returnPressed.connect(self._crear_lista)
        self.campo_nueva_lista.hide()
        layout.addWidget(self.campo_nueva_lista)

        self.area_listas = QScrollArea()
        self.area_listas.setWidgetResizable(True)
        self.area_listas.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area_listas.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area_listas.setFrameShape(QFrame.Shape.NoFrame)

        self.rejilla_listas_widget = QWidget()
        self.rejilla_listas_widget.setStyleSheet("background: transparent;")
        self.rejilla_listas = QGridLayout(self.rejilla_listas_widget)
        self.rejilla_listas.setContentsMargins(10, 8, 10, 10)
        self.rejilla_listas.setHorizontalSpacing(9)
        self.rejilla_listas.setVerticalSpacing(8)
        self.rejilla_listas.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.area_listas.setWidget(self.rejilla_listas_widget)
        # Los mismos degradados que las listas de canciones: sin ellos, la
        # segunda fila de tarjetas se corta a hachazos contra el borde.
        self._poner_desvanecidos(self.area_listas, Portada.COLOR_FONDO_PANEL)
        layout.addWidget(self.area_listas, 1)
        return pagina

    def _construir_pantalla_detalle_lista(self, color_panel_css, color_panel):
        pagina = QWidget()
        pagina.setObjectName("pantalla_detalle_lista")
        pagina.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pagina.setStyleSheet(f"""
            #pantalla_detalle_lista {{
                background-color: {color_panel_css};
                border-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cabecera_detalle = CabeceraPantalla("", con_atras=True)
        self.cabecera_detalle.atras.connect(
            lambda: self.ir_a_pantalla(self.PANTALLA_LISTAS)
        )
        self.cabecera_detalle.accion.connect(self._borrar_lista_abierta)
        layout.addWidget(self.cabecera_detalle)

        self.lista_detalle = QListWidget()
        self.lista_detalle.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_detalle.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_detalle.itemDoubleClicked.connect(self._reproducir_de_lista)
        self._poner_desvanecidos(self.lista_detalle, color_panel)
        layout.addWidget(self.lista_detalle, 1)
        return pagina

    # ---------------- Listas ----------------
    # Hay de dos clases y conviven en la misma rejilla. Las subcarpetas de
    # la carpeta de música son listas por el mero hecho de existir, no se
    # guardan en ningún sitio y se releen del disco cada vez. Las otras se
    # crean aquí dentro y viven en los ajustes, para quien tiene dos mil
    # canciones sueltas y no quiere ponerse a mover archivos.

    @staticmethod
    def _vaciar_rejilla(layout):
        """Tira los widgets que hubiera dentro. No vale '_vaciar_layout',
        que los devuelve a la tarjeta porque está pensado para el cambio
        de modo; aquí las tarjetas viejas se rehacen enteras y hay que
        deshacerse de ellas o se van acumulando invisibles."""
        while layout.count():
            elemento = layout.takeAt(0)
            widget = elemento.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _leer_listas(self):
        try:
            datos = json.loads(self.ajustes.value(CLAVE_LISTAS, "{}", type=str))
        except ValueError:
            return {}
        if not isinstance(datos, dict):
            return {}
        return {n: [r for r in rutas if isinstance(r, str)]
                for n, rutas in datos.items() if isinstance(rutas, list)}

    def _guardar_listas(self, listas):
        self.ajustes.setValue(CLAVE_LISTAS, json.dumps(listas, ensure_ascii=False))

    def _listas_de_subcarpetas(self):
        """Cada subcarpeta con MP3 dentro es una lista, sin más."""
        if not self.carpeta_actual or not os.path.isdir(self.carpeta_actual):
            return []
        encontradas = []
        try:
            for nombre in sorted(os.listdir(self.carpeta_actual)):
                ruta = os.path.join(self.carpeta_actual, nombre)
                if not os.path.isdir(ruta):
                    continue
                canciones = [
                    os.path.join(ruta, f)
                    for f in sorted(os.listdir(ruta))
                    if es_audio(f)
                ]
                if canciones:
                    encontradas.append((nombre, canciones, False))
        except OSError:
            pass
        return encontradas

    def _todas_las_listas(self):
        """Lo que se ve en la rejilla, en orden: primero 'Todas', luego lo
        que has creado tú, y al final las subcarpetas."""
        listas = []
        if self.carpeta_actual and os.path.isdir(self.carpeta_actual):
            todas = [
                os.path.join(self.carpeta_actual, f)
                for f in sorted(os.listdir(self.carpeta_actual))
                if es_audio(f)
            ]
            if todas:
                listas.append((TEXTOS["todas"], todas, False))
        propias = self._leer_listas()
        listas += [(n, propias[n], True) for n in sorted(propias)]
        listas += self._listas_de_subcarpetas()
        return listas

    def entrar_en_listas(self):
        self.modo_elegir_lista = None
        self._pintar_listas()
        self.ir_a_pantalla(self.PANTALLA_LISTAS)

    def _salir_de_listas(self):
        # Si se llegó aquí para añadir una canción, el chevron vuelve a
        # las canciones, que es de donde se venía.
        destino = (
            self.PANTALLA_LISTA if self.modo_elegir_lista else self.PANTALLA_MENU
        )
        self.modo_elegir_lista = None
        self.ir_a_pantalla(destino)

    def _pintar_listas(self):
        eligiendo = self.modo_elegir_lista is not None
        self.cabecera_listas.establecer_titulo(
            TEXTOS["elige_lista"] if eligiendo else TEXTOS["listas"]
        )
        # El "+" sigue estando al elegir dónde meter una canción: puede
        # que la lista que quieres todavía no exista, y mandarte a crearla
        # a otra pantalla para volver luego sería absurdo.
        self.cabecera_listas.update()

        self._vaciar_rejilla(self.rejilla_listas)
        listas = self._todas_las_listas()
        if eligiendo:
            # No tiene sentido ofrecer 'Todas' ni las subcarpetas: en unas
            # no se puede meter nada y en las otras habría que mover el
            # archivo de sitio.
            listas = [(n, r, propia) for n, r, propia in listas if propia]

        if not listas:
            aviso = QLabel(TEXTOS["sin_listas"])
            aviso.setAlignment(Qt.AlignmentFlag.AlignCenter)
            aviso.setWordWrap(True)
            aviso.setStyleSheet(
                "font-size: 11px; color: rgba(255,255,255,110); background: transparent;"
            )
            self.rejilla_listas.addWidget(aviso, 0, 0, 1, 3)
            return

        for indice, (nombre, rutas, propia) in enumerate(listas):
            tarjeta = TarjetaLista(nombre, rutas)
            tarjeta.clicked.connect(
                lambda _=False, n=nombre, r=rutas, p=propia: self._tocar_lista(n, r, p)
            )
            self.rejilla_listas.addWidget(tarjeta, indice // 3, indice % 3)

    def _tocar_lista(self, nombre, rutas, propia):
        if self.modo_elegir_lista is not None:
            self._anadir_a_lista(nombre)
            return
        self.lista_abierta = (nombre, rutas, propia)
        self.cabecera_detalle.establecer_titulo(nombre)
        # Solo las listas propias se pueden borrar: una subcarpeta es del
        # disco, y borrarla sería borrar archivos de verdad.
        self.cabecera_detalle.accion_icono = "papelera" if propia else None

        self.lista_detalle.clear()
        for ruta in rutas:
            titulo, artista = extraer_metadatos(ruta)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ALTO_FILA_CANCION))
            self.lista_detalle.addItem(item)
            self.lista_detalle.setItemWidget(
                item, FilaCancion(f"{titulo} — {artista}" if artista else titulo)
            )
        self.ir_a_pantalla(self.PANTALLA_DETALLE_LISTA)

    def _reproducir_de_lista(self, item):
        if self.lista_abierta is None:
            return
        _, rutas, _ = self.lista_abierta
        indice = self.lista_detalle.row(item)
        if not 0 <= indice < len(rutas):
            return
        # La lista pasa a ser la cola: a partir de aquí, "siguiente" se
        # mueve dentro de ella y no por toda la carpeta.
        self.cargar_cola(rutas, indice)
        self.stack_superior.setCurrentIndex(self.PANTALLA_PORTADA)

    def _pedir_nombre_de_lista(self):
        if self.campo_nueva_lista.isVisible():
            self.campo_nueva_lista.hide()
            return
        self.campo_nueva_lista.clear()
        self.campo_nueva_lista.show()
        self.campo_nueva_lista.setFocus()

    def _crear_lista(self):
        nombre = " ".join(self.campo_nueva_lista.text().split())
        self.campo_nueva_lista.hide()
        if not nombre:
            return
        listas = self._leer_listas()
        listas.setdefault(nombre, [])
        self._guardar_listas(listas)
        if self.modo_elegir_lista is not None:
            # Se estaba buscando dónde meter una canción: la lista que
            # acabas de crear es justamente para eso.
            self._anadir_a_lista(nombre)
            return
        self._pintar_listas()

    def _borrar_lista_abierta(self):
        if self.lista_abierta is None or not self.lista_abierta[2]:
            return
        listas = self._leer_listas()
        listas.pop(self.lista_abierta[0], None)
        self._guardar_listas(listas)
        self.lista_abierta = None
        self._pintar_listas()
        self.ir_a_pantalla(self.PANTALLA_LISTAS)

    def elegir_lista_para(self, ruta):
        """Desde el '+' de una canción: la rejilla pasa a modo elegir."""
        self.modo_elegir_lista = ruta
        self._pintar_listas()
        self.ir_a_pantalla(self.PANTALLA_LISTAS)

    def _anadir_a_lista(self, nombre):
        ruta = self.modo_elegir_lista
        self.modo_elegir_lista = None
        listas = self._leer_listas()
        if ruta and nombre in listas and ruta not in listas[nombre]:
            listas[nombre].append(ruta)
            self._guardar_listas(listas)
        self.ir_a_pantalla(self.PANTALLA_LISTA)

    def _construir_pantalla_estadisticas(self, color_panel_css, color_panel):
        pagina = QWidget()
        # Sin esto, la franja de la cabecera dejaba ver el color de la
        # tarjeta -mas oscuro- en vez del color del panel, y se notaba un
        # escalon justo encima de la lista.
        pagina.setObjectName("pantalla_estadisticas")
        pagina.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pagina.setStyleSheet(f"""
            #pantalla_estadisticas {{
                background-color: {color_panel_css};
                border-radius: {RADIO_ESQUINAS}px;
            }}
        """)
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cabecera_estadisticas = CabeceraPantalla(
            TEXTOS["estadisticas"], con_atras=True
        )
        self.cabecera_estadisticas.atras.connect(
            lambda: self.ir_a_pantalla(self.PANTALLA_MENU)
        )
        layout.addWidget(self.cabecera_estadisticas)

        self.lista_estadisticas = QListWidget()
        self.lista_estadisticas.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_estadisticas.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_estadisticas.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._poner_desvanecidos(self.lista_estadisticas, color_panel)
        layout.addWidget(self.lista_estadisticas, 1)
        return pagina

    def _construir_pantalla_radio(self, color_panel_css, color_panel):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        barra, self.boton_atras_radio, self.campo_radio = self._crear_barra_busqueda(
            color_panel_css, IconoLupa(lado=24), TEXTOS["buscar_emisoras"]
        )
        self.boton_atras_radio.clicked.connect(
            lambda: self.ir_a_pantalla(self.PANTALLA_MENU)
        )
        # Aquí sí hace falta confirmar con Enter: cada pulsación sería una
        # petición al directorio, y no está bien tratar así a un servicio
        # gratuito.
        self.campo_radio.returnPressed.connect(self.buscar_emisoras)
        layout.addWidget(barra)

        self.lista_emisoras = QListWidget()
        self.lista_emisoras.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_emisoras.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista_emisoras.itemDoubleClicked.connect(self.reproducir_emisora_seleccionada)
        self._poner_desvanecidos(self.lista_emisoras, color_panel)
        layout.addWidget(self.lista_emisoras, 1)

        ruta_mascara = QPainterPath()
        ruta_mascara.addRoundedRect(
            QRectF(0, 0, ANCHO_ZONA_SUPERIOR, ALTO_ZONA_SUPERIOR),
            RADIO_ESQUINAS, RADIO_ESQUINAS,
        )
        pagina.setMask(QRegion(ruta_mascara.toFillPolygon().toPolygon()))
        return pagina

    def _poner_desvanecidos(self, lista, color_panel):
        """Los degradados de arriba y abajo que ya tiene la lista de
        canciones, para una lista cualquiera."""
        arriba = DesvanecidoBorde(arriba=True, color_fondo=color_panel)
        arriba.setParent(lista)
        arriba.hide()

        abajo = DesvanecidoBorde(arriba=False, color_fondo=color_panel)
        abajo.setParent(lista)

        barra = lista.verticalScrollBar()

        def actualizar(*_):
            # La geometría se recalcula aquí y no una sola vez al crearlos:
            # cada pantalla le deja a su lista un alto distinto, y al
            # crearlos todavía no se sabe cuál.
            ancho, alto = lista.width(), lista.height()
            arriba.setGeometry(0, 0, ancho, ALTURA_DESVANECIDO)
            abajo.setGeometry(0, alto - ALTURA_DESVANECIDO, ancho, ALTURA_DESVANECIDO)
            arriba.setVisible(barra.value() > barra.minimum())
            abajo.setVisible(barra.value() < barra.maximum())

        barra.valueChanged.connect(actualizar)
        barra.rangeChanged.connect(actualizar)
        actualizar()

    def ir_a_pantalla(self, indice, animar=True):
        """Cambia de pantalla y recuerda en cuál se quedó, para que el
        botón de la lista devuelva a la última y no siempre a la misma."""
        anterior = self.stack_superior.currentIndex()
        self._pantalla_panel = indice
        if indice == anterior:
            return

        # Sin animación si el panel no se está viendo (estamos en la nota)
        # o si el cambio no es entre dos pantallas del menú: ahí no hay
        # ningún "dentro" ni "fuera" que contar.
        animable = (
            animar
            and anterior in self.PROFUNDIDAD
            and indice in self.PROFUNDIDAD
            and self.isVisible()
        )
        if not animable:
            self.stack_superior.setCurrentIndex(indice)
            return

        antes = self.stack_superior.currentWidget().grab()
        self.stack_superior.setCurrentIndex(indice)
        despues = self.stack_superior.currentWidget().grab()
        self.deslizador.deslizar(
            antes, despues,
            hacia_dentro=self.PROFUNDIDAD[indice] > self.PROFUNDIDAD[anterior],
        )

    def alternar_lista(self):
        if self.stack_superior.currentIndex() == self.PANTALLA_PORTADA:
            self.stack_superior.setCurrentIndex(self._pantalla_panel)
        else:
            self.stack_superior.setCurrentIndex(self.PANTALLA_PORTADA)

    # ---------------- Estadísticas ----------------
    # Se llevan desde la primera vez que se abre la app y no caducan ni se
    # borran solas. Viven en este ordenador y no salen de aquí: no se
    # envían a ningún sitio ni hay ningún sitio al que enviarlas.

    def _iniciar_estadisticas(self):
        if not self.ajustes.value(CLAVE_ESTAD_DESDE, "", type=str):
            self.ajustes.setValue(
                CLAVE_ESTAD_DESDE,
                QDate.currentDate().toString(Qt.DateFormat.ISODate),
            )
        # Lo de esta sesión se acumula en memoria y se vuelca de tanto en
        # tanto; así no se escribe en disco diez veces por segundo.
        self._ms_musica_sesion = 0
        self._ms_radio_sesion = 0
        self._canciones_sesion = 0
        self._cuentas_canciones_sesion = {}
        self._cuentas_emisoras_sesion = {}
        self._ms_sin_volcar = 0
        self._reiniciar_conteo_pista()

    def _reiniciar_conteo_pista(self):
        self._posicion_escucha_previa = None
        self._ms_pista_actual = 0
        self._pista_contada = False

    def _acumular_escucha(self, posicion):
        previa = self._posicion_escucha_previa
        self._posicion_escucha_previa = posicion
        if previa is None:
            return

        # Solo cuentan los avances pequeños y hacia delante: un salto en
        # la barra, un cambio de canción o volver de una pausa larga no
        # son tiempo escuchado.
        avance = posicion - previa
        if not 0 < avance <= 2_000:
            return

        if self.emisora_actual is not None:
            self._ms_radio_sesion += avance
        else:
            self._ms_musica_sesion += avance
        self._ms_sin_volcar += avance

        self._ms_pista_actual += avance
        if not self._pista_contada and self._ms_pista_actual >= MS_PARA_CONTAR_REPRODUCCION:
            self._pista_contada = True
            self._anotar_reproduccion()

        if self._ms_sin_volcar >= MS_ENTRE_VOLCADOS_ESTADISTICAS:
            self.volcar_estadisticas()

    def _anotar_reproduccion(self):
        if self.emisora_actual is not None:
            nombre = self.emisora_actual["nombre"]
            self._cuentas_emisoras_sesion[nombre] = (
                self._cuentas_emisoras_sesion.get(nombre, 0) + 1
            )
            return

        if not (self.canciones and 0 <= self.indice_actual < len(self.canciones)):
            return
        titulo, artista = extraer_metadatos(self.canciones[self.indice_actual])
        nombre = f"{titulo} — {artista}" if artista else titulo
        self._cuentas_canciones_sesion[nombre] = (
            self._cuentas_canciones_sesion.get(nombre, 0) + 1
        )
        self._canciones_sesion += 1

    def _leer_cuentas(self, clave):
        try:
            datos = json.loads(self.ajustes.value(clave, "{}", type=str))
        except ValueError:
            return {}
        return datos if isinstance(datos, dict) else {}

    def volcar_estadisticas(self):
        """Suma lo de esta sesión a lo ya guardado y deja los contadores
        de sesión a cero. Es idempotente a propósito: se puede llamar
        tantas veces como haga falta sin contar nada dos veces."""
        sumas = (
            (CLAVE_ESTAD_MS_MUSICA, "_ms_musica_sesion"),
            (CLAVE_ESTAD_MS_RADIO, "_ms_radio_sesion"),
            (CLAVE_ESTAD_CANCIONES, "_canciones_sesion"),
        )
        for clave, atributo in sumas:
            sesion = getattr(self, atributo)
            if sesion:
                self.ajustes.setValue(
                    clave, self.ajustes.value(clave, 0, type=int) + sesion
                )
                setattr(self, atributo, 0)

        recuentos = (
            (CLAVE_ESTAD_CUENTAS_CANCIONES, self._cuentas_canciones_sesion),
            (CLAVE_ESTAD_CUENTAS_EMISORAS, self._cuentas_emisoras_sesion),
        )
        for clave, cuentas in recuentos:
            if not cuentas:
                continue
            guardadas = self._leer_cuentas(clave)
            for nombre, veces in cuentas.items():
                guardadas[nombre] = guardadas.get(nombre, 0) + veces
            self.ajustes.setValue(clave, json.dumps(guardadas, ensure_ascii=False))
            cuentas.clear()

        self._ms_sin_volcar = 0

    def entrar_en_estadisticas(self):
        # Se vuelca antes de pintar para que lo que se está escuchando en
        # este momento ya salga en los números.
        self.volcar_estadisticas()
        self._pintar_estadisticas()
        self.ir_a_pantalla(self.PANTALLA_ESTADISTICAS)

    def _pintar_estadisticas(self):
        idioma = QLocale.system()
        ms_musica = self.ajustes.value(CLAVE_ESTAD_MS_MUSICA, 0, type=int)
        ms_radio = self.ajustes.value(CLAVE_ESTAD_MS_RADIO, 0, type=int)
        canciones = self.ajustes.value(CLAVE_ESTAD_CANCIONES, 0, type=int)

        desde = QDate.fromString(
            self.ajustes.value(CLAVE_ESTAD_DESDE, "", type=str), Qt.DateFormat.ISODate
        )
        if not desde.isValid():
            desde = QDate.currentDate()
        dias = max(1, desde.daysTo(QDate.currentDate()) + 1)

        filas = [
            (TEXTOS["tiempo_escuchado"], formatear_duracion_larga(ms_musica + ms_radio)),
            (TEXTOS["canciones_reproducidas"], idioma.toString(canciones)),
        ]

        mas_oida = self._mas_repetida(CLAVE_ESTAD_CUENTAS_CANCIONES)
        if mas_oida is not None:
            nombre, veces = mas_oida
            filas.append((
                # La cuenta va en el rótulo y no en el valor: un título
                # largo se recorta, y ahí se perdería.
                f"{TEXTOS['cancion_mas_escuchada']}  ·  {idioma.toString(veces)} {TEXTOS['veces']}",
                nombre,
            ))

        if ms_radio:
            filas.append((TEXTOS["tiempo_radio"], formatear_duracion_larga(ms_radio)))

        emisora = self._mas_repetida(CLAVE_ESTAD_CUENTAS_EMISORAS)
        if emisora is not None:
            nombre, veces = emisora
            filas.append((
                f"{TEXTOS['emisora_mas_escuchada']}  ·  {idioma.toString(veces)} {TEXTOS['veces']}",
                nombre,
            ))

        filas.append((
            TEXTOS["media_diaria"],
            formatear_duracion_larga((ms_musica + ms_radio) // dias),
        ))
        filas.append((
            TEXTOS["escuchando_desde"],
            idioma.toString(desde, QLocale.FormatType.LongFormat),
        ))

        self.lista_estadisticas.clear()
        for titulo, valor in filas:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, FilaEstadistica.ALTO))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lista_estadisticas.addItem(item)
            self.lista_estadisticas.setItemWidget(item, FilaEstadistica(titulo, valor))

    def _mas_repetida(self, clave):
        cuentas = self._leer_cuentas(clave)
        if not cuentas:
            return None
        nombre = max(cuentas, key=lambda k: cuentas[k])
        return nombre, cuentas[nombre]

    # ---------------- Radio ----------------
    # La radio es lo único de Digilogic que necesita internet. Todo lo
    # demás sigue funcionando igual sin conexión: si no hay red, esta
    # pantalla lo dice y ya está.

    def entrar_en_radio(self):
        self.ir_a_pantalla(self.PANTALLA_RADIO)
        # La lista se pide la primera vez que alguien abre la radio, no al
        # arrancar: quien no la use nunca no toca la red jamás.
        if not self.emisoras:
            self.cliente_radio.emisoras_del_pais()

    def buscar_emisoras(self):
        self.cliente_radio.buscar(self.campo_radio.text())

    def _al_llegar_emisoras(self, emisoras):
        self.emisoras = emisoras
        self._filas_emisoras = []
        self.lista_emisoras.clear()

        if not emisoras:
            self._poner_aviso_radio(TEXTOS["sin_emisoras"])
            return

        for emisora in emisoras:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ALTO_FILA_CANCION))
            fila = FilaEmisora(emisora)
            self.lista_emisoras.addItem(item)
            self.lista_emisoras.setItemWidget(item, fila)
            self._filas_emisoras.append(fila)

        if self.emisora_actual is not None:
            # La emisora que suena puede seguir estando en los resultados
            # nuevos; si es así, que se vea marcada.
            for i, emisora in enumerate(emisoras):
                if emisora["url"] == self.emisora_actual["url"]:
                    self.indice_emisora = i
                    self.lista_emisoras.setCurrentRow(i)
                    break
            else:
                self.indice_emisora = -1
        self._actualizar_indicadores_ecualizador()

    def _al_fallar_radio(self):
        self.emisoras = []
        self._filas_emisoras = []
        self.lista_emisoras.clear()
        self._poner_aviso_radio(TEXTOS["sin_conexion"])

    def _al_faltar_cifrado(self):
        """El equipo no sabe cifrar y la radio va por HTTPS. Decirlo tal
        cual: culpar a la conexión mandaría al usuario a mirar su router
        durante media hora para nada."""
        self.emisoras = []
        self._filas_emisoras = []
        self.lista_emisoras.clear()
        self._poner_aviso_radio(TEXTOS["radio_no_disponible"])

    def _poner_aviso_radio(self, texto):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, ALTO_FILA_CANCION))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        aviso = QLabel(texto)
        aviso.setAlignment(Qt.AlignmentFlag.AlignCenter)
        aviso.setStyleSheet(
            "font-size: 11px; color: rgba(255,255,255,110); background: transparent;"
        )
        self.lista_emisoras.addItem(item)
        self.lista_emisoras.setItemWidget(item, aviso)

    def reproducir_emisora_seleccionada(self, item):
        indice = self.lista_emisoras.row(item)
        if not 0 <= indice < len(self.emisoras):
            return
        # Igual que en las canciones: si ya está sonando, el doble clic
        # lleva al rótulo de la emisora en vez de recargar el directo.
        if self.emisora_actual is not None and indice == self.indice_emisora:
            self.stack_superior.setCurrentIndex(self.PANTALLA_PORTADA)
            return
        self.cargar_emisora(indice)

    def cargar_emisora(self, indice):
        emisora = self.emisoras[indice]

        # Antes de irse de la música local hay que anotar dónde se quedó:
        # al volver de la radio se reanuda ahí.
        self._guardar_punto_escucha()

        self._reiniciar_conteo_pista()
        # Un directo no se puede analizar por adelantado: ahi el orbe
        # sigue moviendose al azar.
        self.analizador_onda.cancelar()
        self.portada.onda.establecer_envolvente([], AnalizadorOnda.RESOLUCION_MS)
        self.emisora_actual = emisora
        self._actualizar_titulo_clicable()
        self.indice_emisora = indice
        self.player.setSource(QUrl(emisora["url"]))
        self.label_titulo.establecer_texto(emisora["nombre"])
        # La emisora manda en la pantalla: nada de carátulas, y la onda
        # pasa a latir dentro del rótulo en vez de dentro de la nota.
        self.portada.limpiar_portada()
        self.portada.mostrar_dial(texto_de_dial(emisora["nombre"]))
        self.lista_emisoras.setCurrentRow(indice)

        # Una emisora en directo no tiene ni duración ni minuto al que
        # saltar: la barra se queda a cero y el reloj de la derecha lo
        # dice con todas las letras.
        self._posicion_pendiente = None
        self.slider_progreso.setRange(0, 0)
        self._mostrar_estado_radio(TEXTOS["en_directo"])

        self.player.play()
        self.boton_play.establecer_simbolo("pausa")
        self.portada.onda.establecer_activa(True)
        self.label_titulo.establecer_reproduciendo(True)
        self._actualizar_indicadores_ecualizador()
        self._refrescar_centro_multimedia()

    def _mostrar_estado_radio(self, texto):
        """En la radio no hay dos tiempos que enseñar: el contador de la
        izquierda sobra -un directo no empieza en 0:00- y el de la derecha
        se centra para decir si estamos en directo o sin conexión."""
        self.label_tiempo_actual.hide()
        self.label_tiempo_total.hide()
        # En mayúsculas, como los indicadores de emisión de cualquier
        # radio. En los idiomas que no distinguen caja no cambia nada.
        self.label_estado_radio.setText(texto.upper())
        self.label_estado_radio.setGeometry(self.fila_tiempos.rect())
        self.label_estado_radio.show()
        self.label_estado_radio.raise_()

    def _restaurar_fila_tiempos(self):
        self.label_estado_radio.hide()
        self.label_tiempo_actual.show()
        self.label_tiempo_total.show()

    def _al_fallar_reproduccion(self, *_):
        """Un directo que no arranca o que se corta casi siempre es la
        conexión del usuario, no la emisora: decírselo le ahorra pensar
        que la app está rota."""
        if self.emisora_actual is not None:
            self._mostrar_estado_radio(TEXTOS["sin_conexion"])

    def _cambiar_emisora(self, salto):
        if not self.emisoras:
            return
        self.cargar_emisora((self.indice_emisora + salto) % len(self.emisoras))

    def _salir_de_la_radio(self):
        """Volver a la música local: se llama desde cargar_cancion."""
        self.emisora_actual = None
        self.indice_emisora = -1
        self._actualizar_titulo_clicable()
        self.portada.mostrar_nota()
        self._restaurar_fila_tiempos()
        self.label_tiempo_total.setText(formatear_tiempo(0))
        for fila in self._filas_emisoras:
            fila.indicador.establecer_estado(False, False)

    # ---------------- Color de acento ----------------

    def _color_acento_guardado(self):
        guardado = self.ajustes.value(CLAVE_COLOR_ACENTO, "", type=str)
        for color in COLORES_ACENTO:
            if color.name() == guardado:
                return color
        return COLOR_ACENTO_POR_DEFECTO

    def aplicar_color_acento(self, color, guardar=True):
        """Cambia el acento de toda la interfaz en caliente.

        El color vive en un único QColor global que se modifica en el
        sitio, así que lo que se pinta a mano (la nota al arrastrar, el
        menú) se entera solo. Lo que hay que rehacer es lo que Qt guarda
        en hojas de estilo, que se generaron con el color anterior.
        """
        COLOR_ACENTO.setRgb(*color.getRgb()[:3])

        self._aplicar_estilo_play(self._tamano_play)
        for boton, tamano in self._tamanos_icono.items():
            self._aplicar_estilo_icono(boton, tamano)
        self.boton_lista.actualizar_estilo()
        self.boton_aleatorio._actualizar_estilo()

        # El estilo del progreso solo se rehace cuando cambia de estado,
        # así que hay que olvidar el anterior para forzarlo.
        self._progreso_visible = None
        self.actualizar_estilo_progreso(self.slider_progreso.value())

        for muestra in self.muestras_color:
            muestra.establecer_elegido(muestra.color.rgb() == COLOR_ACENTO.rgb())

        self.portada.update()
        for cabecera in (self.boton_atras_lista, self.boton_atras_radio,
                         self.cabecera_listas, self.cabecera_detalle,
                         self.cabecera_ajustes, self.cabecera_estadisticas,
                         self.cabecera_juego):
            cabecera.update()
        self.fila_menu_canciones.update()
        self.fila_menu_listas.update()
        self.fila_menu_radio.update()
        self.fila_menu_estadisticas.update()
        self.fila_menu_ajustes.update()
        self.fila_siempre_visible.update()

        if guardar:
            self.ajustes.setValue(CLAVE_COLOR_ACENTO, COLOR_ACENTO.name())

    # ---------------- Ventana siempre por encima ----------------

    def alternar_siempre_visible(self):
        self.aplicar_siempre_visible(not self.siempre_visible)

    def aplicar_siempre_visible(self, activo, guardar=True):
        """Pone o quita la ventana por encima de todas las demás.

        Cambiar una bandera de ventana obliga a Qt a rehacerla por
        dentro, y al rehacerla la esconde: hay que volver a enseñarla y
        devolverla a donde estaba, porque el sistema la recoloca.
        """
        self.siempre_visible = activo
        posicion = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, activo)
        self.move(posicion)
        self.show()
        self.raise_()
        self.activateWindow()
        self.fila_siempre_visible.establecer_activo(activo)
        if guardar:
            self.ajustes.setValue(CLAVE_SIEMPRE_VISIBLE, activo)

    def cargar_cancion(self, indice, reproducir=True):
        if self.emisora_actual is not None:
            self._salir_de_la_radio()
        self._reiniciar_conteo_pista()
        ruta = self.canciones[indice]
        # La curva de la cancion anterior ya no vale. Mientras se analiza
        # esta, el orbe se mueve al azar como toda la vida.
        self.portada.onda.establecer_envolvente([], AnalizadorOnda.RESOLUCION_MS)
        self.portada.onda.establecer_posicion(0)
        self.analizador_onda.analizar(ruta)
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

    def _al_analizar_onda(self, ruta, envolvente):
        """Llega con retraso -unas decimas- asi que puede que para
        entonces ya se haya cambiado de cancion. Se compara contra lo que
        el reproductor tiene cargado de verdad, que es la unica fuente
        fiable de que suena."""
        if self.emisora_actual is not None:
            return
        if self.player.source().toLocalFile() != ruta:
            return
        self.portada.onda.establecer_envolvente(envolvente, AnalizadorOnda.RESOLUCION_MS)
        self.portada.onda.establecer_posicion(self.player.position())
        if self.jugando():
            # Canción nueva con el juego abierto: partitura nueva.
            self.juego.establecer_partitura(
                envolvente, AnalizadorOnda.RESOLUCION_MS, self.player.position()
            )

    def _actualizar_indicadores_ecualizador(self, *_):
        """Enciende el ecualizador solo en la fila que está sonando
        ahora mismo (y solo si de verdad está reproduciéndose, no en
        pausa), dejando el resto de filas sin indicador."""
        reproduciendo = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        en_radio = self.emisora_actual is not None
        for i, fila in enumerate(self._filas_canciones):
            fila.indicador.establecer_estado(
                not en_radio and i == self.indice_actual, reproduciendo
            )
        for i, fila in enumerate(self._filas_emisoras):
            fila.indicador.establecer_estado(
                en_radio and i == self.indice_emisora, reproduciendo
            )

    def _actualizar_desvanecidos_lista(self, *_):
        """El degradado de abajo se ve mientras quede lista por debajo;
        el de arriba solo aparece una vez que has hecho scroll hacia
        abajo (al principio no hay nada que difuminar arriba)."""
        barra = self.lista_canciones.verticalScrollBar()
        self.desvanecido_arriba.setVisible(barra.value() > barra.minimum())
        self.desvanecido_abajo.setVisible(barra.value() < barra.maximum())

    def reproducir_seleccionada(self, item):
        indice = self.lista_canciones.row(item)
        # Doble clic sobre lo que ya está sonando no quiere decir "empieza
        # otra vez" sino "enséñamela": se cierra la lista y se ve la
        # portada, que es donde uno mira cuando quiere ver qué suena.
        if indice == self.indice_actual and self.emisora_actual is None:
            self.stack_superior.setCurrentIndex(self.PANTALLA_PORTADA)
            return
        if self.indice_actual >= 0 and indice != self.indice_actual:
            self.historial.append(self.indice_actual)
        # Elegir una canción a mano rompe el camino de "adelante": a
        # partir de aquí, "siguiente" vuelve a decidir por su cuenta.
        self.pila_siguientes.clear()
        self.indice_actual = indice
        self.cargar_cancion(indice)

    # Los tres botones de debajo de la pantalla. Casi siempre hacen lo
    # de siempre; con el juego abierto, son los mandos del juego. Van
    # aparte de 'anterior_cancion' y compañía porque a esas las llama
    # también la propia app -al acabarse una canción, por ejemplo- y eso
    # tiene que seguir funcionando mientras se juega.

    def _pulsar_anterior(self):
        self.juego.pulsar(0) if self.jugando() else self.anterior_cancion()

    def _pulsar_play(self):
        self.juego.pulsar(1) if self.jugando() else self.play_pausa()

    def _pulsar_siguiente(self):
        self.juego.pulsar(2) if self.jugando() else self.siguiente_cancion()

    def play_pausa(self):
        if not (self.canciones or self.emisora_actual):
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
        if self.emisora_actual is not None:
            self._cambiar_emisora(1)
            return
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
        if self.emisora_actual is not None:
            # En un directo no hay principio al que rebobinar: "anterior"
            # solo puede significar la emisora de antes.
            self._cambiar_emisora(-1)
            return
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
        if self.emisora_actual is not None:
            if estado == QMediaPlayer.MediaStatus.InvalidMedia:
                self._mostrar_estado_radio(TEXTOS["sin_conexion"])
            elif estado == QMediaPlayer.MediaStatus.BufferedMedia:
                # Ha vuelto a fluir: se deshace el aviso de sin conexión.
                self._mostrar_estado_radio(TEXTOS["en_directo"])

        if estado == QMediaPlayer.MediaStatus.EndOfMedia:
            # En un directo esto no es "se acabó la canción" sino que se
            # ha cortado la emisión. Encadenar con otra emisora sería
            # desconcertante: mejor quedarse quieto.
            if self.emisora_actual is None:
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
        if self.emisora_actual is not None:
            return  # una emisora no es un sitio al que volver
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
        self.volcar_estadisticas()
        super().closeEvent(event)

    def _refrescar_centro_multimedia(self):
        """Le cuenta al sistema qué está sonando, para el Centro de
        Control y la pantalla bloqueada."""
        if self.emisora_actual is not None:
            self.centro_multimedia.actualizar(
                self.emisora_actual["nombre"], TEXTOS["radio"], 0, 0,
                self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            )
            return
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
        self._acumular_escucha(posicion)
        # La animacion lleva su propio reloj entre aviso y aviso; aqui se
        # vuelve a poner en hora, y de paso se cubre el saltar por la barra.
        self.portada.onda.establecer_posicion(posicion)
        self.juego.sincronizar(posicion)
        if not self._arrastrando_slider:
            self.slider_progreso.setValue(posicion)
        self.label_tiempo_actual.setText(formatear_tiempo(posicion))

        # De vez en cuando, no en cada aviso: llegan unos diez por segundo.
        if abs(posicion - self._ultima_posicion_guardada) >= INTERVALO_GUARDAR_POSICION_MS:
            self._guardar_punto_escucha(posicion)

    def actualizar_duracion(self, duracion):
        if self.emisora_actual is not None:
            return  # un directo no tiene duración que enseñar
        self.slider_progreso.setRange(0, duracion)
        self.label_tiempo_total.setText(formatear_tiempo(duracion))
        # La duración llega cuando el archivo ya está leído, que es el
        # primer momento en que la ficha del sistema puede estar completa.
        self._refrescar_centro_multimedia()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    asegurar_tls()   # ver el porqué en la propia función
    ventana = Reproductor()
    ventana.show()
    sys.exit(app.exec())
