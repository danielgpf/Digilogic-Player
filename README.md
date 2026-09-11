# Digilogic Player

[![Descargas](https://img.shields.io/github/downloads/danielgpf/Digilogic-Player/total?label=descargas&color=4F8CFF)](https://github.com/danielgpf/Digilogic-Player/releases)
[![Última versión](https://img.shields.io/github/v/release/danielgpf/Digilogic-Player?label=versi%C3%B3n&color=4F8CFF)](https://github.com/danielgpf/Digilogic-Player/releases/latest)
[![Licencia](https://img.shields.io/badge/licencia-MIT-lightgrey)](LICENSE)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-ap%C3%B3yame-ff5f5f?logo=ko-fi&logoColor=white)](https://ko-fi.com/daniel_digilogic)

Reproductor de MP3 para macOS y Windows, escrito en Python con PyQt6.
Ventana sin bordes, esquinas redondeadas de verdad y un visualizador
animado inspirado en el orbe de Siri que se dibuja dentro de una nota
musical.

Reproduce los MP3 directamente desde la carpeta que le indiques —puede ser
un USB— sin copiarlos ni importarlos a ningún sitio.

**Web:** https://digilogic-app.github.io/

## Descargar

| Sistema | Descarga |
|---|---|
| macOS (Apple Silicon, macOS 13 o superior) | [Digilogic.dmg](https://github.com/danielgpf/Digilogic-Player/releases/latest/download/Digilogic.dmg) |
| Windows 10 u 11 (64 bits) | [Digilogic.exe](https://github.com/danielgpf/Digilogic-Player/releases/latest/download/Digilogic.exe) |

La app no está firmada (la firma de Apple cuesta 99 €/año y la de
Microsoft ronda los 200 €), así que la primera vez cada sistema muestra un
aviso. En macOS: clic derecho → Abrir. En Windows: *Más información* →
*Ejecutar de todas formas*. Solo hace falta una vez. Más detalles en las
[notas de la versión](https://github.com/danielgpf/Digilogic-Player/releases/latest).

## Qué hace

- **Lee tu carpeta de música** y muestra la portada incrustada en cada MP3.
  Si la canción no tiene portada, dibuja una nota musical con lóbulos de
  color que giran y se funden entre sí en mezcla aditiva.
- **Modo compacto**: al pulsar la nota, la ventana se encoge a una tarjeta
  horizontal y se coloca en la esquina superior izquierda de la pantalla,
  para dejarla de fondo mientras trabajas. Se vuelve pulsando de nuevo.
- **Buscador** de la música que ya tienes, que filtra la lista mientras
  escribes.
- **Reproducción aleatoria con historial real**: al pulsar "anterior"
  vuelve a la canción que sonó de verdad, no a otra al azar.
- **"Anterior" con umbral**: si la canción lleva más de 10 segundos
  sonando, la rebobina al principio en vez de saltar a la anterior, como
  hacen Spotify o Apple Music.
- **Recuerda la carpeta** entre sesiones, así que solo hay que elegirla
  una vez.
- **Se integra con cada sistema**: en macOS, el semáforo de cerrar se
  apaga a gris cuando la ventana pierde el foco y los iconos se dibujan a
  la resolución real de las pantallas Retina; en Windows, botón de cerrar
  al estilo de Windows 11 e icono de la app en la barra de tareas.

## Fallos, ideas y comentarios

- ¿Algo no funciona? [Reporta un fallo](https://github.com/danielgpf/Digilogic-Player/issues/new?template=fallo.yml).
- ¿Te falta algo? [Propón una idea](https://github.com/danielgpf/Digilogic-Player/issues/new?template=idea.yml).
- Para dudas, opiniones o contar cómo lo usas: [Discussions](https://github.com/danielgpf/Digilogic-Player/discussions).

Toda idea se lee. Digilogic quiere seguir siendo pequeño y sencillo, así
que no todo entrará, pero lo que entra se anuncia en las notas de cada
versión con el nombre de quien lo propuso.

## Apoyar el proyecto

Digilogic es gratis y de código abierto, y lo seguirá siendo. Si te
resulta útil, puedes [invitarme a un café en Ko-fi](https://ko-fi.com/daniel_digilogic).
Lo recaudado va a estos objetivos, por orden:

| Objetivo | Coste | Qué consigue |
|---|---|---|
| Microsoft Store | 19 $ (una vez) | Instalar en Windows sin el aviso de SmartScreen |
| Firma de código en Windows | ~10 $/mes | Que el `.exe` descargado de la web abra sin avisos |
| Apple Developer | 99 $/año | Que la app de Mac abra sin avisos y, más adelante, versión para iPhone |
| Google Play | 25 $ (una vez) | Versión para Android |

## Ejecutar desde el código

Hace falta Python 3.

```bash
git clone https://github.com/danielgpf/Digilogic-Player.git
cd Digilogic-Player
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python Digilogic.py
```

En Windows, cambia `./venv/bin/` por `venv\Scripts\`.

La primera vez, pulsa sobre el título ("Selecciona una carpeta") para
elegir dónde tienes tus MP3.

### Empaquetar

- **macOS**: `./venv/bin/python setup.py py2app` → `dist/Digilogic.app`.
- **Windows**: `venv\Scripts\python construir_windows.py` → `dist\Digilogic.exe`.

Cada sistema empaqueta solo desde sí mismo: el `.app` se genera en un Mac y
el `.exe` en un Windows.

## Cómo se usa

| Acción | Cómo |
|---|---|
| Elegir carpeta de música | Pulsar sobre el título |
| Entrar o salir del modo compacto | Pulsar sobre la nota o la portada |
| Mover la ventana | Arrastrarla desde cualquier punto |
| Ver la lista de canciones | Botón de las tres líneas |
| Reproducir una canción | Doble clic en la lista |
| Buscar en tu música | Escribir en la barra de arriba |

## Estructura

- `Digilogic.py` — punto de entrada.
- `reproductor.py` — todo el reproductor: interfaz, animaciones y lógica.
- `setup.py` — empaquetado para macOS (py2app).
- `construir_windows.py` — empaquetado para Windows (PyInstaller).
- `Nota-musica.svg`, `icono aleatorio.png`, `icono lista.png`,
  `Digilogic.ico` — recursos gráficos que la aplicación carga en tiempo de
  ejecución.

## Licencia

MIT. Ver [LICENSE](LICENSE).
