# Guía para leer el código

Esta guía explica el código de la práctica en el orden en que conviene leerlo. Ábrela en un
panel de VS Code y el fichero del que habla en otro. Las referencias son del tipo
`pasarela.py:113` (fichero y línea). Si cambias el código, las líneas pueden moverse un poco:
busca la función por su nombre.

Cada sección tiene tres tipos de bloques:

- **Qué hace**: el código explicado línea a línea o bloque a bloque.
- **Por qué así**: la decisión que hay detrás y qué pasaría si se hiciera de otra forma.
- **Pruébalo**: un experimento corto para verlo funcionar (o fallar) con tus propios ojos.

Orden recomendado:

1. [El mapa: qué pieza hace qué](#1-el-mapa)
2. [Lo que el código da por sabido](#2-lo-que-el-código-da-por-sabido)
3. [La pasarela: `pasarela/pasarela.py`](#3-la-pasarela-pasarelapasarelapy)
4. [El cliente: `cliente/transporte.py`](#4-el-cliente-clientetransportepy)
5. [Los scripts de la práctica de TCP/IP](#5-los-scripts-de-la-práctica-de-tcpip)
6. [Las herramientas: `medir.py`, `eco.py`, `prueba_pasarela.py`](#6-las-herramientas)
7. [La PKI y sus scripts](#7-la-pki-y-sus-scripts)
8. [Recorridos completos, paso a paso](#8-recorridos-completos)
9. [Ejercicios para comprobar que lo entiendes](#9-ejercicios)
10. [Preguntas que te puede hacer el profesor](#10-preguntas-del-profesor)

---

## 1. El mapa

```
                cliente                                pasarela                      robot
 ┌───────────────────────────────┐        ┌──────────────────────────────┐     ┌─────────┐
 │ primaria.py                   │        │ pasarela.py                  │     │ URSim   │
 │   leer_robot()  -> IP robot   │        │                              │     │         │
 │   conectar((IP, 30001))  ─────┼─ TLS ─>│ aceptar() -> atender()       │     │         │
 │   recv / cortar_mensajes ...  │  3000  │   1 handshake                │     │         │
 │                               │        │   2 ¿CN autorizado?          │     │         │
 │ transporte.py                 │        │   3 create_connection ───────┼─TCP>│ 30001   │
 │   lee cliente.json            │        │   4 reenviar(): select()     │     │         │
 │   30001 -> 3000 (puertos)     │        │       cliente -> robot       │     │         │
 │   wrap_socket (TLS)           │        │       robot -> cliente       │     │         │
 │   SocketTLS: mensajes + Lock  │        │   5 cierra y registra        │     │         │
 └───────────────────────────────┘        └──────────────────────────────┘     └─────────┘
```

Hay cuatro piezas, y cada una sabe lo mínimo:

| Pieza | Sabe | No sabe |
|---|---|---|
| `primaria.py`, `ensenar_reproducir.py` | Cómo son los mensajes del robot | Que existe TLS o una pasarela |
| `transporte.py` | Cómo abrir una conexión TLS y a qué puerto de la pasarela va cada puerto del robot | Qué hay dentro de los mensajes |
| `pasarela.py` | Quién está autorizado y a qué robot va cada puerto | Qué hay dentro de los mensajes |
| La PKI (`pki/`) | Quién es quién (certificados firmados por la CA) | Nada del robot |

La idea clave de toda la práctica: **la pasarela no interpreta nada**. Copia bytes. Por eso
el mismo programa vale para la interfaz primaria (binaria), para el dashboard (texto) o para el
servidor de eco: no le importa lo que pase por ella.

### El viaje de un `movej`

Para fijar el mapa, sigue lo que le pasa a una orden `movej(...)` que envía `ensenar_reproducir.py`:

1. `Robot.enviar()` (`ensenar_reproducir.py:232`) hace `self.sock.sendall(b"movej(...)\n")`.
   Ese `sock` lo devolvió `conectar()`, así que es un `SocketTLS`.
2. `SocketTLS.sendall()` (`transporte.py:90`) coge el cerrojo y llama al `sendall` de `ssl`.
   OpenSSL cifra los bytes en uno o varios **registros TLS** y los manda por el socket TCP al
   puerto 3000 de la pasarela. En Wireshark, en `tcp.port == 3000`, solo se ve `Application Data`.
3. En la pasarela, el hilo de ese cliente está en `reenviar()` (`pasarela.py:150`) esperando en
   `select()`. El socket TLS se vuelve legible y `select()` lo devuelve en `listos`.
4. `origen.recv(TAM_RECV)` (`pasarela.py:168`) le pide a OpenSSL los datos: OpenSSL comprueba
   la etiqueta de autenticación del registro (si alguien lo hubiera tocado por el camino, aquí
   daría error) y devuelve los bytes en claro: `b"movej(...)\n"`.
5. `destino.sendall(datos)` (`pasarela.py:176`) los escribe tal cual en el socket TCP del robot.
   En Wireshark, en `tcp.port == 30001`, se lee `movej(...)` en claro.
6. El robot ejecuta la orden y sigue mandando su estado 10 veces por segundo. Esos bytes hacen
   el camino inverso: `select()` devuelve el socket del robot, `recv` del robot, `sendall` al TLS
   del cliente (cifrado), y en el cliente `SocketTLS.recv()` lo descifra y se lo da al hilo
   receptor de `ensenar_reproducir.py`, que lo trocea con `cortar_mensajes` como siempre.

---

## 2. Lo que el código da por sabido

Si algo de esto no lo tienes claro, léelo antes: el código lo usa sin explicarlo.

### TCP es un flujo de bytes, no de mensajes

`recv(65536)` devuelve **lo que haya llegado**, hasta 65536 bytes: medio mensaje, uno, o tres y
pico. Por eso tu práctica de TCP/IP tenía `cortar_mensajes()` con un buffer. La pasarela no
necesita nada de eso: copia trozos sin saber dónde empieza cada mensaje (requisito P12), y el
que trocea es el cliente, igual que antes.

`recv()` devuelve `b""` (0 bytes) **solo** cuando el otro extremo ha cerrado. Por eso todos los
bucles del código terminan con `if not datos:`.

### TLS envuelve un socket TCP

El módulo `ssl` no abre conexiones. El procedimiento es siempre:

```python
ctx = ssl.SSLContext(...)               # 1. configurar: versiones, certificados, qué se exige
tcp = socket.create_connection(...)     # 2. conexión TCP normal
tls = ctx.wrap_socket(tcp, ...)         # 3. envolverla: aquí se hace el handshake
tls.sendall(...); tls.recv(...)         # 4. usarla igual que un socket normal
```

El objeto que devuelve `wrap_socket` es un `ssl.SSLSocket`. Tiene los mismos métodos que un
socket (`recv`, `sendall`, `close`, `settimeout`, `with ...`) y además `version()`, `cipher()`,
`getpeercert()`, `pending()` y `unwrap()`.

### El contexto: dónde se decide la seguridad

Todo lo que se exige se configura en el `SSLContext`, antes de conectar:

| Atributo / método | Qué decide |
|---|---|
| `SSLContext(PROTOCOL_TLS_SERVER)` | Contexto de servidor: **no** pide certificado al cliente salvo que se diga |
| `SSLContext(PROTOCOL_TLS_CLIENT)` | Contexto de cliente: **ya** exige certificado al servidor y comprueba su nombre/IP |
| `minimum_version = TLSv1_3` | Rechaza TLS 1.2 y anteriores |
| `load_cert_chain(crt, key, password)` | La identidad propia: certificado y clave privada |
| `load_verify_locations(cafile)` | En qué CA se confía para validar al otro (el «ancla de confianza») |
| `verify_mode = CERT_REQUIRED` | En el servidor: exige certificado al cliente |

Un `SSLContext` creado así no confía en **ninguna** CA hasta que se le carga una. Por eso no se
usa `ssl.create_default_context()`: cargaría también las CA del sistema (las de las webs), y
cualquier certificado firmado por ellas sería válido (requisito P5).

### Qué se comprueba al validar un certificado

Cuando un extremo recibe el certificado del otro, OpenSSL comprueba, en este orden:

1. **Cadena**: que esté firmado por una CA de las cargadas con `load_verify_locations`.
   Si no: `self-signed certificate in certificate chain` o `unable to get local issuer certificate`.
2. **Fechas**: que no esté caducado ni sea todavía futuro.
3. **Uso (EKU)**: que el del servidor tenga `serverAuth` y el del cliente `clientAuth`.
   Si no: `unsuitable certificate purpose` (es lo que le pasa a `cliente-mal`).
4. **Nombre** (solo el cliente, sobre el servidor): que la IP a la que se conecta esté en la SAN
   del certificado. Si no: `IP address mismatch`. Lo activa pasar `server_hostname=IP` a
   `wrap_socket`.

Lo que **no** comprueba TLS es si ese cliente puede usar el robot: eso es **autorización**, y lo
hace la pasarela después, mirando el CN (P7). `cliente-b` pasa la validación (su certificado es
correcto) pero no la autorización.

### TLS 1.3: el rechazo del cliente llega «tarde»

En TLS 1.3 el cliente termina su handshake (envía su certificado y su `Finished`) **antes** de
que el servidor lo valide. Así que, para el cliente, `wrap_socket` termina bien aunque luego el
servidor rechace su certificado. El rechazo le llega como alerta en la **primera lectura**
(requisito C9). Por eso los mensajes de «tu certificado ha sido rechazado» están en
`SocketTLS.recv()` y no en `conectar()`.

### Cierre ordenado: close_notify

Cerrar TLS «bien» es enviar una alerta `close_notify` antes de cerrar TCP. Así el otro sabe que
la conexión terminó a propósito y no por un corte (un atacante podría cortar TCP para truncar
datos). En Python: `tls.unwrap()` envía el `close_notify`. Cuando el otro lo recibe, su `recv()`
devuelve `b""`.

### Hilos, bloqueos y timeouts

- Una llamada como `accept()` o `recv()` **bloquea** el hilo hasta que pasa algo.
- `settimeout(t)` hace que, si en `t` segundos no pasa nada, lance `TimeoutError`. Se usa para
  que los hilos no se queden esperando para siempre y puedan mirar si tienen que terminar.
- `select.select([a, b], [], [], t)` espera a la vez a **varios** sockets y devuelve los que
  tienen datos para leer (o una lista vacía si pasan `t` segundos).
- `threading.Event` es una bandera que se puede activar desde un hilo y consultar desde otros
  sin peligro: `parar.set()`, `parar.is_set()`.

### Las excepciones están anidadas

```
OSError
 ├── TimeoutError              (socket.timeout es lo mismo)
 ├── ConnectionError
 │    ├── ConnectionRefusedError
 │    └── ConnectionResetError
 └── ssl.SSLError
      ├── ssl.SSLCertVerificationError
      └── ssl.SSLEOFError ...
```

Un `except OSError` captura también `TimeoutError` y `SSLError`. Por eso, en el código, los
`except` más concretos van **antes** que `except OSError`: si fuera al revés, nunca se llegaría
a ellos.

---

## 3. La pasarela: `pasarela/pasarela.py`

Léela de arriba abajo; está ordenada en el mismo orden en que se ejecuta.

### 3.1 Cabecera e imports (`pasarela.py:1-38`)

**Qué hace.** El docstring resume el programa: qué es, qué hilos tiene y cómo se arranca. Todos
los `import` son de la biblioteca estándar: no hay que instalar nada.

Las tres constantes (`pasarela.py:34-36`):

| Constante | Valor | Para qué |
|---|---|---|
| `TAM_RECV` | 65536 | Máximo de bytes por `recv()`. Es el tamaño del «cubo» con el que se copia, no el de un mensaje. Un registro TLS lleva como mucho 16 kB, así que cabe entero |
| `ESPERA` | 1 s | Cada cuánto vuelve cada hilo de su espera para mirar si hay que parar |
| `TIEMPO_ENVIO` | 5 s | Si un `sendall` lleva 5 s sin poder enviar (el otro no lee), se da la conexión por muerta |

`log = logging.getLogger("pasarela")` (`pasarela.py:38`): el registro. Se configura en `main()`
para escribir en fichero y en pantalla a la vez.

**Por qué así.** Las constantes son de diseño (cómo funciona el programa) y van en el código.
Lo que depende del laboratorio (IP, puertos, rutas de ficheros, autorizados) va en el JSON.

### 3.2 `leer_config()` (`pasarela.py:41-53`)

**Qué hace.**

- `pasarela.py:43`: si se arranca con un argumento (`python pasarela.py otra.json`) usa ese
  fichero; si no, `pasarela.json` de la misma carpeta que el script (`Path(__file__)`), esté
  donde esté la terminal.
- `pasarela.py:47-48`: las rutas de ficheros (`ca`, `certificado`, `clave`, `registro`) se
  convierten en rutas **relativas a la carpeta del JSON**. Por eso en el JSON pone
  `"../pki/pasarela/pasarela.crt"` y funciona aunque copies la carpeta a otro PC.
  `expanduser()` convierte `~` en tu carpeta de usuario. Detalle de `pathlib`: `carpeta / ruta`
  devuelve `ruta` tal cual si ya es absoluta.
- `pasarela.py:49-50`: lee cada campo obligatorio sin hacer nada con él. Si falta alguno salta
  un `KeyError` **aquí**, al arrancar, y no a mitad de una conexión.
- `pasarela.py:52-53`: cualquier problema (fichero que no existe, JSON mal escrito, campo que
  falta) termina el programa con un mensaje claro en lugar de una traza de Python.

**Pruébalo.** Quita la coma de una línea de `pasarela.json` y arranca: verás
`pasarela.json no válido: JSONDecodeError(...)` con la línea y columna del error.

### 3.3 `crear_contexto()` (`pasarela.py:56-68`)

Es la función más importante para la seguridad: casi todos los requisitos de TLS se cumplen
aquí, con una línea cada uno.

**Qué hace, línea a línea.**

- `pasarela.py:58`: la contraseña de la clave privada. `os.environ.get("PASARELA_CLAVE")`
  devuelve el valor de la variable o `None`; si es `None` (o está vacía), el `or` llama a
  `getpass.getpass(...)`, que la pide sin mostrarla. **Nunca** está en el código ni en el JSON (P2).
- `pasarela.py:59`: contexto de **servidor**.
- `pasarela.py:60`: solo TLS 1.3 (P3). Un cliente que solo hable TLS 1.2 recibe la alerta
  `protocol version` en el handshake.
- `pasarela.py:62`: la identidad de la pasarela. Si la contraseña es incorrecta, o la clave no
  corresponde al certificado, lanza `ssl.SSLError`.
- `pasarela.py:63`: el ancla de confianza: **solo** `ca.crt` (P5).
- `pasarela.py:64-66`: si algo de lo anterior falla, el programa termina explicando el motivo
  más probable.
- `pasarela.py:67`: `CERT_REQUIRED`: exige certificado a todos los clientes (P4). Sin esta línea,
  un servidor no pide certificado y cualquiera podría conectarse.
- P6 (rechazar certificados sin `clientAuth`) no necesita código: OpenSSL lo comprueba solo en
  un contexto de servidor. La prueba con `cliente-mal` lo demuestra.

**Por qué así.** Un solo contexto para toda la pasarela, creado al arrancar. Lo usan todos los
hilos, pero solo para leerlo (`wrap_socket`), que es seguro. Así la contraseña se pide una vez.

**Pruébalo.**
- Arranca la pasarela y escribe mal la contraseña: `No se pudo cargar el certificado... (¿contraseña incorrecta...?)`.
- Comenta la línea 67, arranca, y conecta con `python3 cliente/medir.py --sin-cert`: el cliente
  sin certificado **entra**. Vuelve a dejarla como estaba.

### 3.4 `cerrar_tls()` (`pasarela.py:71-78`)

**Qué hace.** El cierre ordenado de una conexión TLS:

- `pasarela.py:74`: un tiempo límite corto, porque `unwrap()` espera un momento a que el otro
  conteste con su propio `close_notify`.
- `pasarela.py:75`: `unwrap()` envía `close_notify`.
- `pasarela.py:76-77`: si el otro ya se había ido (cortó sin `close_notify`, o mandó un RST),
  `unwrap()` lanza una excepción. Da igual: se ignora y se cierra el socket de todas formas.
- `pasarela.py:78`: `close()` libera el socket.

**Por qué así.** Está en una función porque se usa en cuatro sitios: cliente no autorizado, robot
no disponible, fin normal de la conexión y parada de la pasarela.

### 3.5 La clase `Pasarela` y `arrancar()` (`pasarela.py:81-99`)

**Qué hace.**

- `pasarela.py:82-84`: la clase guarda lo que comparten todos los hilos: la configuración, el
  contexto TLS y el `Event` `parar`. Nada más se comparte, así que no hace falta ningún `Lock`.
- `arrancar()` recorre las `rutas` del JSON. Por cada una:
  - `pasarela.py:91`: `socket.create_server((ip_escucha, puerto))` crea el socket de escucha
    **en esa IP concreta** (P1). Si se pusiera `""` o `"0.0.0.0"` escucharía en todas las
    interfaces del PC (wifi, Tailscale, docker...), y cualquiera de esas redes podría intentar
    conectarse. `create_server` también activa `SO_REUSEADDR`, para poder rearrancar la pasarela
    enseguida sin esperar a que el sistema libere el puerto.
  - `pasarela.py:92-93`: si el puerto está ocupado (otra pasarela, un `s_server` olvidado) o la
    IP no es de este PC (cable desconectado), termina explicándolo.
  - `pasarela.py:94`: `accept()` volverá cada segundo aunque no llegue nadie (para mirar `parar`).
  - `pasarela.py:96`: arranca un hilo `aceptar` para esta ruta. El `name=` sale en las trazas y
    ayuda a depurar.

**Pruébalo.** Arranca la pasarela y en otra terminal mira qué escucha y dónde:

```bash
ss -ltnp | grep python
# LISTEN 0 ... 10.172.18.70:3000 ...      <- solo en la IP del cable, no en 0.0.0.0
```

Intenta conectar por la otra IP del mismo PC:

```bash
python3 -c "import socket; socket.create_connection(('127.0.0.1', 3000))"
# ConnectionRefusedError: [Errno 111] Connection refused      <- P1
```

### 3.6 `aceptar()` (`pasarela.py:101-111`)

**Qué hace.** Un bucle por ruta:

- `pasarela.py:103`: mientras no se haya pedido parar.
- `pasarela.py:105`: espera un cliente nuevo. Devuelve el socket TCP de la conexión y la
  dirección del cliente `(ip, puerto)`; el puerto del cliente no interesa (`_`).
- `pasarela.py:106-107`: si pasa un segundo sin clientes, vuelve arriba a mirar `parar`.
- `pasarela.py:108-109`: si el socket de escucha se cierra, sale.
- `pasarela.py:110`: lanza **un hilo por cliente** con `atender`. El hilo de `aceptar` vuelve
  enseguida a esperar al siguiente: un cliente lento no retrasa a los demás (P10).
- `pasarela.py:111`: al salir del bucle, cierra el socket de escucha: deja de aceptar (P16).

**Por qué así.** El handshake se hace en el hilo del cliente, **no** aquí. Si se hiciera aquí,
un cliente que se conecta y no habla bloquearía `aceptar` 10 s, y nadie más podría entrar
mientras tanto. La prueba P10 conecta un cliente mudo y, a la vez, cuatro normales: los cuatro
funcionan en 0,06 s.

### 3.7 `atender()` (`pasarela.py:113-148`): los 4 pasos de la pasarela

Es el corazón del programa. Cada paso que falla termina la función con `return`, después de
registrar el motivo y cerrar lo que estuviera abierto.

**Paso 1: handshake** (`pasarela.py:115-123`)

- `pasarela.py:117`: tiempo límite para el handshake (P8). El valor viene del JSON (10 s).
- `pasarela.py:119`: `wrap_socket(conexion, server_side=True)` hace el handshake completo:
  envía su certificado, pide el del cliente, lo valida (cadena, fechas, `clientAuth`) y acuerda
  las claves. Si todo va bien devuelve el `SSLSocket`.
- `pasarela.py:120-123`: si falla, el motivo exacto viene en la excepción. Ejemplos reales del
  registro:

  | Cliente | Motivo en el registro |
  |---|---|
  | Sin certificado | `PEER_DID_NOT_RETURN_A_CERTIFICATE` |
  | Certificado con serverAuth | `certificate verify failed: unsuitable certificate purpose` |
  | Valida la pasarela con otra CA | `TLSV1_ALERT_UNKNOWN_CA` (la alerta la manda el cliente) |
  | Solo TLS 1.2 | `UNSUPPORTED_PROTOCOL` |
  | Se conecta y no habla | `The handshake operation timed out` |

  En todos estos casos **no** se abre conexión con el robot.

**Paso 2: autorización** (`pasarela.py:125-132`)

- `pasarela.py:126`: `getpeercert()` devuelve el certificado del cliente, ya validado, como
  diccionario. El campo `subject` tiene una forma rara: una tupla de tuplas de pares,
  `((('commonName', 'cliente-a'),),)`. La comprensión `dict(campo[0] for campo in ...)` lo
  convierte en `{'commonName': 'cliente-a'}`.
- `pasarela.py:128`: `quien` junta lo que el registro debe llevar en cada línea (P9): IP, CN,
  versión de TLS y suite. Así todas las líneas posteriores de esta conexión lo incluyen.
- `pasarela.py:129-132`: si el CN no está en `autorizados` (P7), se registra y se cierra **con
  close_notify**: el handshake ya se hizo, así que el cierre es TLS. El cliente lo verá como
  `b""` en su primera lectura.

**Paso 3: conectar con el robot** (`pasarela.py:134-142`)

- `pasarela.py:135`: el destino sale de la ruta por la que entró el cliente (P11).
- `pasarela.py:137`: conexión TCP normal con el robot, con tiempo límite (`tiempo_robot`).
- `pasarela.py:138-141`: si el robot no está (P13), se registra con nivel `ERROR` y se cierra al
  cliente con `close_notify`. Fíjate en el orden: primero se autentica y autoriza al cliente, y
  solo entonces se toca el robot. Un cliente no autorizado nunca llega a provocar una conexión
  con el robot.

**Paso 4: reenviar y cerrar** (`pasarela.py:144-148`)

- `reenviar()` no vuelve hasta que la conexión termina, y devuelve el motivo.
- Después se cierra el robot (`close`, es TCP), se cierra el cliente con `cerrar_tls()` y se
  registra el motivo (P14, P15).

### 3.8 `reenviar()` (`pasarela.py:150-179`): copiar en los dos sentidos

**Qué hace.**

- `pasarela.py:159-160`: los dos sockets con `TIEMPO_ENVIO` (5 s). No afecta a la espera de
  datos (de eso se encarga `select`), solo a `sendall`: si el otro extremo no lee y su buffer se
  llena, `sendall` se quedaría bloqueado; así, a los 5 s, lanza `TimeoutError` y se cierra.
- `pasarela.py:161`: un diccionario para no repetir código: dado el socket del que han llegado
  datos, dice a qué socket hay que mandarlos y cómo se llama el origen (para el registro).
- `pasarela.py:162`: el bucle dura mientras no se pida parar (P16).
- `pasarela.py:164`: la línea más delicada.
  - `select.select([tls, robot], [], [], ESPERA)` espera hasta 1 s a que **alguno** de los dos
    tenga datos y devuelve la lista de los que tienen (`[0]`: la primera de las tres listas que
    devuelve `select`, la de «listos para leer»).
  - `tls.pending()`: TLS trabaja por registros. Puede pasar que OpenSSL ya haya leído y
    descifrado datos del socket que todavía no le ha entregado a Python. Esos datos ya no están
    en el socket, así que `select` no los ve y se quedaría esperando. Si `pending()` no es 0,
    se lee del TLS directamente sin esperar a `select`.
- `pasarela.py:165`: si pasa 1 s sin datos, `listos` está vacío, el `for` no hace nada y se
  vuelve a mirar `parar`.
- `pasarela.py:168`: lee lo que haya. Con TLS, `recv` devuelve los datos ya descifrados y
  comprobados.
- `pasarela.py:169-170`: puede pasar que `select` vea bytes en el socket TLS pero aún no un
  registro completo (el resto viene de camino). Entonces `recv` espera, y si en 5 s no llega
  el resto lanza `TimeoutError`; se vuelve a intentar.
- `pasarela.py:171-174`: un error al leer o `b""` (cierre) terminan la conexión con su motivo
  (P15).
- `pasarela.py:176`: `sendall` envía **todos** los bytes (un `send` normal podría enviar solo
  parte). Tal cual, sin mirarlos (P12).
- `pasarela.py:179`: si se sale del `while` es que se pidió parar.

**Por qué un hilo y `select`, y no dos hilos.** El enunciado sugiere dos hilos por cliente: uno
copia cliente→robot y otro robot→cliente. Se programó así primero. La prueba de estrés (4
clientes enviando 1 MB mientras reciben el eco) fallaba aproximadamente 1 de cada 20 rondas
con `[Errno 11] Resource temporarily unavailable`. El motivo: los dos hilos usan el mismo
`SSLSocket`, uno leyendo y otro escribiendo a la vez, y por debajo los dos usan el **mismo
objeto SSL de OpenSSL**, que no está hecho para usarse desde dos hilos a la vez. Con un socket
TCP normal sí se puede (por eso la práctica de TCP/IP no tenía este problema).

Con `select`, un solo hilo hace las dos cosas por turnos, y cada socket lo usa un único hilo.
La misma prueba pasa 20 de 20 rondas. Además el cierre es más simple: con dos hilos, cuando
uno termina hay que despertar al otro, que está bloqueado en un `recv`, y eso obliga a cerrar
sockets desde otro hilo.

¿Pierde algo? Mientras el hilo está enviando en un sentido no está leyendo del otro. Pero los
datos no se pierden: esperan en el buffer del sistema operativo y se leen en la siguiente
vuelta, microsegundos después.

**Pruébalo.** En `docs/diseno.md` está el razonamiento completo. Si quieres ver el fallo, pide
que te pase la versión con dos hilos y ejecuta la prueba automática varias veces.

### 3.9 `main()` (`pasarela.py:182-201`)

**Qué hace.**

1. `pasarela.py:183`: lee la configuración.
2. `pasarela.py:184-186`: el registro (P9), con dos salidas a la vez: el fichero del JSON
   (`FileHandler`) y la pantalla (`StreamHandler`). El formato añade fecha y hora y el nivel
   (`INFO`, `WARNING` para rechazos, `ERROR` para robot no disponible).
3. `pasarela.py:187`: crea el contexto (aquí se pide la contraseña) y la pasarela.
4. `pasarela.py:188`: abre los sockets y arranca los hilos `aceptar`.
5. `pasarela.py:190-194`: el hilo principal no tiene nada que hacer: duerme hasta que llega
   Ctrl+C, que en Python es la excepción `KeyboardInterrupt` (solo la recibe el hilo principal).
6. `pasarela.py:196-201`: la parada ordenada (P16):
   - `parar.set()` activa la bandera.
   - En menos de 1 s, cada hilo `aceptar` sale de su `accept()`, ve la bandera, sale del bucle y
     cierra su socket de escucha.
   - En menos de 1 s, cada hilo de cliente sale de `select()`, ve la bandera, `reenviar()`
     devuelve «parada de la pasarela» y `atender()` cierra el robot y manda `close_notify` al cliente.
   - Un cliente a mitad de handshake termina su handshake (máximo 10 s) y sigue por su camino;
     si llega a `reenviar`, sale enseguida.
   - `join()` espera a que todos los hilos terminen. Cuando terminan, escribe «Pasarela parada».

**Por qué así.** No se cierra ningún socket desde el hilo principal: cada hilo cierra lo suyo al
ver la bandera. Cerrar un socket mientras otro hilo lo está usando es una fuente clásica de
errores raros.

---

## 4. El cliente: `cliente/transporte.py`

`transporte.py` es todo lo nuevo del lado del cliente. Los scripts de la práctica de TCP/IP solo
llaman a `conectar()`.

### 4.1 Qué pasa al importarlo (`transporte.py:110-111`)

Lo primero que hay que saber: **al hacer `from transporte import conectar`, se ejecuta el final
del módulo**:

```python
CFG = leer_config()                                                     # transporte.py:110
CONTEXTO = crear_contexto(CFG) if CFG["modo"] == "pasarela" else None   # transporte.py:111
```

Es decir, al arrancar `primaria.py` se lee `cliente.json`, se miran las opciones de la línea de
órdenes y, en modo pasarela, se pide la contraseña **una sola vez**. Después, cada reconexión
del script reutiliza el mismo contexto. En modo directo no se crea contexto ni se pide nada.

### 4.2 `leer_config()` (`transporte.py:37-57`)

**Qué hace.**

- `transporte.py:40`: lee `cliente.json` de la carpeta de `transporte.py` (no de donde esté la terminal).
- `transporte.py:43-49`: las opciones de la línea de órdenes con `argparse`:
  - `add_help=False`: para no robarle `-h` al script.
  - `parse_known_args()`: coge solo las opciones que conoce y deja pasar el resto. Así no
    molesta si un script tuviera sus propias opciones.
- `transporte.py:50-56`: las opciones **sobrescriben** lo del JSON: `--directo` cambia el modo;
  `--sin-cert` borra certificado y clave; `--cert`, `--clave` y `--ca` cambian esos ficheros.

**Pruébalo.** `python3 cliente/medir.py --directo` mide sin pasarela, sin tocar el JSON (C7).

### 4.3 `crear_contexto()` (`transporte.py:60-74`)

**Qué hace.** El espejo de la pasarela, pero de cliente:

- `transporte.py:62`: `PROTOCOL_TLS_CLIENT` ya trae de serie `verify_mode = CERT_REQUIRED` y
  `check_hostname = True`: exige certificado a la pasarela, con `serverAuth`, y comprobará la
  IP contra la SAN (C3).
- `transporte.py:63`: solo TLS 1.3 (C4).
- `transporte.py:64`: rutas relativas a la carpeta del cliente, como en la pasarela.
- `transporte.py:66`: solo la CA del laboratorio (C3).
- `transporte.py:67-69`: el certificado propio (C2), con la contraseña de `CLIENTE_CLAVE` o
  pedida con `getpass` (C1). Con `--sin-cert` no se carga nada ni se pide contraseña.
- `transporte.py:73`: `sslsocket_class = SocketTLS`: a partir de aquí, cada `wrap_socket` de este
  contexto devuelve un `SocketTLS` (la clase de la sección siguiente) en lugar de un
  `SSLSocket` normal. Es la forma oficial de `ssl` de personalizar los sockets que crea.

### 4.4 `SocketTLS` (`transporte.py:77-107`)

Un `SSLSocket` normal con dos añadidos. Como hereda de `ssl.SSLSocket`, todo lo demás
(`close`, `settimeout`, `with`...) funciona igual.

**Añadido 1: mensajes de diagnóstico en `recv()`** (C8, C9)

- `transporte.py:98-103`: si la lectura lanza un `SSLError` cuyo motivo contiene `ALERT`, es que
  la pasarela ha mandado una alerta TLS: en la práctica, que ha rechazado el certificado del
  cliente (`TLSV13_ALERT_CERTIFICATE_REQUIRED` sin certificado,
  `SSLV3_ALERT_UNSUPPORTED_CERTIFICATE` con `serverAuth`). Se explica y se **vuelve a lanzar**
  la excepción (`raise`), para que el script haga lo de siempre (en `primaria.py`: «Conexión
  perdida... Reintento en 2 s»).
- `transporte.py:104-106`: `b""` justo después de conectar significa que la pasarela ha cerrado:
  cliente no autorizado o robot no disponible. El cliente no puede saber cuál de las dos: el
  motivo exacto está en el registro de la pasarela.

**Añadido 2: un `Lock` entre `recv()` y `sendall()`**

- `transporte.py:90-92` y `96-97`: `with self.cerrojo:` hace que un `recv` y un `sendall` del
  mismo socket nunca se ejecuten a la vez.
- Es el mismo problema que en la pasarela (sección 3.8): `ensenar_reproducir.py` tiene un hilo
  que lee el estado del robot sin parar y el hilo principal envía los `movej` por el mismo socket.
  Con TCP no importaba; con TLS fallaría de vez en cuando.
- El precio: `sendall` espera a que termine el `recv` en curso. Como el robot manda estado 10
  veces por segundo, esa espera es de menos de 0,1 s. Está marcado como `shortcut:` en el
  docstring porque depende de eso: con un servidor que tarde mucho en mandar datos, un `sendall`
  podría esperar hasta el `TIMEOUT` del `recv`.
- `self.cerrojo` se crea en `conectar()` (`transporte.py:142`) y no en un `__init__`, porque los
  `SSLSocket` no se construyen con `__init__` sino desde `wrap_socket`.

### 4.5 `conectar()` (`transporte.py:114-147`)

Recibe lo mismo que `socket.create_connection()`: `(host, puerto)` y `timeout`. Por eso se puede
sustituir sin tocar nada más en los scripts.

**Qué hace.**

- `transporte.py:116-117`: modo directo: exactamente lo que hacía la práctica de TCP/IP.
- `transporte.py:119-123`: modo pasarela. **El host que pasa el script se ignora** (es la IP del
  robot); se usa la IP de la pasarela. El puerto del robot se traduce con `puertos`
  (`"30001" -> 3000`). Las claves de un JSON siempre son texto, de ahí el `str(...)`.
- `transporte.py:124-131`: la conexión TCP con la pasarela, con dos mensajes distintos (C8):
  - `ConnectionRefusedError`: la máquina responde, pero nadie escucha en ese puerto
    (la pasarela no está arrancada).
  - Cualquier otro `OSError` (sobre todo `TimeoutError` o «No route to host»): la máquina no
    responde (IP equivocada, cable, red).
  - En los dos casos se imprime y se vuelve a lanzar, para que el script reintente.
- `transporte.py:133`: el handshake. `server_hostname=ip` es lo que hace que se compruebe que la
  IP está en la SAN del certificado de la pasarela. Por eso hay que conectarse a la IP de la SAN
  y no a `127.0.0.1`, aunque la pasarela esté en el mismo PC.
- `transporte.py:134-137`: `SSLCertVerificationError` = el certificado de la pasarela no vale
  (otra CA, IP que no coincide, caducado...). `verify_message` dice exactamente por qué (C8).
- `transporte.py:138-141`: cualquier otro fallo del handshake (por ejemplo, la pasarela solo
  acepta TLS 1.3 y el cliente no).
- `transporte.py:142`: crea el cerrojo de `SocketTLS`.
- `transporte.py:143-146`: lo que ve el operador al conectar (C5): versión, suite, CN de la
  pasarela y fecha de caducidad de su certificado.

---

## 5. Los scripts de la práctica de TCP/IP

`cliente/primaria.py` y `cliente/ensenar_reproducir.py` son copias de los de `Practica1UR`.
Para ver exactamente qué ha cambiado:

```bash
diff ~/Documents/Practica1UR/entregaURs/primaria.py cliente/primaria.py
diff ~/Documents/Practica1UR/entregaURs/ensenar_reproducir.py cliente/ensenar_reproducir.py
```

Los cambios son:

| Fichero | Línea | Antes | Ahora |
|---|---|---|---|
| `primaria.py` | 13 | `import socket` | (se quita: ya no se usa) |
| `primaria.py` | 20 | | `from transporte import conectar` |
| `primaria.py` | 140 | `socket.create_connection((host, PUERTO), timeout=TIMEOUT)` | `conectar((host, PUERTO), timeout=TIMEOUT)` |
| `ensenar_reproducir.py` | 38 | | `from transporte import conectar` |
| `ensenar_reproducir.py` | 202 | `socket.create_connection((self.host, PUERTO), ...)` | `conectar((self.host, PUERTO), ...)` |
| `ensenar_reproducir.py` | 241 | `socket.create_connection((self.host, PUERTO_DASHBOARD), ...)` | `conectar((self.host, PUERTO_DASHBOARD), ...)` |

Todo lo demás (`cortar_mensajes`, `leer_q`, `leer_estado`, la máquina de estados de enseñar y
reproducir...) es idéntico (C6). Esto funciona por tres motivos:

1. `conectar()` devuelve algo que se usa igual que un socket (`with`, `recv`, `sendall`).
2. TLS, como TCP, es un flujo de bytes: los mensajes del robot llegan troceados igual que antes,
   y `cortar_mensajes` los recompone igual.
3. Las constantes `PUERTO = 30001` y `PUERTO_DASHBOARD = 29999` siguen siendo los puertos
   **del robot**; la traducción al puerto de la pasarela la hace `transporte.py`.

Un detalle visible: `primaria.py` sigue imprimiendo «Conectado a 10.172.18.73:30001». Esa línea
es del script y usa la IP del robot de `robots.json`. La conexión real va a la pasarela; lo
confirma la línea `[TLS]` que aparece justo antes.

**Cómo encajan los errores con el bucle de reconexión.** Mira `primaria.py:138-169`: el bucle
`while True` con `except OSError` que imprime «Conexión perdida» y reintenta. Como todas las
excepciones de `transporte.py` son `OSError` (o hijas), ese bucle sigue funcionando: si la
pasarela no está, el cliente imprime el `[TLS] ...` que corresponde y reintenta cada 2 s.

---

## 6. Las herramientas

### 6.1 `cliente/medir.py` (C10)

**Qué hace.**

- Usa el dashboard (29999) porque es **petición-respuesta**: se envía `robotmode\n` y el robot
  contesta una línea. La interfaz primaria (30001) envía su estado sin que se le pida, así que no
  hay ninguna «petición» que cronometrar.
- `medir.py:25-33` `leer_linea()`: lee hasta el `\n`, porque una respuesta podría llegar en dos
  trozos (flujo de bytes, otra vez).
- `medir.py:39-41`: mide cuánto tarda en abrirse la conexión. En modo pasarela eso incluye TCP con
  la pasarela + handshake TLS + la conexión de la pasarela con el robot.
- `medir.py:44-48`: N peticiones, cada una cronometrada con `time.perf_counter()` (el reloj de
  más resolución de Python, pensado para medir intervalos).
- Usa `conectar()` de `transporte.py`, así que mide **el mismo camino** que usan los
  visualizadores, y admite las mismas opciones (`--directo`, `--sin-cert`...). Por eso también
  sirve como cliente de pruebas en la demostración: no abre ventana.

**Cómo leer los resultados.** Conectar cuesta unos 5 ms más por la pasarela (el handshake con
firmas RSA). Cada petición, unas décimas de ms más: cifrar, descifrar y un salto más.

### 6.2 `pasarela/eco.py`

El servidor de pruebas de la Fase 3: devuelve tal cual lo que recibe. Un hilo por conexión
(`eco.py:15-18`). Poniéndolo como `destino` en el JSON, la pasarela se prueba sin robot: si lo
que se envía vuelve idéntico, el reenvío en los dos sentidos funciona.

### 6.3 `pruebas/prueba_pasarela.py`

Una prueba automática de P1 a P16. Léela después de entender la pasarela: cada bloque de
`main()` está marcado con el requisito que comprueba (`# P1: ...`, `# P12: ...`).

- `main()` (línea 104) escribe un JSON de prueba en una carpeta temporal: la misma
  configuración que `pasarela.json`, pero con los puertos 3100-3102, `tiempo_handshake` de 2 s y
  tres rutas: dos al eco y una a un puerto cerrado (el 9, para P13). Después arranca `eco.py` y
  `pasarela.py` como procesos aparte, igual que lo harías tú a mano.
- `contexto()` (línea 41) y `conectar()` (línea 52): un cliente TLS mínimo que permite elegir
  certificado o forzar TLS 1.2.
- `eco_ok()` (línea 57): envía bytes al azar en trozos y comprueba que vuelven iguales. Envía y
  lee **por turnos**, por el mismo motivo de la sección 3.8.
- `eco_dos_sentidos()` y `estres()` (líneas 74 y 85): la prueba de estrés. Usa `asyncio`, que
  hace envío y recepción a la vez **en un solo hilo**, así que el cliente de prueba no tiene el
  problema de los dos hilos y lo que se prueba es solo la pasarela.
- `cerrada()` (línea 94): `True` si la pasarela corta la conexión (0 bytes o alerta TLS).
- P16 se prueba de verdad: se le manda `SIGINT` (lo mismo que Ctrl+C) a la pasarela con un
  cliente conectado, y se comprueba que el cliente recibe el `close_notify` (`b""`) y que el
  proceso termina con código 0.
- El `preexec_fn` que restaura `SIGINT` existe porque un proceso lanzado en segundo plano desde
  un shell hereda `SIGINT` ignorado, y Ctrl+C no le haría nada.

---

## 7. La PKI y sus scripts

Los comandos de OpenSSL y qué hace cada opción están en `docs/E1_pki.md`. Aquí solo lo que
toca al código:

- **Qué fichero usa cada programa**:

  | Fichero | Pasarela | Cliente | ¿Secreto? |
  |---|---|---|---|
  | `pki/publico/ca.crt` | Sí (para validar clientes) | Sí (para validar la pasarela) | No |
  | `pki/pasarela/pasarela.crt` + `.key` | Sí | No | La `.key` sí |
  | `pki/cliente-a/cliente-a.crt` + `.key` | No | Sí | La `.key` sí |
  | `pki/ca-privado/ca.key` | Nunca | Nunca | El que más |

  La pasarela no necesita los certificados de los clientes: le basta con la CA. Esa es la
  ventaja de una PKI: quien valida no conoce a cada equipo, solo a la CA que los firmó.
- **`pki/pasarela.ext`**: decide la SAN de la pasarela. Si cambia la IP, hay que volver a firmar
  (README, «Cambiar las IP»).
- **`pki/refirmar.sh`**: vuelve a emitir el certificado de la CA (con la misma clave) y a firmar
  los cuatro certificados. Se usó una vez, para corregir el CN de la CA. Comprueba la contraseña
  antes de tocar nada; si es incorrecta, no cambia ningún fichero.
- **`pki/registro.sh`**: genera la tabla de certificados del E1 leyendo los `.crt`.

---

## 8. Recorridos completos

Para cada caso: qué funciones se ejecutan, en qué orden, qué sale en el cliente, qué sale en el
registro y qué se ve en Wireshark. Sigue cada uno con el código abierto.

### 8.1 Conexión buena (cliente-a, robot disponible)

| # | Dónde | Qué pasa |
|---|---|---|
| 1 | `primaria.py:140` | `conectar(("10.172.18.73", 30001), timeout=5)` |
| 2 | `transporte.py:121` | 30001 → 3000 |
| 3 | `transporte.py:125` | TCP con 10.172.18.70:3000. Wireshark: SYN, SYN-ACK, ACK |
| 4 | `pasarela.py:105` | `accept()` devuelve la conexión; `pasarela.py:110` lanza el hilo `cliente-10.172.18.70` |
| 5 | `transporte.py:133` / `pasarela.py:119` | Handshake en los dos lados a la vez. Wireshark: Client Hello y Server Hello en claro, el resto `Application Data` (los certificados van cifrados) |
| 6 | `transporte.py:143-146` | Cliente: `[TLS] TLSv1.3, TLS_AES_256_GCM_SHA384. Pasarela: CN=pasarela-ur3e, caduca ...` |
| 7 | `pasarela.py:126-129` | CN = `cliente-a`: autorizado |
| 8 | `pasarela.py:137` | TCP con 10.172.18.73:30001. Wireshark (cable): SYN... |
| 9 | `pasarela.py:142` | Registro: `INFO ... CN=cliente-a TLSv1.3 TLS_AES_256_GCM_SHA384 conectado con el robot 10.172.18.73:30001` |
| 10 | `pasarela.py:145` | Entra en `reenviar()`. El robot empieza a mandar su estado, la pasarela lo copia al cliente |
| 11 | `primaria.py` | `recv` → `cortar_mensajes` → `q_actual (grados): [...]` |

### 8.2 cliente-b (certificado válido, no autorizado)

Pasos 1 a 6 iguales: el handshake **funciona**, porque el certificado de cliente-b es correcto.
Después:

| # | Dónde | Qué pasa |
|---|---|---|
| 7 | `pasarela.py:129-131` | CN = `cliente-b`, no está en `autorizados`. Registro: `WARNING ... CN=cliente-b TLSv1.3 ... rechazado: CN no autorizado`. `cerrar_tls()` envía `close_notify` |
| 8 | `transporte.py:104-106` | Primera lectura del cliente: `b""`. Cliente: `[TLS] La pasarela ha cerrado la conexión: cliente no autorizado o robot no disponible ...` |
| 9 | `primaria.py` | «Conexión perdida: el robot ha cerrado la conexión. Reintento en 2 s» y vuelve a empezar (y vuelve a ser rechazado) |

No hay paso 8 de 8.1: la pasarela nunca conecta con el robot. En Wireshark: handshake completo
en el 3000, unos pocos `Application Data` (el `close_notify` va cifrado) y el cierre TCP (FIN), y
nada en el 30001.

### 8.3 Cliente sin certificado

| # | Dónde | Qué pasa |
|---|---|---|
| 5 | `pasarela.py:119` | La pasarela pide certificado y el cliente no manda ninguno. `wrap_socket` lanza `SSLError`; OpenSSL envía antes la alerta `certificate required` |
| 6 | `pasarela.py:121` | Registro: `WARNING 10.172.18.70 rechazado en el handshake: [SSL: PEER_DID_NOT_RETURN_A_CERTIFICATE] ...` |
| 7 | `transporte.py:133` | Para el cliente, su handshake **ha terminado bien** (TLS 1.3): imprime la línea `[TLS] TLSv1.3...` |
| 8 | `transporte.py:98-100` | En la primera lectura llega la alerta: `[TLS] La pasarela ha rechazado el certificado del cliente (TLSV13_ALERT_CERTIFICATE_REQUIRED)` |

Este es el ejemplo de C9: el rechazo no aparece al conectar sino al leer.

### 8.4 Cliente con la CA de otro estudiante

El cliente valida a la pasarela con `otra-ca/ca.crt`, que no firmó `pasarela.crt`:

| # | Dónde | Qué pasa |
|---|---|---|
| 5 | `transporte.py:133-137` | `SSLCertVerificationError`. Cliente: `[TLS] Certificado de la pasarela NO válido: self-signed certificate in certificate chain`. El cliente envía la alerta `unknown ca` |
| 6 | `pasarela.py:121` | Registro: `rechazado en el handshake: [SSL: TLSV1_ALERT_UNKNOWN_CA] ...` |

Aquí el que rechaza es el cliente: protege al cliente de una pasarela falsa.

### 8.5 El robot se cae con un cliente conectado

| # | Dónde | Qué pasa |
|---|---|---|
| 1 | `pasarela.py:164` | `select` devuelve el socket del robot: hay «algo» (el FIN del robot) |
| 2 | `pasarela.py:168, 173-174` | `recv` del robot devuelve `b""` → `reenviar()` devuelve «el robot ha cerrado la conexión» (o, si el robot manda RST, «error al leer del robot: Connection reset by peer») |
| 3 | `pasarela.py:146-148` | Cierra el robot, `close_notify` al cliente. Registro: `INFO ... cerrado: el robot ha cerrado la conexión` |
| 4 | `transporte.py:104-106` | Cliente: `[TLS] La pasarela ha cerrado la conexión ...`, y `primaria.py` reintenta |
| 5 | `pasarela.py:137-141` | Al reintentar, la pasarela no puede conectar con el robot: `ERROR ... rechazado: robot 10.172.18.73:30001 no disponible ([Errno 111] Connection refused)` y `close_notify` al cliente (P13) |

Si el robot se queda mudo sin cerrar (cable cortado), el paso 1 no ocurre. Lo nota el cliente:
`primaria.py` no recibe nada en 5 s (`TIMEOUT`), su `recv` lanza `TimeoutError`, cierra y
reconecta; la pasarela ve que el cliente ha cerrado y, al reconectar, ya no puede abrir el robot
(paso 5). Está explicado como limitación en `docs/diseno.md`.

### 8.6 Ctrl+C en la pasarela con clientes conectados

| # | Dónde | Qué pasa |
|---|---|---|
| 1 | `pasarela.py:193` | `KeyboardInterrupt` en el hilo principal |
| 2 | `pasarela.py:196-197` | Registro: `Parando: ...`; `parar.set()` |
| 3 | `pasarela.py:103, 111` | Cada `aceptar` sale de su `accept()` (≤ 1 s), ve `parar` y cierra su socket de escucha |
| 4 | `pasarela.py:162, 179` | Cada cliente sale de `select()` (≤ 1 s) y `reenviar()` devuelve «parada de la pasarela» |
| 5 | `pasarela.py:146-148` | Cierra el robot, `close_notify` al cliente. Registro: `... cerrado: parada de la pasarela` |
| 6 | `pasarela.py:198-201` | `join()` de todos los hilos; registro: `Pasarela parada` |

---

## 9. Ejercicios

Hazlos con la pasarela arrancada en una terminal (`python3 pasarela/pasarela.py`) y mirando su
registro. Deja todo como estaba al terminar.

1. **Autorización.** Añade `"cliente-b"` a `autorizados` en `pasarela.json`, rearranca y conecta
   con `python3 cliente/medir.py --cert pki/cliente-b/cliente-b.crt --clave pki/cliente-b/cliente-b.key`.
   Ahora entra. ¿Qué sección del código ha cambiado de camino? (Sección 3.7, paso 2.)
2. **Handshake mudo (P8).** Esto abre una conexión TCP con la pasarela y no hace el handshake:
   ```bash
   python3 -c "import socket,time; s=socket.create_connection(('10.172.18.70',3000)); t=time.time(); print(s.recv(1), round(time.time()-t,1), 's')"
   ```
   A los 10 s la pasarela te echa (`b'' 10.0 s`). ¿Qué línea sale en el registro y qué línea del
   código la escribe?
3. **SAN y superficie.** Cambia `"pasarela"` en `cliente/cliente.json` a `127.0.0.1` (sigue
   siendo este PC) y conecta con `medir.py`: `[TLS] Conexión rechazada: nadie escucha en
   127.0.0.1:3000`. Falla antes de TLS, porque la pasarela solo escucha en la IP del cable (P1,
   sección 3.5). Si la pasarela escuchara en todas las IP, fallaría un paso después, en el
   handshake: `IP address mismatch`, porque `127.0.0.1` no está en la SAN (sección 4.5,
   `server_hostname`). Lo segundo se ve con la prueba de humo: `s_client ... -verify_ip 127.0.0.1`.
4. **Qué escucha y dónde (P1).** Con la pasarela arrancada, `ss -ltnp | grep python`. ¿Por qué
   aparece `10.172.18.70:3000` y no `0.0.0.0:3000`? ¿Qué cambiaría en el código si quisieras
   escuchar en todas? (Sección 3.5.)
5. **Ver el cifrado.** Captura con Wireshark en `any` mientras haces un `movej` con
   `ensenar_reproducir.py`. Filtra `tcp.port == 30001` y busca la orden (botón derecho → Follow →
   TCP Stream). Haz lo mismo con `tcp.port == 3000`. ¿Qué ves en cada caso?
6. **Robot no disponible (P13).** Cambia el `destino` de la ruta 3000 a un puerto del robot que
   esté cerrado (por ejemplo `30009`), rearranca y conecta con `primaria.py`. ¿Qué dice el
   cliente? ¿Y el registro? ¿Qué nivel tiene la línea y por qué?
7. **Directo vs pasarela (C7, C10).** `python3 cliente/medir.py --directo` y
   `python3 cliente/medir.py`, tres veces cada uno. ¿Dónde está la diferencia grande: al conectar
   o en cada petición? ¿Por qué?
8. **El orden de los `except`.** En `transporte.py:134-141`, intercambia los dos `except` (el de
   `SSLError` antes que el de `SSLCertVerificationError`) y conecta con `--ca pki/otra-ca/ca.crt`.
   ¿Qué mensaje sale ahora? ¿Por qué? (Sección 2, excepciones anidadas.) Déjalo como estaba.

---

## 10. Preguntas del profesor

**¿Por qué no `create_default_context()`?**
Porque carga las CA del sistema. La pasarela aceptaría cualquier certificado firmado por una CA
pública. Solo debe valer nuestra CA (P5).

**¿Qué diferencia hay entre autenticación y autorización aquí?**
Autenticación: TLS comprueba que el certificado del cliente es auténtico (firmado por la CA,
vigente, con `clientAuth`) y que el cliente tiene su clave privada. Autorización: la pasarela
decide, con la lista de CN, si ese cliente autenticado puede usar el robot. cliente-b está
autenticado pero no autorizado.

**¿Por qué el cliente-b ve que el handshake funciona y luego se le cierra?**
Porque su certificado es válido: el handshake termina bien. La autorización es posterior, fuera
de TLS (`pasarela.py:129`), y la pasarela cierra con `close_notify`.

**¿Por qué el cliente sin certificado no ve el error al conectar?**
En TLS 1.3 el cliente termina su parte del handshake antes de que el servidor valide su
certificado. El rechazo llega como alerta en la primera lectura (C9, `transporte.py:98-100`).

**¿Por qué el cliente tiene que conectarse a la IP y no a `localhost`?**
Porque comprueba que la dirección a la que se conecta está en la SAN del certificado de la
pasarela, y la SAN solo tiene `IP:10.172.18.70`.

**¿Qué protege TLS y qué no?**
Protege el tramo cliente-pasarela: nadie puede leer, modificar ni suplantar. No protege el tramo
pasarela-robot: va en claro, y cualquiera en la misma red puede hablar con el robot directamente.
Por eso, en una planta, la pasarela se pone lo más cerca posible del robot (idealmente en una red
aparte solo con él). Wireshark sigue viendo tamaños y tiempos de los registros cifrados.

**¿Por qué un hilo por cliente con `select` en lugar de dos?**
Sección 3.8: un `SSLSocket` no admite lectura y escritura simultáneas desde dos hilos (lo
comprobamos con una prueba de estrés que fallaba con `[Errno 11]`). Con `select`, cada socket lo
usa un solo hilo y el cierre es más simple.

**¿Cómo se cumple que un cliente lento no retrase a los demás (P10)?**
Cada cliente tiene su hilo, y el handshake se hace en ese hilo, no en el que acepta. Un cliente
que no habla solo bloquea su hilo, y como mucho 10 s (P8). Uno que no lee se corta a los 5 s de
`sendall` bloqueado.

**¿Dónde está la contraseña de la clave?**
En ningún fichero. Se pide al arrancar con `getpass` o se lee de una variable de entorno de esa
terminal (README, «Contraseñas»).

**¿Qué pasa si roban `pasarela.key`?**
Está cifrada con contraseña: sin ella no sirve. Si roban las dos cosas, pueden hacerse pasar por
la pasarela hasta que el certificado caduque o se revoque (CRL/OCSP, que esta práctica no
implementa). Por eso `chmod 600` y por eso la clave de la CA no está en la pasarela.

**¿Qué hay que cambiar para pasar del eco al robot?**
Solo `destino` y `puerto_destino` en `pasarela.json`. El código de la pasarela no sabe qué hay al
otro lado (Fase 5: «si tienes que tocar el código, tu diseño no separaba bien el reenvío del destino»).
