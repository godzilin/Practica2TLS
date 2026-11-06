"""
Visualizador UR3e por la interfaz de tiempo real (puerto 30003) · Bloque B (aprobado).

El robot envía 500 veces por segundo (e-Series) un paquete de estructura fija: sin
tipos ni subpaquetes, cada campo siempre en el mismo byte. Los 6 q_actual empiezan
en el byte 252.

A 500 Hz no se puede dibujar cada paquete: se leen todos, se guarda solo el último
y PyBullet se actualiza FPS veces por segundo. Así el robot de la pantalla no acumula
retraso.

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
PUERTO = 30003         # interfaz de tiempo real
# "angulos": paquetes/s y q_actual una vez por segundo (B2, B3)
# "paquetes": bytes de cada recv() y longitud de cada paquete (B1)
MODO = "angulos"
FPS = 30               # veces por segundo que se actualiza PyBullet
TIMEOUT = 5.0          # s sin recibir nada = robot apagado o cable cortado
REINTENTO = 2.0        # s de espera antes de volver a conectar
MAX_LONGITUD = 65536   # una longitud mayor significa que el buffer está desalineado
TAM_RECV = 65536       # máximo de bytes que devuelve cada recv(). No es el tamaño de un mensaje:
                       # el buffer junta o corta lo que llegue. Cabe el mensaje más largo aceptado
POS_Q_ACTUAL = 252     # byte donde empiezan los 6 double de q_actual (hoja Client Interfaces)
# =========================


def leer_robot():
    """Nombre e IP del primer robot de robots.json (en la misma carpeta que este script)."""
    try:
        robot = json.loads(Path(__file__).with_name("robots.json").read_text(encoding="utf-8"))[0]
        return str(robot["nombre"]), str(robot["host"])
    except (OSError, ValueError, LookupError, TypeError) as e:   # no existe, mal escrito o le falta algo
        raise SystemExit(f"robots.json no válido: {e!r}")


def cortar_paquetes(buffer):
    """Saca del buffer los paquetes completos y los devuelve en una lista.

    Igual que en la primaria, pero la cabecera son solo 4 bytes (la longitud): no hay
    tipo porque solo existe un tipo de paquete. La longitud cambia con la versión del
    robot, así que se lee de cada paquete en vez de escribirla como constante.
    """
    completos = []
    while len(buffer) >= 4:
        (longitud,) = struct.unpack_from(">i", buffer)
        if not POS_Q_ACTUAL + 6 * 8 <= longitud <= MAX_LONGITUD:   # tiene que caber q_actual
            raise ConnectionError(f"longitud imposible ({longitud}): buffer desalineado")
        if len(buffer) < longitud:
            break                                           # aún no ha llegado entero
        completos.append(bytes(buffer[:longitud]))
        del buffer[:longitud]
    return completos


def grados(q):
    """Texto de una lista de ángulos en radianes, escritos en grados."""
    return "[" + ", ".join(f"{math.degrees(v):7.2f}" for v in q) + "]"


def main():
    nombre, host = leer_robot()
    print(f"Robot: {nombre} ({host}:{PUERTO})")
    escena = UR3eRG2()

    while True:                                             # una vuelta por cada conexión
        try:
            with conectar((host, PUERTO), timeout=TIMEOUT) as sock:
                print(f"Conectado a {host}:{PUERTO}")
                buffer = bytearray()
                q = None                                    # último q_actual recibido
                paquetes = 0                                # para contar paquetes por segundo
                ultimo_dibujo = ultimo_print = time.monotonic()
                while True:
                    # 1. Recibir lo que haya y añadirlo al final del buffer
                    datos = sock.recv(TAM_RECV)
                    if not datos:
                        raise ConnectionError("el robot ha cerrado la conexión")
                    if MODO == "paquetes":
                        print(f"recv() -> {len(datos)} B")
                    buffer += datos

                    # 2. Leer TODOS los paquetes completos, quedándose solo con el último q
                    for paquete in cortar_paquetes(buffer):
                        if MODO == "paquetes":
                            print(f"    paquete de {len(paquete)} B")
                        nuevo = list(struct.unpack_from(">6d", paquete, POS_Q_ACTUAL))
                        if all(math.isfinite(v) for v in nuevo):    # un NaN no se dibuja
                            q = nuevo
                        paquetes += 1

                    # 3. Dibujar a ritmo fijo, no con cada paquete
                    ahora = time.monotonic()
                    if q is not None and ahora - ultimo_dibujo >= 1 / FPS:
                        escena.move_robot(0, list(q))       # una copia: move_robot alarga la lista
                        ultimo_dibujo = ahora

                    # 4. Una vez por segundo: paquetes/s y ángulos
                    if MODO == "angulos" and q is not None and ahora - ultimo_print >= 1:
                        print(f"{paquetes / (ahora - ultimo_print):4.0f} paquetes/s   "
                              f"q_actual (grados): {grados(q)}")
                        paquetes, ultimo_print = 0, ahora
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
