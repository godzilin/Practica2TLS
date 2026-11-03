"""
Transporte de los clientes del robot: TLS a través de la pasarela, o TCP directo al robot.

conectar() sustituye a socket.create_connection() en los scripts de la práctica de TCP/IP.
Recibe lo mismo (dirección del robot y timeout) y devuelve un socket que se usa igual
(recv, sendall, with ...). En cada script solo cambia esa línea: el código que construye
e interpreta los mensajes del robot es el mismo (C6).

Modos (campo "modo" de cliente.json, o --directo en la línea de órdenes):
    "pasarela"  TLS 1.3 con la IP de la pasarela, en el puerto que corresponde al del robot
                según "puertos" (30001 -> 3000...). Presenta el certificado del cliente y
                valida el de la pasarela contra la CA del laboratorio.
    "directo"   TCP con el robot, como en la práctica de TCP/IP (C7).

Opciones de línea de órdenes, para las pruebas de la demostración:
    --directo               sin pasarela
    --cert F --clave F      otro certificado de cliente (cliente-b, cliente-mal)
    --sin-cert              sin certificado de cliente
    --ca F                  otra CA de confianza (la de otro estudiante)

La contraseña de la clave del cliente se pide una vez al arrancar, sin mostrarla, o se lee
de la variable de entorno CLIENTE_CLAVE (ver README).
"""

import argparse
import getpass
import json
import os
import socket
import ssl
import threading
from pathlib import Path

CARPETA = Path(__file__).parent


def leer_config():
    """cliente.json, con lo que se cambie en la línea de órdenes (C1, C7)."""
    try:
        cfg = json.loads((CARPETA / "cliente.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"cliente.json no válido: {e!r}")
    opciones = argparse.ArgumentParser(add_help=False)
    opciones.add_argument("--directo", action="store_true")
    opciones.add_argument("--sin-cert", action="store_true")
    opciones.add_argument("--cert")
    opciones.add_argument("--clave")
    opciones.add_argument("--ca")
    args, _ = opciones.parse_known_args()       # parse_known: no molesta a las opciones del script
    if args.directo:
        cfg["modo"] = "directo"
    if args.sin_cert:
        cfg["certificado"] = cfg["clave"] = None
    for campo in ("cert", "clave", "ca"):
        if getattr(args, campo):
            cfg["certificado" if campo == "cert" else campo] = getattr(args, campo)
    return cfg


def crear_contexto(cfg):
    """Contexto TLS de cliente: solo TLS 1.3, solo la CA del laboratorio y su certificado."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)       # C3: ya exige serverAuth y comprueba la SAN
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3        # C4
    ruta = lambda f: CARPETA / Path(f).expanduser()     # relativa a esta carpeta, o absoluta
    try:
        ctx.load_verify_locations(cafile=ruta(cfg["ca"]))           # C3: nada de CA del sistema
        if cfg["certificado"]:                                      # C2 (no hay con --sin-cert)
            clave = os.environ.get("CLIENTE_CLAVE") or getpass.getpass("Contraseña de la clave del cliente: ")
            ctx.load_cert_chain(ruta(cfg["certificado"]), ruta(cfg["clave"]), password=clave)
    except (ssl.SSLError, OSError) as e:
        raise SystemExit(f"No se pudo cargar la CA, el certificado o la clave del cliente "
                         f"(¿contraseña incorrecta o fichero que no existe?): {e}")
    ctx.sslsocket_class = SocketTLS                     # recv() que explica los cierres (C8, C9)
    return ctx


class SocketTLS(ssl.SSLSocket):
    """Socket TLS normal, con dos añadidos:

    1. recv() explica por qué la pasarela corta la conexión. Con TLS 1.3 el cliente termina
       su handshake antes de que la pasarela compruebe su certificado, así que el rechazo no
       aparece al conectar sino en la primera lectura (C9).
    2. recv() y sendall() no se ejecutan a la vez: un SSLSocket de Python falla a veces si un
       hilo lee mientras otro escribe, y ensenar_reproducir.py lo hace (un hilo recibe el
       estado y el principal envía los movej). Con TCP normal no hacía falta.
       shortcut: sendall espera a que acabe el recv en curso; vale porque el robot envía
       el estado 10 veces por segundo (espera < 0,1 s).
    """

    def sendall(self, datos, flags=0):
        with self.cerrojo:
            return super().sendall(datos, flags)

    def recv(self, n=1024, flags=0):
        try:
            with self.cerrojo:
                datos = super().recv(n, flags)
        except ssl.SSLError as e:
            if "ALERT" in (e.reason or ""):             # p. ej. TLSV13_ALERT_CERTIFICATE_REQUIRED
                print(f"[TLS] La pasarela ha rechazado el certificado del cliente ({e.reason})")
            else:
                print(f"[TLS] Error TLS: {e.reason or e}")
            raise
        if not datos:
            print("[TLS] La pasarela ha cerrado la conexión: cliente no autorizado o robot no disponible "
                  "(el motivo está en el registro de la pasarela)")
        return datos


CFG = leer_config()
CONTEXTO = crear_contexto(CFG) if CFG["modo"] == "pasarela" else None   # contraseña una sola vez


def conectar(direccion, timeout=None):
    """Igual que socket.create_connection(direccion, timeout), pero según el modo."""
    if CFG["modo"] == "directo":
        return socket.create_connection(direccion, timeout=timeout)

    ip = CFG["pasarela"]
    try:
        puerto = CFG["puertos"][str(direccion[1])]
    except KeyError:
        raise SystemExit(f"cliente.json: falta en \"puertos\" a qué puerto de la pasarela va el {direccion[1]}")
    try:
        tcp = socket.create_connection((ip, puerto), timeout=timeout)
    except ConnectionRefusedError:
        print(f"[TLS] Conexión rechazada: nadie escucha en {ip}:{puerto} (¿está arrancada la pasarela?)")
        raise
    except OSError as e:                                # TimeoutError, red o equipo inalcanzable
        print(f"[TLS] Pasarela inalcanzable en {ip}:{puerto}: {e}")
        raise
    try:
        tls = CONTEXTO.wrap_socket(tcp, server_hostname=ip)    # handshake; la IP se busca en la SAN
    except ssl.SSLCertVerificationError as e:
        tcp.close()
        print(f"[TLS] Certificado de la pasarela NO válido: {e.verify_message}")
        raise
    except (ssl.SSLError, OSError) as e:
        tcp.close()
        print(f"[TLS] Fallo en el handshake con la pasarela: {getattr(e, 'reason', None) or e}")
        raise
    tls.cerrojo = threading.Lock()                      # ver SocketTLS
    cert = tls.getpeercert()                            # C5: lo que ve el operador al conectar
    sujeto = dict(campo[0] for campo in cert["subject"])
    print(f"[TLS] {tls.version()}, {tls.cipher()[0]}. Pasarela: CN={sujeto.get('commonName')}, "
          f"caduca {cert['notAfter']}")
    return tls
