"""
Visualizador UR3e por la interfaz primaria (puerto 30001) · Bloque A (aprobado).

Se conecta al robot (URSim o real), corta los mensajes binarios que llegan 10 veces
por segundo, saca los 6 ángulos de las articulaciones (q_actual) y mueve con ellos
el UR3e de PyBullet. Si se pierde la conexión, vuelve a conectar.

Cómo usarlo: pon el robot el primero en robots.json y pulsa F5 en VS Code.
"""

import json
import math
import struct
import time
from pathlib import Path

import pybullet as p
from UR3e_rg2.dynamic_ur3e import UR3eRG2

from transporte import conectar   # práctica TLS: único cambio, en lugar de socket.create_connection

# ===== CONFIGURACIÓN =====
PUERTO = 30001         # interfaz primaria
# Qué se escribe en la consola (un hito del enunciado en cada caso):
#   "angulos"  -> A4/A5: los 6 q_actual en grados, una vez por segundo
#   "bytes"    -> A1: cuántos bytes devuelve cada recv()
#   "mensajes" -> A2/A3: longitud y tipo de cada mensaje, y sus subpaquetes
MODO = "angulos"
TIMEOUT = 5.0          # s sin recibir nada = robot apagado o cable cortado
REINTENTO = 2.0        # s de espera antes de volver a conectar
MAX_LONGITUD = 65536   # una longitud mayor significa que el buffer está desalineado
TAM_RECV = 65536       # máximo de bytes que devuelve cada recv(). No es el tamaño de un mensaje:
                       # el buffer junta o corta lo que llegue. Cabe el mensaje más largo aceptado
# =========================

# Tipos del documento Primary/Secondary Client Interface
MSG_ESTADO = 16        # Robot State Message: llega 10 veces por segundo
MSG_ROBOT = 20         # Robot Message: versión, errores... (subtipo en el byte 14)
SUB_ARTICULACIONES = 1 # subpaquete Joint data, dentro del mensaje de estado


def leer_robot():
    """Nombre e IP del primer robot de robots.json (en la misma carpeta que este script)."""
    try:
        robot = json.loads(Path(__file__).with_name("robots.json").read_text(encoding="utf-8"))[0]
        return str(robot["nombre"]), str(robot["host"])
    except (OSError, ValueError, LookupError, TypeError) as e:   # no existe, mal escrito o le falta algo
        raise SystemExit(f"robots.json no válido: {e!r}")


def cortar_mensajes(buffer):
    """Saca del buffer los mensajes completos y los devuelve en una lista.

    TCP entrega un flujo de bytes: un recv() puede traer medio mensaje, uno o varios.
    Cada mensaje empieza por su longitud (int de 4 bytes, incluida la cabecera) y su
    tipo (1 byte), así que se sabe dónde acaba. Lo incompleto se queda en el buffer.
    """
    completos = []
    while len(buffer) >= 5:                                 # hay al menos una cabecera
        (longitud,) = struct.unpack_from(">i", buffer)      # '>' = big-endian
        if not 5 <= longitud <= MAX_LONGITUD:
            raise ConnectionError(f"longitud imposible ({longitud}): buffer desalineado")
        if len(buffer) < longitud:
            break                                           # aún no ha llegado entero
        completos.append(bytes(buffer[:longitud]))
        del buffer[:longitud]                               # quitarlo del buffer
    return completos


def subpaquetes(mensaje):
    """Recorre un mensaje de estado y devuelve [(tipo, inicio, tamaño)] de cada subpaquete.

    Cada subpaquete empieza también por su tamaño (4 bytes) y su tipo (1 byte): para
    pasar al siguiente basta con sumar el tamaño, aunque no sepamos qué contiene.
    Si un subpaquete se sale del mensaje, el mensaje está corrupto y se deja de leer.
    """
    lista = []
    pos = 5                                                 # saltar la cabecera del mensaje
    while pos + 5 <= len(mensaje):
        tam, tipo = struct.unpack_from(">iB", mensaje, pos)
        if tam < 5 or pos + tam > len(mensaje):
            break
        lista.append((tipo, pos, tam))
        pos += tam
    return lista


