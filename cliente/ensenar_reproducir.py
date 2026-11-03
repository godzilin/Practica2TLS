"""
Mini-aplicación «Enseñar y reproducir» · Bloque D (sobresaliente).

1. Enseñar: se lleva el robot a una postura (pestaña Mover de PolyScope o guiado a mano)
   y se pulsa Espacio para guardarla como punto.
2. Previsualizar: un robot fantasma (azul) recorre la secuencia sin mover el real.
3. Ejecutar: se envía un movej por punto y no se envía el siguiente hasta comprobar,
   con el estado que publica el robot, que el anterior ha terminado.
4. Supervisar: si llega un error o el robot se para, la secuencia se detiene, se
   muestra el motivo y se espera a que el operador la reanude o la cancele.
5. Guardar y cargar la secuencia en secuencia.json.

    hilo receptor ──> último estado (protegido con lock) + cola de errores
    bucle principal (el único que usa PyBullet y envía órdenes), FPS veces por segundo:
        1. lee las teclas   2. avanza la máquina de estados   3. dibuja

Teclas, con la ventana de PyBullet activa:
    Espacio  guardar la postura actual          B      borrar el último punto
    P        previsualizar                      Enter  ejecutar / reanudar
    Esc      parar el robot y cancelar          G / C  guardar / cargar secuencia.json
    E        provocar una parada de protección (para la demostración)

Cómo usarlo: pon el robot el primero en robots.json y pulsa F5 en VS Code.
"""

import json
import math
import queue
import struct
import threading
import time
import unicodedata
from pathlib import Path

import pybullet as p
from UR3e_rg2.dynamic_ur3e import UR3eRG2

from transporte import conectar   # práctica TLS: único cambio, en lugar de socket.create_connection

# ===== CONFIGURACIÓN =====
V = 0.3                # rad/s   velocidad de los movej: MÁXIMO permitido en el robot real
A = 0.5                # rad/s²  aceleración de los movej: MÁXIMO permitido en el robot real
TOLERANCIA = 0.01      # rad: distancia máxima al punto para darlo por alcanzado
TIEMPO_ARRANQUE = 2.0  # s: si el programa no arranca en este tiempo, la orden se ha rechazado
ACELERAR_PREVIEW = 3.0 # la previsualización va este número de veces más rápida que el robot
FICHERO_SECUENCIA = Path(__file__).with_name("secuencia.json")
PUERTO = 30001         # interfaz primaria: se lee el estado y se envían las órdenes
PUERTO_DASHBOARD = 29999   # para desbloquear una parada de protección al reanudar
FPS = 30               # veces por segundo que se actualiza la ventana
SIN_DATOS = 1.0        # s sin estado nuevo = robot desconectado
TIMEOUT = 5.0          # s sin recibir nada = robot apagado o cable cortado
REINTENTO = 2.0        # s de espera antes de volver a conectar
MAX_LONGITUD = 65536   # una longitud mayor significa que el buffer está desalineado
TAM_RECV = 65536       # máximo de bytes que devuelve cada recv(). No es el tamaño de un mensaje:
                       # el buffer junta o corta lo que llegue. Cabe el mensaje más largo aceptado
# =========================

MSG_ESTADO = 16        # Robot State Message
MSG_ROBOT = 20         # Robot Message (subtipo en el byte 14)
SUB_MODO = 0           # subpaquete Robot mode data
SUB_ARTICULACIONES = 1 # subpaquete Joint data
RUNNING = 7            # robotMode: encendido y con frenos sueltos
ESPACIO, ENTER, ESC = ord(" "), p.B3G_RETURN, 27
MOVIENDO_ROBOT = ("ENVIANDO", "MOVIENDO", "REANUDANDO")    # modos en los que el robot obedece una orden nuestra
AYUDA = {             # líneas de ayuda de la ventana: solo las teclas que sirven en cada modo
    "REPOSO": ["Espacio: guardar postura   B: borrar   P: ver", "Enter: ejecutar   G / C: guardar / cargar fichero"],
    "PREVISUALIZANDO": ["Esc: cancelar"],
    "ENVIANDO": ["Esc: PARAR   E: provocar error"],
    "MOVIENDO": ["Esc: PARAR   E: provocar error"],
    "DETENIDO": ["Enter: reanudar   Esc: cancelar"],
    "REANUDANDO": ["Esc: PARAR"],
}


