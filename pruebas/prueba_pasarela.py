"""
Prueba automática de la pasarela contra el servidor de eco (Fase 3), sin el robot.

Arranca eco.py y pasarela.py con una configuración de prueba (puertos 3100-3102, para
no chocar con una pasarela de verdad) y comprueba los requisitos P1 a P16 uno a uno.
Al final enseña el registro de la pasarela.

    PASARELA_CLAVE=... CLIENTE_CLAVE=... CLIENTEB_CLAVE=... python pruebas/prueba_pasarela.py

Necesita el cable puesto: la pasarela escucha en la IP de su SAN (10.172.18.70).
"""

import asyncio
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PKI = RAIZ / "pki"
IP = "10.172.18.70"
ECO, BUENA, SIN_ROBOT = 3100, 3101, 3102        # puertos de la pasarela de prueba
TIEMPO_HANDSHAKE = 2

fallos = []


def comprobar(nombre, condicion, detalle=""):
    print(f"{'OK   ' if condicion else 'FALLO'} {nombre} {detalle}")
    if not condicion:
        fallos.append(nombre)


def contexto(cliente="cliente-a", clave_env="CLIENTE_CLAVE", tls12=False):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(PKI / "publico/ca.crt")
    if tls12:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    if cliente:
        ctx.load_cert_chain(PKI / f"{cliente}/{cliente}.crt", PKI / f"{cliente}/{cliente}.key",
                            password=os.environ[clave_env])
    return ctx


def conectar(puerto, ctx=None):
    tls = (ctx or contexto()).wrap_socket(socket.create_connection((IP, puerto), timeout=5), server_hostname=IP)
    return tls


def eco_ok(tls, trozos=13, tam=16_000):
    """Envía bytes al azar en trozos y comprueba que vuelven iguales (P12).
    Envía y lee en secuencia: un SSLSocket de Python no admite leer y escribir desde dos hilos."""
    for _ in range(trozos):
        enviado = os.urandom(tam)
        tls.sendall(enviado)
        recibido = b""
        while len(recibido) < tam:
            datos = tls.recv(65536)
            if not datos:
                return False
            recibido += datos
        if recibido != enviado:
            return False
    return True


async def eco_dos_sentidos(ctx, tam=1_000_000):
    """1 MB que se envía mientras se recibe el eco: la pasarela copia en los dos sentidos a la vez.
    asyncio lo hace en un solo hilo, así que el cliente de prueba no tiene el problema de los hilos."""
    lector, escritor = await asyncio.open_connection(IP, ECO, ssl=ctx, server_hostname=IP)
    enviado = os.urandom(tam)
    escritor.write(enviado)
    recibido = await asyncio.wait_for(lector.readexactly(tam), 10)
    escritor.close()
    return recibido == enviado


async def estres(ctx, rondas=10):
    """rondas x 4 clientes a la vez, cada uno con 1 MB en los dos sentidos."""
    for _ in range(rondas):
        resultados = await asyncio.gather(*[eco_dos_sentidos(ctx) for _ in range(4)], return_exceptions=True)
        if resultados != [True] * 4:
            return False
    return True


def cerrada(tls):
    """True si la pasarela cierra la conexión (0 bytes o error TLS) antes de 5 s."""
    try:
        return tls.recv(1024) == b""
    except ssl.SSLError:
        return True
    except TimeoutError:
        return False