def leer_q(mensaje):
    """Los 6 q_actual (rad) de un mensaje de estado, o None si no se pueden leer."""
    for tipo, pos, tam in subpaquetes(mensaje):
        if tipo != SUB_ARTICULACIONES:
            continue
        # 6 articulaciones de 41 B (>dddffffB = 3·8 + 4·4 + 1). El primer double es q_actual.
        if tam != 5 + 6 * 41:
            print(f"AVISO: el subpaquete de articulaciones mide {tam} B, no 251")
            return None
        q = [struct.unpack_from(">d", mensaje, pos + 5 + 41 * j)[0] for j in range(6)]
        return q if all(math.isfinite(v) for v in q) else None   # un NaN no se dibuja
    return None


def leer_version(mensaje):
    """Texto del mensaje de versión (tipo 20, subtipo 3), o None si es otro mensaje.

    Bytes: longitud (4), tipo (1), timestamp (8), source (1), subtipo (1),
    longitud del nombre n (1), nombre (n), versión mayor (1) y menor (1).
    """
    if len(mensaje) < 16 or mensaje[14] != 3:
        return None
    n = mensaje[15]
    if len(mensaje) < 18 + n:
        return None
    return f"Versión: {mensaje[16:16 + n].decode(errors='replace')} {mensaje[16 + n]}.{mensaje[17 + n]}"


def grados(q):
    """Texto de una lista de ángulos en radianes, escritos en grados."""
    return "[" + ", ".join(f"{math.degrees(v):7.2f}" for v in q) + "]"


def imprimir_estructura(mensaje):
    """Hitos A2/A3: longitud y tipo del mensaje y, si es de estado, sus subpaquetes."""
    print(f"mensaje {len(mensaje):5d} B  tipo {mensaje[4]}")
    if mensaje[4] == MSG_ESTADO:
        subs = subpaquetes(mensaje)
        for tipo, _, tam in subs:
            print(f"    subpaquete tipo {tipo:2d}  {tam:4d} B")
        print(f"    suma de subpaquetes = {sum(tam for _, _, tam in subs)} B, "
              f"mensaje - 5 = {len(mensaje) - 5} B")


def main():
    nombre, host = leer_robot()
    print(f"Robot: {nombre} ({host}:{PUERTO})")
    escena = UR3eRG2()                                      # abre la ventana con el robot 0
    ultimo_print = 0.0

    while True:                                             # una vuelta por cada conexión
        try:
            with conectar((host, PUERTO), timeout=TIMEOUT) as sock:
                print(f"Conectado a {host}:{PUERTO}")
                buffer = bytearray()
                while True:
                    # 1. Recibir lo que haya y añadirlo al final del buffer
                    datos = sock.recv(TAM_RECV)
                    if not datos:
                        raise ConnectionError("el robot ha cerrado la conexión")
                    if MODO == "bytes":
                        print(f"recv() -> {len(datos)} B")
                    buffer += datos

                    # 2. Tratar cada mensaje completo. Los tipos que no interesan se ignoran
                    for mensaje in cortar_mensajes(buffer):
                        if MODO == "mensajes":
                            imprimir_estructura(mensaje)
                        if mensaje[4] == MSG_ROBOT and leer_version(mensaje):
                            print(leer_version(mensaje))
                        if mensaje[4] != MSG_ESTADO:
                            continue
                        q = leer_q(mensaje)
                        if q is None:
                            continue
                        escena.move_robot(0, list(q))       # una copia: move_robot alarga la lista
                        if MODO == "angulos" and time.monotonic() - ultimo_print >= 1:
                            print(f"q_actual (grados): {grados(q)}")
                            ultimo_print = time.monotonic()
        except OSError as e:                                # ConnectionError y timeout también lo son
            print(f"Conexión perdida: {e}. Reintento en {REINTENTO:.0f} s")
            time.sleep(REINTENTO)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Fin")
    except p.error:                                         # move_robot sin ventana
        print("Ventana de PyBullet cerrada. Fin")
