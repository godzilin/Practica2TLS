"""
Servidor de eco para la Fase 3: devuelve tal cual lo que recibe.
Hace de «robot» para probar la pasarela sin el robot: en pasarela.json se pone
"destino": "127.0.0.1" y "puerto_destino": 9000.

    python eco.py
"""

import socket
import threading

DIRECCION = ("127.0.0.1", 9000)


def atender(conexion):
    with conexion:
        while datos := conexion.recv(65536):     # b'' = el otro ha cerrado
            conexion.sendall(datos)


with socket.create_server(DIRECCION) as servidor:
    print(f"Eco en {DIRECCION[0]}:{DIRECCION[1]}. Ctrl+C para parar")
    try:
        while True:
            conexion, _ = servidor.accept()
            threading.Thread(target=atender, args=(conexion,), daemon=True).start()
    except KeyboardInterrupt:
        print("Fin")