def main():
    carpeta = Path(tempfile.mkdtemp())
    cfg = json.loads((RAIZ / "pasarela/pasarela.json").read_text(encoding="utf-8"))
    cfg.update({"ip_escucha": IP, "tiempo_handshake": TIEMPO_HANDSHAKE, "registro": str(carpeta / "prueba.log"),
                "rutas": [{"escucha": ECO, "destino": "127.0.0.1", "puerto_destino": 9000},
                          {"escucha": BUENA, "destino": "127.0.0.1", "puerto_destino": 9000},
                          {"escucha": SIN_ROBOT, "destino": "127.0.0.1", "puerto_destino": 9}]})
    for campo in ("ca", "certificado", "clave"):
        cfg[campo] = str((RAIZ / "pasarela" / cfg[campo]).resolve())
    (carpeta / "prueba.json").write_text(json.dumps(cfg), encoding="utf-8")

    eco = subprocess.Popen([sys.executable, RAIZ / "pasarela/eco.py"], stdout=subprocess.DEVNULL)
    pasarela = subprocess.Popen([sys.executable, RAIZ / "pasarela/pasarela.py", carpeta / "prueba.json"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))   # por si se lanza en segundo plano
    time.sleep(1.5)
    try:
        # P1: escucha solo en la IP de la pasarela
        try:
            socket.create_connection(("127.0.0.1", ECO), timeout=1).close()
            comprobar("P1 solo escucha en su IP", False, "(127.0.0.1 acepta conexiones)")
        except OSError:
            comprobar("P1 solo escucha en su IP", True)

        # P12: cliente-a, los bytes vuelven iguales aunque lleguen troceados
        with conectar(ECO) as tls:
            comprobar("P12 reenvío sin modificar (200 kB)", eco_ok(tls), f"{tls.version()} {tls.cipher()[0]}")
        comprobar("P12 los dos sentidos a la vez (10 rondas x 4 clientes x 1 MB)", asyncio.run(estres(contexto())))

        # P3: TLS 1.2 rechazado
        try:
            conectar(ECO, contexto(tls12=True))
            comprobar("P3 TLS 1.2 rechazado", False)
        except ssl.SSLError as e:
            comprobar("P3 TLS 1.2 rechazado", True, f"({e.reason})")

        # P4: sin certificado -> rechazo, que en TLS 1.3 se ve en la primera lectura
        comprobar("P4 sin certificado rechazado", cerrada(conectar(ECO, contexto(cliente=None))))

        # P6: certificado de cliente con serverAuth
        if "CLIENTEMAL_CLAVE" in os.environ:
            comprobar("P6 certificado serverAuth rechazado",
                      cerrada(conectar(ECO, contexto("cliente-mal", "CLIENTEMAL_CLAVE"))))

        # P7: cliente-b tiene certificado válido pero no está autorizado
        if "CLIENTEB_CLAVE" in os.environ:
            comprobar("P7 cliente-b no autorizado", cerrada(conectar(ECO, contexto("cliente-b", "CLIENTEB_CLAVE"))))

        # P8 y P10: un cliente que se conecta y no habla no bloquea a los demás,
        # y la pasarela lo echa al pasar tiempo_handshake
        mudo = socket.create_connection((IP, ECO), timeout=TIEMPO_HANDSHAKE + 3)
        inicio = time.monotonic()
        clientes = [conectar(ECO) for _ in range(4)]
        resultados = [None] * 4

        def usar(i):
            resultados[i] = eco_ok(clientes[i])
        hilos = [threading.Thread(target=usar, args=(i,)) for i in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        comprobar("P10 4 clientes a la vez con uno mudo", all(resultados) and time.monotonic() - inicio < TIEMPO_HANDSHAKE,
                  f"({time.monotonic() - inicio:.2f} s)")
        comprobar("P8 el cliente mudo es expulsado", mudo.recv(1) == b"",
                  f"({time.monotonic() - inicio:.1f} s, límite {TIEMPO_HANDSHAKE} s)")
        mudo.close()

        # P13: destino caído -> la pasarela cierra al cliente (close_notify) y lo registra
        comprobar("P13 robot no disponible", cerrada(conectar(SIN_ROBOT)))

        # P14 y P15: el «robot» se cae con clientes conectados
        conectado = clientes[0]
        eco.terminate()
        eco.wait()
        comprobar("P14 se cae el robot y se cierra el cliente", cerrada(conectado))

        # P16: Ctrl+C con un cliente dentro -> close_notify a los clientes y fin ordenado
        eco = subprocess.Popen([sys.executable, RAIZ / "pasarela/eco.py"], stdout=subprocess.DEVNULL)
        time.sleep(0.5)
        dentro = conectar(BUENA)
        dentro.sendall(b"hola")
        dentro.recv(4)
        pasarela.send_signal(signal.SIGINT)
        comprobar("P16 Ctrl+C cierra a los clientes con close_notify", dentro.recv(1024) == b"")
        try:
            pasarela.wait(5)
            comprobar("P16 la pasarela termina", pasarela.returncode == 0)
        except subprocess.TimeoutExpired:
            comprobar("P16 la pasarela termina", False, "(sigue viva a los 5 s)")
    finally:
        pasarela.kill()
        eco.kill()

    print("\nRegistro de la pasarela (P9):")
    print((carpeta / "prueba.log").read_text(encoding="utf-8"))
    print("TODO OK" if not fallos else f"FALLOS: {fallos}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