def leer_robot():
    """Nombre e IP del primer robot de robots.json (en la misma carpeta que este script)."""
    try:
        robot = json.loads(Path(__file__).with_name("robots.json").read_text(encoding="utf-8"))[0]
        return str(robot["nombre"]), str(robot["host"])
    except (OSError, ValueError, LookupError, TypeError) as e:   # no existe, mal escrito o le falta algo
        raise SystemExit(f"robots.json no válido: {e!r}")


def cortar_mensajes(buffer):
    """Saca del buffer los mensajes completos (longitud de 4 bytes delante) en una lista."""
    completos = []
    while len(buffer) >= 5:
        (longitud,) = struct.unpack_from(">i", buffer)
        if not 5 <= longitud <= MAX_LONGITUD:
            raise ConnectionError(f"longitud imposible ({longitud}): buffer desalineado")
        if len(buffer) < longitud:
            break                                           # aún no ha llegado entero
        completos.append(bytes(buffer[:longitud]))
        del buffer[:longitud]
    return completos


def leer_estado(mensaje):
    """Ángulos y modo del robot de un mensaje de estado, o None si falta algo.

    Recorre los subpaquetes saltando cada uno con su tamaño y lee dos:
    - Robot mode data (0): uint64 timestamp (8 B) y 7 bool: ..., isEmergencyStopped
      (byte 11), isProtectiveStopped (12), isProgramRunning (13), isProgramPaused (14);
      robotMode (uchar) va en el byte 15.
    - Joint data (1): 6 bloques de 41 B (>dddffffB) cuyo primer double es q_actual.
    """
    estado = {}
    pos = 5                                                 # saltar la cabecera del mensaje
    while pos + 5 <= len(mensaje):
        tam, tipo = struct.unpack_from(">iB", mensaje, pos)
        if tam < 5 or pos + tam > len(mensaje):
            return None                                     # mensaje corrupto
        datos = pos + 5                                     # primer byte tras la cabecera
        if tipo == SUB_MODO and tam >= 5 + 16:
            estado["parada_emergencia"] = mensaje[datos + 11] != 0
            estado["parada_proteccion"] = mensaje[datos + 12] != 0
            estado["programa_en_marcha"] = mensaje[datos + 13] != 0
            estado["modo_robot"] = mensaje[datos + 15]
        elif tipo == SUB_ARTICULACIONES and tam == 5 + 6 * 41:
            q = [struct.unpack_from(">d", mensaje, datos + 41 * j)[0] for j in range(6)]
            if all(math.isfinite(v) for v in q):            # un NaN no se dibuja ni se envía
                estado["q"] = q
        pos += tam
    return estado if len(estado) == 5 else None


def leer_error(mensaje):
    """Texto de un mensaje de error tipo 20, o None si no es un error.

    Subtipo 6: error del robot (p. ej. parada de protección), con código y argumento.
    El texto puede venir vacío, así que se muestra el código como PolyScope: C<código>A<arg>.
    Subtipo 10: excepción del programa URScript, con la línea y la columna del fallo.
    """
    if len(mensaje) < 23:
        return None
    if mensaje[14] == 6 and len(mensaje) >= 32:
        codigo, argumento = struct.unpack_from(">ii", mensaje, 15)
        texto = mensaje[32:].decode(errors="replace").strip("\x00 ")
        return f"Error del robot C{codigo}A{argumento} {texto}".strip()
    if mensaje[14] == 10:
        linea, columna = struct.unpack_from(">ii", mensaje, 15)
        return f"Excepción en línea {linea}, columna {columna}: {mensaje[23:].decode(errors='replace')}"
    return None


def problema(estado):
    """Por qué el robot no puede moverse ahora, o None si puede."""
    if estado is None:
        return "Sin datos del robot"
    if estado["parada_emergencia"]:
        return "Parada de emergencia: suelta la seta y rearma en PolyScope"
    if estado["parada_proteccion"]:
        return "Parada de protección"
    if estado["modo_robot"] != RUNNING:
        return f"El robot no está en RUNNING (modo {estado['modo_robot']}): enciéndelo y suelta frenos"
    return None


