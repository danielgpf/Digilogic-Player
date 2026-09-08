# Digilogic Player

Reproductor de MP3 para macOS, escrito en Python con PyQt6. Ventana sin
bordes, esquinas redondeadas de verdad y un visualizador animado inspirado
en el orbe de Siri que se dibuja dentro de una nota musical.

Reproduce los MP3 directamente desde la carpeta que le indiques —puede ser
un USB— sin copiarlos ni importarlos a ningún sitio.

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
- **Se integra con macOS**: el botón de cerrar se apaga a gris cuando la
  ventana pierde el foco, y los iconos se dibujan a la resolución real de
  las pantallas Retina.

## Instalación

Hace falta Python 3.

```bash
git clone https://github.com/danielgpf/Digilogic-Player.git
cd Digilogic-Player
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python Digilogic.py
```

La primera vez, pulsa sobre el título ("Selecciona una carpeta") para
elegir dónde tienes tus MP3.

### Empaquetar como app de macOS

```bash
./venv/bin/python setup.py py2app
```

El resultado queda en `dist/Digilogic.app`.

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
- `setup.py` — configuración de empaquetado con py2app.
- `Nota-musica.svg`, `icono aleatorio.png`, `icono lista.png` — recursos
  gráficos que la aplicación carga en tiempo de ejecución.

## Licencia

MIT. Ver [LICENSE](LICENSE).
