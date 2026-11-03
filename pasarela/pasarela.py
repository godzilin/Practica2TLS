"""
Pasarela TLS para el robot UR3e.

Para los clientes se comporta como el robot, pero por TLS 1.3 con autenticación mutua.
Para el robot es un cliente TCP normal. Entre los dos solo copia bytes, sin mirarlos.

    cliente ══TLS══> pasarela :3000 ──TCP──> robot :30001     (una «ruta» de pasarela.json)

Hilos (el diagrama está en docs/diseno.md):
    principal               arranca todo y espera a Ctrl+C
    1 por ruta              acepta clientes nuevos                                aceptar()
    1 por cliente           handshake, autorización, conexión con el robot        atender()
                            y reenvío en los dos sentidos con select()            reenviar()

Cómo usarlo:
    python pasarela.py              usa pasarela.json (en la misma carpeta que este fichero)
    python pasarela.py otra.json    usa otra configuración
La contraseña de la clave privada se pide al arrancar, sin mostrarla, o se lee de la
variable de entorno PASARELA_CLAVE (ver README).
"""

import getpass
import json
import logging
import os
import select
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

TAM_RECV = 65536    # máximo de bytes por recv(). No es un mensaje: se copia lo que llegue (P12)
ESPERA = 1.0        # s: cada cuánto mira cada hilo si tiene que terminar
TIEMPO_ENVIO = 5    # s: si un extremo no acepta datos en este tiempo, se da por muerto

log = logging.getLogger("pasarela")


def leer_config():
    """Lee el JSON de configuración. Las rutas de ficheros relativas son relativas al JSON."""
    fichero = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("pasarela.json")
    try:
        cfg = json.loads(fichero.read_text(encoding="utf-8"))
        carpeta = fichero.resolve().parent
        for campo in ("ca", "certificado", "clave", "registro"):
            cfg[campo] = carpeta / Path(cfg[campo]).expanduser()     # si ya es absoluta, se queda igual
        for campo in ("ip_escucha", "rutas", "autorizados", "tiempo_handshake", "tiempo_robot"):
            cfg[campo]                                               # que falte algo se detecta aquí
        return cfg
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit(f"{fichero} no válido: {e!r}")


def crear_contexto(cfg):
    """Contexto TLS de servidor con todos los requisitos de seguridad."""
    clave = os.environ.get("PASARELA_CLAVE") or getpass.getpass("Contraseña de la clave de la pasarela: ")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3                    # P3: solo TLS 1.3
    try:
        ctx.load_cert_chain(cfg["certificado"], cfg["clave"], password=clave)   # P2: identidad
        ctx.load_verify_locations(cafile=cfg["ca"])                 # P5: solo la CA del laboratorio
    except (ssl.SSLError, OSError) as e:                            # contraseña mal, fichero que falta...
        raise SystemExit(f"No se pudo cargar el certificado, la clave o la CA "
                         f"(¿contraseña incorrecta o fichero que no existe?): {e}")
    ctx.verify_mode = ssl.CERT_REQUIRED                             # P4: certificado obligatorio
    return ctx                                                      # P6 (clientAuth) lo comprueba OpenSSL


def cerrar_tls(tls):
    """Cierre TLS ordenado: close_notify (unwrap) y después cerrar el socket."""
    try:
        tls.settimeout(ESPERA)          # no esperar mucho a que el otro conteste al close_notify
        tls.unwrap()
    except (ssl.SSLError, OSError):     # el otro extremo ya se había ido sin close_notify
        pass
    tls.close()