def punto_valido(q):
    """6 números finitos entre -2π y 2π. Evita, por ejemplo, enviar grados como si fueran radianes."""
    return (isinstance(q, list) and len(q) == 6
            and all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 2 * math.pi
                    for v in q))


def distancia(q1, q2):
    """Mayor diferencia articular entre dos posturas (rad)."""
    return max(abs(a - b) for a, b in zip(q1, q2))


def grados(q):
    """Texto de una lista de ángulos en radianes, escritos en grados."""
    return "[" + ", ".join(f"{math.degrees(v):.1f}" for v in q) + "]"


def solo_ascii(texto):
    """PyBullet corta el texto en la primera letra no ASCII: á -> a, ñ -> n."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


class Robot:
    """Conexión con el robot por la interfaz primaria.

    Un hilo lee sin parar y guarda el último estado (con lock) y los errores (en una cola,
    para no perder ninguno). El bucle principal envía las órdenes por el mismo socket con
    enviar(): un hilo lee y otro escribe, porque una conexión TCP va en los dos sentidos.
    """

    def __init__(self, host):
        self.host = host
        self.sock = None                    # socket de la conexión actual. Lo cambia el hilo receptor;
                                            # asignar una variable es atómico, así que no necesita lock
        self.estado, self.instante = None, 0.0
        self.lock = threading.Lock()
        self.errores = queue.Queue()
        threading.Thread(target=self.recibir, daemon=True).start()

    def recibir(self):
        """Hilo receptor: conecta, lee, guarda el estado y reconecta si se corta."""
        while True:
            try:
                with conectar((self.host, PUERTO), timeout=TIMEOUT) as sock:
                    print(f"Conectado a {self.host}:{PUERTO}")
                    self.sock = sock
                    buffer = bytearray()
                    while True:
                        datos = sock.recv(TAM_RECV)
                        if not datos:
                            raise ConnectionError("el robot ha cerrado la conexión")
                        buffer += datos
                        for mensaje in cortar_mensajes(buffer):
                            if mensaje[4] == MSG_ESTADO:
                                estado = leer_estado(mensaje)
                                if estado:
                                    with self.lock:
                                        self.estado, self.instante = estado, time.monotonic()
                            elif mensaje[4] == MSG_ROBOT and leer_error(mensaje):
                                self.errores.put(leer_error(mensaje))
            except OSError as e:
                print(f"Conexión perdida: {e}. Reintento en {REINTENTO:.0f} s")
                time.sleep(REINTENTO)

    def leer(self):
        """Último estado, o None si hace más de SIN_DATOS segundos que no llega ninguno."""
        with self.lock:
            return self.estado if time.monotonic() - self.instante < SIN_DATOS else None

    def enviar(self, orden):
        """Envía una orden URScript. El salto de línea final es obligatorio."""
        print(">>", orden)
        try:
            self.sock.sendall((orden + "\n").encode())
            return True
        except (OSError, AttributeError) as e:              # conexión cerrada o nunca abierta
            print(f"No se pudo enviar: {e}")
            return False

    def dashboard(self, orden):
        """Orden de texto al dashboard server (29999). Devuelve su respuesta."""
        try:
            with conectar((self.host, PUERTO_DASHBOARD), timeout=TIMEOUT) as d:
                d.recv(1024)                                # mensaje de bienvenida
                d.sendall((orden + "\n").encode())
                respuesta = d.recv(1024).decode(errors="replace").strip()
        except OSError as e:
            respuesta = f"sin respuesta ({e})"
        print(f"[dashboard] {orden} -> {respuesta}")
        return respuesta


class Aplicacion:
    """Máquina de estados de la secuencia. En cada fotograma mira el estado y decide; nunca espera.

    REPOSO ──Enter──> ENVIANDO ──programa en marcha──> MOVIENDO ──ha llegado──> ENVIANDO (siguiente)
      │                  │                               │                       o REPOSO (último)
      │ P                └──────── error o parada ───────┴──> DETENIDO ──Enter──> REANUDANDO ──> ENVIANDO
      v
    PREVISUALIZANDO ──fin──> REPOSO               Esc (en cualquier modo) ──> REPOSO
    """

    def __init__(self, escena, fantasma, robot):
        self.escena, self.fantasma, self.robot = escena, fantasma, robot
        self.puntos = []                    # la secuencia: listas de 6 ángulos (rad)
        self.paso = 0                       # punto que se está ejecutando
        self.modo, self.t_modo = "REPOSO", time.monotonic()   # modo actual y cuándo se entró
        self.aviso = "Lleva el robot a una postura y pulsa Espacio"
        self.tramos = []                    # previsualización: [(desde, hasta, duración)]
        self.textos = {}                    # fila -> (id, texto, color) de los textos de la ventana

    def cambiar_modo(self, modo, aviso=None):
        self.modo, self.t_modo = modo, time.monotonic()
        if aviso:
            self.avisar(aviso)

    def avisar(self, texto):
        self.aviso = texto
        print(f"[{self.modo}] {texto}")

    # ----- teclas -----
    def tecla(self, t, estado):
        if t == ESC:
            # También en DETENIDO: tras un corte de red el robot puede seguir moviéndose
            if self.modo in MOVIENDO_ROBOT + ("DETENIDO",):
                self.robot.enviar("stopj(2)")
            self.cambiar_modo("REPOSO", "Cancelado")
        elif t == ord("e") and self.modo in ("ENVIANDO", "MOVIENDO"):
            self.avisar("Provocando una parada de protección (demostración)")
            self.robot.enviar("protective_stop()")
        elif t == ENTER and self.modo == "DETENIDO":
            self.reanudar(estado)
        elif self.modo != "REPOSO":
            return                                          # el resto de teclas, solo en reposo
        elif t == ESPACIO:
            if estado is None:
                self.avisar("Sin datos del robot: no se puede guardar")
            else:
                self.puntos.append(estado["q"])
                self.avisar(f"Punto {len(self.puntos)} guardado (grados): {grados(estado['q'])}")
        elif t == ord("b") and self.puntos:
            self.puntos.pop()
            self.avisar(f"Último punto borrado. Quedan {len(self.puntos)}")
        elif t == ord("p"):
            self.previsualizar(estado)
        elif t == ENTER:
            self.ejecutar(estado)
        elif t == ord("g"):
            self.guardar()
        elif t == ord("c"):
            self.cargar()

    def ejecutar(self, estado):
        if not self.puntos:
            self.avisar("No hay puntos: enseña alguno con Espacio")
        elif problema(estado):
            self.avisar(f"No se puede ejecutar. {problema(estado)}")
        else:
            while not self.robot.errores.empty():           # olvidar errores antiguos
                self.robot.errores.get()
            self.paso = 0
            self.enviar_punto()

    def enviar_punto(self):
        q = self.puntos[self.paso]
        if not punto_valido(q):                             # última comprobación antes de mover
            self.cambiar_modo("DETENIDO", f"Punto {self.paso + 1} no válido: {q}")
        elif self.robot.enviar(f"movej([{', '.join(f'{v:.5f}' for v in q)}], a={A}, v={V})"):
            self.cambiar_modo("ENVIANDO", f"Moviendo al punto {self.paso + 1}/{len(self.puntos)}")
        else:
            self.cambiar_modo("DETENIDO", "Sin conexión con el robot")

    def reanudar(self, estado):
        while not self.robot.errores.empty():               # olvidar los errores ya mostrados
            self.robot.errores.get()
        if estado is not None and estado["parada_proteccion"]:
            # Una parada de emergencia NO se desbloquea por red: tiene que hacerlo una persona
            self.robot.dashboard("unlock protective stop")
        self.cambiar_modo("REANUDANDO", f"Reanudando en el punto {self.paso + 1}")

    def previsualizar(self, estado):
        if not self.puntos:
            self.avisar("No hay puntos que previsualizar")
            return
        inicio = estado["q"] if estado else self.puntos[0]
        # Cada tramo dura lo que tardaría a velocidad V la articulación que más se mueve
        self.tramos = [(a, b, max(distancia(a, b) / V / ACELERAR_PREVIEW, 0.3))
                       for a, b in zip([inicio] + self.puntos[:-1], self.puntos)]
        self.cambiar_modo("PREVISUALIZANDO", f"Previsualizando {len(self.puntos)} puntos")

    def postura_preview(self):
        """Postura del fantasma en este instante de la previsualización (None si ya acabó)."""
        t = time.monotonic() - self.t_modo
        for a, b, duracion in self.tramos:
            if t < duracion:
                f = t / duracion                            # 0 al empezar el tramo, 1 al acabar
                return [x + (y - x) * f for x, y in zip(a, b)]
            t -= duracion
        return None

    def guardar(self):
        try:
            # Un punto por línea, para que el fichero se lea a simple vista
            FICHERO_SECUENCIA.write_text("[\n" + ",\n".join(json.dumps(q) for q in self.puntos)
                                         + "\n]\n", encoding="utf-8")
            self.avisar(f"{len(self.puntos)} puntos guardados en {FICHERO_SECUENCIA.name}")
        except OSError as e:
            self.avisar(f"No se pudo guardar: {e}")

    def cargar(self):
        try:
            puntos = json.loads(FICHERO_SECUENCIA.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.avisar(f"No se pudo cargar: {e}")
            return
        if not isinstance(puntos, list) or not all(punto_valido(q) for q in puntos):
            self.avisar(f"{FICHERO_SECUENCIA.name} tiene puntos no válidos: no se carga")
            return
        self.puntos = puntos
        self.avisar(f"{len(puntos)} puntos cargados de {FICHERO_SECUENCIA.name}")

    # ----- un fotograma -----
    def actualizar(self, estado):
        """Avanza la máquina de estados según lo que publica el robot."""
        t = time.monotonic() - self.t_modo                  # tiempo en el modo actual
        error = None if self.robot.errores.empty() else self.robot.errores.get()

        if self.modo in ("ENVIANDO", "MOVIENDO"):
            motivo = error or problema(estado)
            objetivo = self.puntos[self.paso]
            if motivo:
                self.cambiar_modo("DETENIDO", motivo)
            elif self.modo == "ENVIANDO":
                # 1. Esperar a que el programa arranque (isProgramRunning a verdadero)
                if estado["programa_en_marcha"]:
                    self.cambiar_modo("MOVIENDO")
                elif t > TIEMPO_ARRANQUE:
                    if distancia(estado["q"], objetivo) < TOLERANCIA:
                        self.punto_alcanzado()              # ya estaba en el punto: acabó enseguida
                    else:
                        self.cambiar_modo("DETENIDO", f"La orden no ha arrancado en {TIEMPO_ARRANQUE:.0f} s "
                                                      "(¿modo remoto? ¿orden rechazada?)")
            elif not estado["programa_en_marcha"]:
                # 2. El programa ha terminado: 3. comprobar que ha llegado al punto
                d = distancia(estado["q"], objetivo)
                if d < TOLERANCIA:
                    self.punto_alcanzado()
                else:
                    self.cambiar_modo("DETENIDO", f"El programa terminó a {d:.3f} rad del punto {self.paso + 1}")

        elif self.modo == "REANUDANDO":
            if problema(estado) is None:
                self.enviar_punto()                         # reenviar el punto pendiente
            elif t > 5:
                self.cambiar_modo("DETENIDO", f"No se puede reanudar. {problema(estado)}")

        elif self.modo == "PREVISUALIZANDO":
            if self.postura_preview() is None:
                self.cambiar_modo("REPOSO", "Previsualización terminada. Enter: ejecutar")

        elif error:
            self.avisar(error)                              # un error fuera de una ejecución

    def punto_alcanzado(self):
        print(f"Punto {self.paso + 1} alcanzado")
        self.paso += 1
        if self.paso < len(self.puntos):
            self.enviar_punto()
        else:
            self.cambiar_modo("REPOSO", f"Secuencia completada ({len(self.puntos)} puntos)")

    # ----- dibujo -----
    def dibujar(self, estado):
        if estado is not None:
            self.escena.move_robot(0, list(estado["q"]))    # una copia: move_robot alarga la lista

        # El fantasma enseña lo que va a hacer el robot
        if self.modo == "PREVISUALIZANDO":
            fantasma = self.postura_preview()
        elif self.modo != "REPOSO":
            fantasma = self.puntos[self.paso]               # el punto al que va (o iba)
        elif self.puntos:
            fantasma = self.puntos[-1]                      # el último punto enseñado
        else:
            fantasma = estado["q"] if estado else None      # sin puntos: encima del robot
        if fantasma is not None:
            self.escena.move_robot(self.fantasma, list(fantasma))

        # Cinco líneas de texto encima del robot: estado, aviso, puntos y ayuda
        cabecera = self.modo
        if self.modo in ("ENVIANDO", "MOVIENDO"):
            cabecera += f" {self.paso + 1}/{len(self.puntos)}"
        if problema(estado):
            cabecera += " - " + ("SIN DATOS" if estado is None else "ROBOT NO LISTO")
        rojo = self.modo == "DETENIDO" or problema(estado) is not None
        color = [0.8, 0, 0] if rojo else [0, 0, 0]
        lineas = [(cabecera, color, 1.8), (self.aviso[:60], color, 1.3),
                  (f"Puntos: {len(self.puntos)}", [0, 0, 0], 1.3)]
        lineas += [(texto, [0.3, 0.3, 0.3], 1.2) for texto in AYUDA[self.modo]]
        lineas += [("", [0, 0, 0], 1.2)] * (5 - len(lineas))     # borrar líneas de ayuda que sobran
        for fila, (texto, color, tam) in enumerate(lineas):
            self.escribir(fila, solo_ascii(texto), color, tam)

    def escribir(self, fila, texto, color, tam):
        """Una línea de texto encima del robot. Solo se redibuja si ha cambiado (si no, parpadea)."""
        anterior = self.textos.get(fila)
        if anterior and anterior[1:] == (texto, color):
            return
        reemplazar = {"replaceItemUniqueId": anterior[0]} if anterior else {}
        id_ = p.addUserDebugText(texto, [0.25, -0.25, 1.0 - 0.08 * fila], color, textSize=tam, **reemplazar)
        self.textos[fila] = (id_, texto, color)


def teclas():
    """Teclas pulsadas desde el último fotograma, en minúscula (por si está Bloq Mayús)."""
    return [t + 32 if ord("A") <= t <= ord("Z") else t
            for t, estado in p.getKeyboardEvents().items() if estado & p.KEY_WAS_TRIGGERED]


def main():
    if not (0 < V <= 0.3 and 0 < A <= 0.5):
        raise SystemExit("V y A superan los límites de la práctica (0,3 rad/s y 0,5 rad/s²)")
    nombre, host = leer_robot()
    print(f"Robot: {nombre} ({host}:{PUERTO})")

    escena = UR3eRG2()                                      # robot 0: el real
    fantasma = escena.spawn_robot([0, 0, 0, 0, 0, 0])       # en la misma base
    for enlace in range(-1, p.getNumJoints(fantasma)):      # -1 es la base
        p.changeVisualShape(fantasma, enlace, rgbaColor=[0.2, 0.6, 1.0, 0.35])   # azul transparente
    p.configureDebugVisualizer(p.COV_ENABLE_KEYBOARD_SHORTCUTS, 0)   # que PyBullet no use nuestras teclas
    p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)                  # ocultar los paneles laterales
    p.resetDebugVisualizerCamera(cameraDistance=1.6, cameraYaw=50, cameraPitch=-25,
                                 cameraTargetPosition=[0, 0, 0.4])   # cerca del robot y de los textos

    robot = Robot(host)
    app = Aplicacion(escena, fantasma, robot)
    try:
        while True:
            estado = robot.leer()
            for t in teclas():
                app.tecla(t, estado)
            app.actualizar(estado)
            app.dibujar(estado)
            time.sleep(1 / FPS)
    finally:
        # Salga como salga el programa (Ctrl+C, ventana cerrada o un fallo), si el robot
        # se estaba moviendo por una orden nuestra, se para
        if app.modo in MOVIENDO_ROBOT:
            robot.enviar("stopj(2)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Fin")
    except p.error:                                         # PyBullet sin ventana
        print("Ventana de PyBullet cerrada. Fin")
