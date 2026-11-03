"""
Tiempo de respuesta del robot, directo y a través de la pasarela (C10).

La interfaz primaria (30001) envía el estado sin que se le pida, así que no tiene
peticiones que medir. El dashboard server (29999) sí: a cada orden contesta una línea.
Se envía N veces "robotmode" y se mide con perf_counter lo que tarda cada respuesta.
También se mide lo que tarda en abrirse la conexión (en TLS, incluye el handshake).

    python medir.py              según el modo de cliente.json (pasarela)
    python medir.py --directo    directo al robot, para comparar
"""

import json
import statistics
import time
from pathlib import Path

from transporte import CFG, conectar

N = 100                 # peticiones que se miden
PUERTO_DASHBOARD = 29999
TIMEOUT = 5.0


def leer_linea(sock):
    """Lee hasta el salto de línea: una respuesta puede llegar en varios trozos."""
    respuesta = b""
    while not respuesta.endswith(b"\n"):
        datos = sock.recv(1024)
        if not datos:
            raise ConnectionError("conexión cerrada")
        respuesta += datos
    return respuesta.decode(errors="replace").strip()


def main():
    host = json.loads(Path(__file__).with_name("robots.json").read_text(encoding="utf-8"))[0]["host"]
    print(f"Modo: {CFG['modo']}")
    inicio = time.perf_counter()
    with conectar((host, PUERTO_DASHBOARD), timeout=TIMEOUT) as sock:
        conexion = time.perf_counter() - inicio
        print("Bienvenida:", leer_linea(sock))
        tiempos = []
        for _ in range(N):
            inicio = time.perf_counter()
            sock.sendall(b"robotmode\n")
            respuesta = leer_linea(sock)
            tiempos.append((time.perf_counter() - inicio) * 1000)
    print(f"Última respuesta: {respuesta}")
    print(f"Abrir la conexión: {conexion * 1000:.2f} ms")
    print(f"{N} peticiones (ms): media {statistics.mean(tiempos):.3f}  mediana {statistics.median(tiempos):.3f}  "
          f"mín {min(tiempos):.3f}  máx {max(tiempos):.3f}")


if __name__ == "__main__":
    try:
        main()
    except OSError as e:            # el motivo ya lo ha explicado transporte.py
        raise SystemExit(f"Medida cancelada: {e}")