class Pasarela:
    def __init__(self, cfg, ctx):
        self.cfg, self.ctx = cfg, ctx
        self.parar = threading.Event()  # lo activa Ctrl+C: todos los hilos lo miran (P16)

    def arrancar(self):
        """Un socket de escucha y un hilo que acepta por cada ruta."""
        servidores = []
        for ruta in self.cfg["rutas"]:
            try:
                servidor = socket.create_server((self.cfg["ip_escucha"], ruta["escucha"]))   # P1
            except OSError as e:
                raise SystemExit(f"No se puede escuchar en {self.cfg['ip_escucha']}:{ruta['escucha']}: {e}")
            servidor.settimeout(ESPERA)     # accept() vuelve cada segundo para mirar self.parar
            servidores.append(servidor)
            threading.Thread(target=self.aceptar, args=(servidor, ruta), name=f"aceptar-{ruta['escucha']}").start()
            log.info(f"Escuchando en {self.cfg['ip_escucha']}:{ruta['escucha']} -> "
                     f"{ruta['destino']}:{ruta['puerto_destino']}")
        return servidores

    def aceptar(self, servidor, ruta):
        """Hilo de una ruta: acepta clientes y lanza un hilo por cada uno (P10)."""
        while not self.parar.is_set():
            try:
                conexion, (ip, _) = servidor.accept()
            except TimeoutError:
                continue
            except OSError:                 # socket de escucha cerrado
                break
            threading.Thread(target=self.atender, args=(conexion, ip, ruta), name=f"cliente-{ip}").start()
        servidor.close()

    def atender(self, conexion, ip, ruta):
        """Hilo de un cliente: los 4 pasos de una pasarela transparente."""
        # 1. Handshake TLS con tiempo límite (P8). Aquí se rechaza a quien no tiene certificado,
        #    trae uno de otra CA o uno sin clientAuth, o habla TLS 1.2 (P3 a P6)
        conexion.settimeout(self.cfg["tiempo_handshake"])
        try:
            tls = self.ctx.wrap_socket(conexion, server_side=True)
        except (ssl.SSLError, OSError) as e:
            log.warning(f"{ip} rechazado en el handshake: {e}")
            conexion.close()
            return

        # 2. Autorización: el CN del certificado tiene que estar en la lista (P7)
        sujeto = dict(campo[0] for campo in tls.getpeercert()["subject"])
        cn = sujeto.get("commonName", "?")
        quien = f"{ip} CN={cn} {tls.version()} {tls.cipher()[0]}"   # P9: lo que sale en cada línea
        if cn not in self.cfg["autorizados"]:
            log.warning(f"{quien} rechazado: CN no autorizado")
            cerrar_tls(tls)
            return

        # 3. Conexión con el robot (P11). Si no se puede, cierre TLS ordenado (P13)
        destino = (ruta["destino"], ruta["puerto_destino"])
        try:
            robot = socket.create_connection(destino, timeout=self.cfg["tiempo_robot"])
        except OSError as e:
            log.error(f"{quien} rechazado: robot {destino[0]}:{destino[1]} no disponible ({e})")
            cerrar_tls(tls)
            return
        log.info(f"{quien} conectado con el robot {destino[0]}:{destino[1]}")

        # 4. Reenviar en los dos sentidos hasta que uno cierre (P12, P14, P15)
        motivo = self.reenviar(tls, robot)
        robot.close()
        cerrar_tls(tls)
        log.info(f"{quien} cerrado: {motivo}")

    def reenviar(self, tls, robot):
        """Copia bytes del cliente al robot y del robot al cliente hasta que uno cierre, algo
        falle o se pare la pasarela. Devuelve el motivo del cierre.

        Un solo hilo atiende los dos sentidos: select() espera a que cualquiera de los dos
        sockets tenga datos. No se usan dos hilos (uno por sentido) porque un SSLSocket de
        Python no admite que un hilo lea mientras otro escribe: lo probamos y a veces falla
        con «[Errno 11] Resource temporarily unavailable» (ver README, decisiones).
        """
        tls.settimeout(TIEMPO_ENVIO)        # sendall() a quien no lee no puede quedarse colgado
        robot.settimeout(TIEMPO_ENVIO)
        otro = {tls: (robot, "cliente"), robot: (tls, "robot")}
        while not self.parar.is_set():
            # tls.pending(): datos que TLS ya ha descifrado y que select() no ve en el socket
            listos = [tls] if tls.pending() else select.select([tls, robot], [], [], ESPERA)[0]
            for origen in listos:           # vacío si pasa ESPERA s sin datos: volver a mirar parar
                destino, nombre = otro[origen]
                try:
                    datos = origen.recv(TAM_RECV)
                except TimeoutError:        # select vio bytes pero aún no un registro TLS entero
                    continue
                except OSError as e:
                    return f"error al leer del {nombre}: {e}"
                if not datos:               # 0 bytes = el otro extremo ha cerrado
                    return f"el {nombre} ha cerrado la conexión"
                try:
                    destino.sendall(datos)  # P12: tal cual, sin mirar dónde empieza cada mensaje
                except OSError as e:
                    return f"error al enviar lo que llega del {nombre}: {e}"
        return "parada de la pasarela"


def main():
    cfg = leer_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        handlers=[logging.FileHandler(cfg["registro"], encoding="utf-8"),   # P9
                                  logging.StreamHandler()])
    pasarela = Pasarela(cfg, crear_contexto(cfg))
    pasarela.arrancar()
    log.info(f"Pasarela en marcha. Autorizados: {', '.join(cfg['autorizados'])}. Ctrl+C para parar")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    # P16: dejar de aceptar, cerrar las conexiones abiertas (con close_notify) y esperar a los hilos
    log.info("Parando: no se aceptan más clientes y se cierran las conexiones abiertas")
    pasarela.parar.set()
    for hilo in threading.enumerate():
        if hilo is not threading.main_thread():
            hilo.join()
    log.info("Pasarela parada")


if __name__ == "__main__":
    main()
