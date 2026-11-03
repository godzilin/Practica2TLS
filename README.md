# Pasarela TLS para el robot UR3e

Práctica de Redes de Comunicación Industriales: **proteger** la comunicación con el robot UR3e
sin cambiarla. El robot solo habla TCP en claro; una **pasarela** se pone delante y los clientes
le hablan por **TLS 1.3 con autenticación mutua**. La pasarela comprueba quién es cada cliente y
copia los bytes al robot sin mirarlos.

```
 cliente (primaria.py, ensenar_reproducir.py)          pasarela.py                    robot / URSim
   transporte.conectar()  ══ TLS 1.3, mTLS ══>  10.172.18.70:3000  ── TCP en claro ──>  .73:30001
                          ══ TLS 1.3, mTLS ══>  10.172.18.70:3001  ── TCP en claro ──>  .73:29999
        tramo C1: cifrado, cliente y pasarela autenticados    tramo C2: en claro (como antes)
```

- **Pasarela** (`pasarela/pasarela.py`): servidor TLS para los clientes y cliente TCP para el
  robot. Solo biblioteca estándar de Python (≥ 3.10).
- **Cliente seguro** (`cliente/`): los scripts de la práctica de TCP/IP con **una línea
  cambiada**: `socket.create_connection(...)` pasa a ser `conectar(...)`, de `transporte.py`.
  El código que construye e interpreta los mensajes del robot no cambia.
- **PKI** (`pki/`): CA propia y certificados creados con OpenSSL.

Documentos de la entrega: [`docs/diseno.md`](docs/diseno.md) (E2: conexiones, hilos y cierres) y
[`docs/E1_pki.md`](docs/E1_pki.md) (E1: registro de la PKI, borrador). Para entender el código
línea a línea: [`docs/guia_codigo.md`](docs/guia_codigo.md). Las decisiones tomadas están al
final de este README.

---

## Estructura

```
Practica2TLS/
├── pasarela/
│   ├── pasarela.py          la pasarela
│   ├── pasarela.json        su configuración (IP, rutas, autorizados, certificados, tiempos)
│   └── eco.py               servidor de eco que hace de «robot» para probar sin robot (Fase 3)
├── cliente/
│   ├── transporte.py        conectar(): TLS a la pasarela o TCP directo al robot
│   ├── cliente.json         su configuración (modo, IP de la pasarela, puertos, certificados)
│   ├── robots.json          IP del robot (la misma que en la práctica de TCP/IP)
│   ├── primaria.py          visualizador de la práctica de TCP/IP (1 línea cambiada)
│   ├── ensenar_reproducir.py  enseñar y reproducir (1 línea cambiada, 2 sitios)
│   └── medir.py             tiempos de respuesta directo vs pasarela (C10)
├── pruebas/
│   └── prueba_pasarela.py   prueba automática de P1 a P16 contra el eco
├── pki/
│   ├── publico/ca.crt       certificado de la CA (se reparte)
│   ├── ca-privado/          clave de la CA (NO se sube a git ni se entrega)
│   ├── pasarela/ cliente-a/ cliente-b/ cliente-mal/   .crt (en git) y .key (fuera de git)
│   ├── otra-ca/ca.crt       una CA ajena, para la prueba «CA de otro estudiante»
│   ├── pasarela.ext cliente.ext   extensiones que impone la CA
│   ├── refirmar.sh          vuelve a emitir la CA y a firmar los 4 certificados
│   └── registro.sh          tabla de certificados para el E1
├── docs/
│   ├── diseno.md            E2
│   ├── E1_pki.md            E1 (borrador)
│   ├── guia_codigo.md       guía para leer el código, con recorridos y ejercicios
│   └── captura_ejemplo.pcapng   captura de ejemplo (no es la de la demostración)
└── material/                wheel del UR3e para PyBullet (la misma de la práctica de TCP/IP)
```

**Secretos**: `.gitignore` deja fuera las `.key`, `pki/ca-privado/`, `*.p12`, los `.csr`, los
registros y cualquier fichero `claves*`. Compruébalo antes de subir nada con
`git ls-files | grep -E 'key|ca-privado|claves'` (no debe salir nada).

---

## Puesta en marcha

### 1. Instalar

La pasarela no necesita instalar nada. Los visualizadores necesitan PyBullet y la wheel del UR3e:

```bash
python3 -m venv .venv
.venv/bin/pip install material/ur3e_rg2-0.1.0-py3-none-any.whl
```

### 2. Arrancar la pasarela

```bash
python3 pasarela/pasarela.py
# Contraseña de la clave de la pasarela:      (no se ve al escribir)
# ... INFO Escuchando en 10.172.18.70:3000 -> 10.172.18.73:30001
# ... INFO Escuchando en 10.172.18.70:3001 -> 10.172.18.73:29999
# ... INFO Pasarela en marcha. Autorizados: cliente-a. Ctrl+C para parar
```

Cada conexión queda en `pasarela/pasarela.log` (y en pantalla). Ctrl+C la para de forma ordenada.

### 3. Arrancar un cliente

Igual que en la práctica de TCP/IP (F5 en VS Code o desde la terminal):

```bash
.venv/bin/python cliente/primaria.py
# Contraseña de la clave del cliente:
# Robot: URSim (10.172.18.73:30001)
# [TLS] TLSv1.3, TLS_AES_256_GCM_SHA384. Pasarela: CN=pasarela-ur3e, caduca Nov  3 10:18:30 2027 GMT
# Conectado a 10.172.18.73:30001
# q_actual (grados): [...]
```

La línea `[TLS]` es la de C5 (versión, suite, sujeto y caducidad del certificado de la pasarela).
El script sigue escribiendo «Conectado a 10.172.18.73:30001» porque esa línea es suya y no se ha
tocado: es la dirección del robot, aunque la conexión real vaya a la pasarela.

Para conectar **directo al robot**, sin pasarela (C7): `--directo` en la línea de órdenes, o
`"modo": "directo"` en `cliente/cliente.json`.

---

## Configuración

Todas las IP, puertos y rutas de ficheros están en los JSON; en el código no hay ninguna.
Las rutas de ficheros relativas son relativas a la carpeta del JSON, así que la carpeta entera
se puede copiar a otro PC.

### `pasarela/pasarela.json`

| Campo | Para qué |
|---|---|
| `ip_escucha` | IP de la pasarela. Solo se escucha en esa IP (P1), y tiene que ser la de la SAN de su certificado. |
| `rutas` | Lista de `{"escucha", "destino", "puerto_destino"}`: cada puerto de la pasarela va a un puerto del robot. `3000 -> 30001` es la obligatoria; `3001 -> 29999` (dashboard) la usan `ensenar_reproducir.py` y `medir.py`. |
| `autorizados` | CN de los clientes que pueden pasar (P7). `cliente-b` no está. |
| `ca`, `certificado`, `clave` | Ficheros PEM de la PKI. |
| `registro` | Fichero de registro (P9). |
| `tiempo_handshake` | Segundos máximos para el handshake (P8). |
| `tiempo_robot` | Segundos máximos para conectar con el robot. |

Se puede arrancar con otro fichero: `python3 pasarela/pasarela.py otra.json` (así lo hace la
prueba automática).

### `cliente/cliente.json`

| Campo | Para qué |
|---|---|
| `modo` | `"pasarela"` (TLS) o `"directo"` (TCP al robot, C7). |
| `pasarela` | IP de la pasarela: la de la SAN de su certificado (no `127.0.0.1` ni un nombre). |
| `puertos` | Puerto del robot -> puerto de la pasarela: `{"30001": 3000, "29999": 3001}`. Así los scripts siguen usando sus constantes `PUERTO = 30001` y `PUERTO_DASHBOARD = 29999`. |
| `ca`, `certificado`, `clave` | Ficheros PEM del cliente (C1). |

`cliente/robots.json` es el de la práctica de TCP/IP: solo se usa la IP del robot en modo directo.

Opciones de línea de órdenes de cualquier cliente (para la demostración):

| Opción | Efecto |
|---|---|
| `--directo` | Sin pasarela. |
| `--cert F --clave F` | Otro certificado de cliente (cliente-b, cliente-mal). |
| `--sin-cert` | Sin certificado de cliente. |
| `--ca F` | Otra CA de confianza (la de otro estudiante). |

---

## Contraseñas de las claves

Todas las claves privadas están cifradas. Ningún programa tiene la contraseña escrita: ni en el
código ni en los JSON (P2, C1). Cada programa la consigue así:

1. **Si existe la variable de entorno** (`PASARELA_CLAVE` en la pasarela, `CLIENTE_CLAVE` en los
   clientes), la usa.
2. **Si no**, la pide al arrancar con `getpass`, sin mostrarla en pantalla.

Por qué la variable de entorno: para las pruebas hay que lanzar varios clientes a la vez (P10) y
repetirlas muchas veces, y escribir la contraseña en cada uno es lento. El enunciado la permite
(«pedirla al arrancar, sin mostrarla en pantalla, o leerla de una variable de entorno»).

Riesgos de la variable de entorno y cómo se reducen:

- Escrita en la orden (`CLIENTE_CLAVE=xxx python3 ...`) queda en el **historial** del shell.
  Mejor leerla sin que se vea y exportarla solo en esa terminal:
  ```bash
  read -rs CLIENTE_CLAVE; export CLIENTE_CLAVE      # bash/zsh: no se ve ni queda en el historial
  ```
- Cualquier proceso **del mismo usuario** puede leerla (`/proc/<pid>/environ`). Otro usuario no.
- Dura lo que dure la terminal: al cerrarla desaparece. `unset CLIENTE_CLAVE` la borra antes.

En la demostración se puede no usar: sin variable, la pasarela y el cliente la piden con `getpass`.

---

## Cambiar las IP (laboratorio)

Las IP de la pasarela y del robot aún no están asignadas. Cuando lo estén:

1. **JSON**: `ip_escucha` y `destino` en `pasarela/pasarela.json`, y `pasarela` en
   `cliente/cliente.json` (y la IP del robot en `cliente/robots.json` para el modo directo).
2. **Certificado de la pasarela**: la IP de la pasarela va firmada en su SAN, así que hay que
   volver a firmarlo. La clave y la CSR no llevan la IP y se reutilizan; la CA y los clientes no
   cambian. Pide la contraseña de la CA:
   ```bash
   cd pki
   IP=10.172.21.30        # la IP asignada
   sed -i "s/^subjectAltName = IP:.*/subjectAltName = IP:$IP/" pasarela.ext
   openssl req -new -key pasarela/pasarela.key -subj "/CN=pasarela-ur3e" -out pasarela/pasarela.csr
   openssl x509 -req -in pasarela/pasarela.csr -CA publico/ca.crt -CAkey ca-privado/ca.key \
     -CAserial ca-privado/ca.srl -days 365 -sha256 -extfile pasarela.ext -out pasarela/pasarela.crt
   ./registro.sh          # tabla nueva para el E1 (cambia el número de serie)
   ```
   La orden `req` regenera la CSR por si no está (los `.csr` no se suben a git).

---

## Pruebas

### Prueba automática (Fase 3, sin robot)

```bash
read -rs PASARELA_CLAVE; export PASARELA_CLAVE
read -rs CLIENTE_CLAVE; export CLIENTE_CLAVE
read -rs CLIENTEB_CLAVE; export CLIENTEB_CLAVE         # opcional: prueba P7
read -rs CLIENTEMAL_CLAVE; export CLIENTEMAL_CLAVE     # opcional: prueba P6
python3 pruebas/prueba_pasarela.py
```

Arranca `eco.py` y una pasarela con puertos 3100-3102 (no choca con la pasarela real) y comprueba:

```
OK    P1 solo escucha en su IP
OK    P12 reenvío sin modificar (200 kB) TLSv1.3 TLS_AES_256_GCM_SHA384
OK    P12 los dos sentidos a la vez (10 rondas x 4 clientes x 1 MB)
OK    P3 TLS 1.2 rechazado (TLSV1_ALERT_PROTOCOL_VERSION)
OK    P4 sin certificado rechazado
OK    P6 certificado serverAuth rechazado
OK    P7 cliente-b no autorizado
OK    P10 4 clientes a la vez con uno mudo (0.06 s)
OK    P8 el cliente mudo es expulsado (2.0 s, límite 2 s)
OK    P13 robot no disponible
OK    P14 se cae el robot y se cierra el cliente
OK    P16 Ctrl+C cierra a los clientes con close_notify
OK    P16 la pasarela termina
TODO OK
```

Necesita el cable puesto (la pasarela escucha en la IP de su SAN).

### Pruebas de aceptación con el robot (Fase 5 y demostración)

Con la pasarela arrancada (`python3 pasarela/pasarela.py`):

| Prueba | Orden | Resultado obtenido con URSim (10.172.18.73) |
|---|---|---|
| Cliente A, mismas operaciones | `.venv/bin/python cliente/primaria.py` y `--directo` | Los mismos `q_actual` por los dos caminos. Con la clase `Robot` de `ensenar_reproducir.py`, un `movej` de +0,2 rad en la base movió URSim +11,46º y volvió, y el dashboard respondió `Robotmode: RUNNING`. `ensenar_reproducir.py` arranca y conecta por la pasarela (la secuencia completa con teclas no se ha probado aún) |
| Cliente B | `python3 cliente/medir.py --cert pki/cliente-b/cliente-b.crt --clave pki/cliente-b/cliente-b.key` | Cliente: handshake correcto y luego `[TLS] La pasarela ha cerrado la conexión: cliente no autorizado o robot no disponible`. Registro: `CN=cliente-b ... rechazado: CN no autorizado` |
| Sin certificado | `python3 cliente/medir.py --sin-cert` | Cliente: `[TLS] La pasarela ha rechazado el certificado del cliente (TLSV13_ALERT_CERTIFICATE_REQUIRED)`. Registro: `peer did not return a certificate` |
| Certificado con serverAuth | `python3 cliente/medir.py --cert pki/cliente-mal/cliente-mal.crt --clave pki/cliente-mal/cliente-mal.key` | Cliente: `... (SSLV3_ALERT_UNSUPPORTED_CERTIFICATE)`. Registro: `certificate verify failed: unsuitable certificate purpose` |
| Se desconecta el robot | parar URSim con un cliente conectado | Registro: `cerrado: el robot ha cerrado la conexión`; cliente: `[TLS] La pasarela ha cerrado la conexión...` (probado con el eco; con el robot real, en el laboratorio) |
| 4 clientes y uno bloqueado | prueba automática (P10) | Los otros siguen funcionando |
| CA de otro estudiante | `python3 cliente/medir.py --ca pki/otra-ca/ca.crt` | `[TLS] Certificado de la pasarela NO válido: self-signed certificate in certificate chain` |
| Tiempos directo vs pasarela | `python3 cliente/medir.py --directo` y `python3 cliente/medir.py` | Ver C10 |

`medir.py` sirve de cliente de pruebas porque usa el mismo `transporte.py` que los visualizadores
y no abre ventana. Los mensajes de error son los mismos con `primaria.py`.

Los cinco mensajes del cliente (C8):

| Situación | Mensaje |
|---|---|
| Pasarela inalcanzable | `[TLS] Pasarela inalcanzable en IP:puerto: <error>` |
| Conexión rechazada | `[TLS] Conexión rechazada: nadie escucha en IP:puerto (¿está arrancada la pasarela?)` |
| Certificado de la pasarela no válido | `[TLS] Certificado de la pasarela NO válido: <motivo>` |
| Certificado del cliente rechazado | `[TLS] La pasarela ha rechazado el certificado del cliente (<alerta>)` |
| Cerrada tras el handshake | `[TLS] La pasarela ha cerrado la conexión: cliente no autorizado o robot no disponible` |

### C10 · Tiempos (URSim, mismo PC para cliente y pasarela)

`medir.py` envía 100 veces `robotmode` al dashboard y mide cada respuesta con `perf_counter`:

| | Abrir la conexión | Media por petición | Mediana |
|---|---|---|---|
| Directo | 1,2 – 1,8 ms | 0,48 – 0,68 ms | 0,32 – 0,49 ms |
| A través de la pasarela | 6,1 – 7,0 ms | 0,66 – 0,75 ms | 0,44 – 0,46 ms |

TLS cuesta sobre todo **al conectar** (+5 ms: handshake con dos certificados RSA y la conexión
extra pasarela-robot). Por petición, la diferencia es de décimas de milisegundo: está dentro del
ruido de la red, frente al mensaje de estado del robot, que llega cada 100 ms.

---

## Wireshark

Cliente y pasarela están en el mismo PC, así que el tramo cifrado no sale por el cable: Linux
lo entrega por la interfaz **`lo`**. El tramo en claro sí va por el cable. Lo más cómodo es
capturar en **`any`**, que incluye las dos.

| Qué | Filtro |
|---|---|
| Tramo cifrado (cliente-pasarela) | `tcp.port == 3000` (o `3001`) |
| Tramo en claro (pasarela-robot) | `tcp.port == 30001` (o `29999`) |
| Handshakes | `tls.handshake.type == 1 \|\| tls.handshake.type == 2` |

En `docs/captura_ejemplo.pcapng` hay una captura hecha así (dos `movej`, un cliente-b y uno sin
certificado). En el tramo 30001 se leen los `movej(...)` en claro; en el 3000 la palabra `movej`
no aparece ni una vez: solo hay `Application Data`. En TLS 1.3 los certificados tampoco se ven:
viajan cifrados después del Server Hello. Por eso el rechazo de cliente-b o del cliente sin
certificado se ve como registros cifrados seguidos del cierre TCP, no como una alerta legible.

---

## Requisitos y dónde se cumplen

| Req. | Dónde |
|---|---|
| P1 | `socket.create_server((ip_escucha, puerto))` en `arrancar()` |
| P2 | `load_cert_chain(..., password=...)`, contraseña de `getpass` o `PASARELA_CLAVE` |
| P3 | `ctx.minimum_version = TLSv1_3` |
| P4 | `ctx.verify_mode = CERT_REQUIRED` |
| P5 | `SSLContext` sin `create_default_context` + `load_verify_locations(ca)` |
| P6 | Lo hace OpenSSL al validar al cliente (probado con `cliente-mal`) |
| P7 | `atender()` paso 2: CN contra `autorizados` |
| P8 | `conexion.settimeout(tiempo_handshake)` antes de `wrap_socket` |
| P9 | `logging` a fichero: hora, IP, CN, versión, suite y motivo |
| P10 | Un hilo por cliente |
| P11 | `rutas` en `pasarela.json` |
| P12 | `reenviar()`: `recv` y `sendall` tal cual |
| P13 | `atender()` paso 3: registra y `cerrar_tls()` |
| P14, P15 | `reenviar()` termina con 0 bytes o excepción en cualquier lado y se cierran los dos |
| P16 | `Event` «parar», `join` de todos los hilos, `close_notify` a cada cliente |
| C1 | `cliente.json` + opciones; `getpass` o `CLIENTE_CLAVE` |
| C2 | `load_cert_chain` del cliente |
| C3 | `PROTOCOL_TLS_CLIENT` (cadena, serverAuth y SAN con `server_hostname=IP`) + solo `ca.crt` |
| C4 | `minimum_version = TLSv1_3` |
| C5 | Línea `[TLS]` al conectar |
| C6 | `primaria.py` y `ensenar_reproducir.py`: solo cambia `create_connection` por `conectar` |
| C7 | `--directo` o `"modo": "directo"` |
| C8, C9 | Mensajes de `conectar()` y de `SocketTLS.recv()` |
| C10 | `medir.py` |

---

## Decisiones tomadas

1. **Proyecto aparte de la práctica de TCP/IP.** `Practica1UR` no se toca. Se copiaron
   `primaria.py` y `ensenar_reproducir.py` y solo se cambió la línea que abre la conexión.
   Así la entrega es autocontenida y el cambio de C6 se ve con un `diff`.
2. **`conectar()` con la misma firma que `socket.create_connection()`.** Recibe la dirección del
   robot y el timeout, y devuelve un socket. Los scripts no saben si van por TLS o directo; el
   puerto del robot se traduce al de la pasarela con `puertos` en `cliente.json`, así que las
   constantes `PUERTO = 30001` de los scripts siguen valiendo.
3. **Rutas en la pasarela.** Un puerto de la pasarela por cada puerto del robot que se usa
   (3000 -> 30001, 3001 -> 29999). El enunciado solo pide el 3000; el 3001 hace falta porque
   `ensenar_reproducir.py` también habla con el dashboard. No se añadió el 30003
   (`tiempo_real.py`) porque no se usa en la demostración: sería una línea más en los dos JSON.
4. **Un hilo por cliente con `select()`, no dos.** Ver `docs/diseno.md`: con dos hilos por
   cliente (uno por sentido), la prueba de estrés falló a veces con `[Errno 11]` porque un
   `SSLSocket` de Python no admite que un hilo lea mientras otro escribe. Con `select()` cada
   socket lo usa un solo hilo y la prueba pasa siempre.
5. **El mismo problema en el cliente: `recv` y `sendall` con un `Lock`.**
   `ensenar_reproducir.py` lee el estado en un hilo y envía los `movej` desde otro por el mismo
   socket. Con TCP no pasaba nada; con TLS, `SocketTLS` hace que no se ejecuten a la vez. La
   espera máxima de un `sendall` es lo que tarda en llegar el siguiente estado (< 0,1 s).
6. **Contraseña por `getpass` o variable de entorno** (ver «Contraseñas»). Nunca en el código ni
   en los JSON.
7. **La PKI dentro del proyecto** (`pki/`), para llevar todo a otro PC en una carpeta. Los
   secretos quedan fuera de git con `.gitignore`. Al copiar la carpeta a un PC que solo haga de
   pasarela o de cliente, lo correcto es **no** copiar `pki/ca-privado/` (la clave de la CA no
   debe estar en ningún equipo de la red).
8. **Dos certificados de prueba extra**: `cliente-mal` (de cliente pero emitido con
   `serverAuth`, para P6) y `otra-ca` (una CA ajena, para la prueba «CA de otro estudiante» sin
   depender de un compañero).
9. **Sin VM.** Cliente y pasarela corren en el mismo PC Linux. El cliente se conecta a la IP de
   la pasarela (la de la SAN), nunca a `127.0.0.1`, y el tramo cifrado se captura en `lo`.
10. **Cada hilo mira cada segundo si tiene que parar** (`accept()` y `select()` con 1 s de
    espera). Es la forma más simple de que Ctrl+C cierre todo de forma ordenada sin cerrar
    sockets desde otro hilo.
11. **Si un extremo no acepta datos en 5 s, se cierra la conexión** (`TIEMPO_ENVIO`), para que un
    cliente que no lee no deje colgado su hilo para siempre.
12. **La pasarela no detecta por sí misma un robot mudo** (cable cortado sin cierre TCP). Lo
    detecta el cliente: `primaria.py` da el robot por perdido a los 5 s, cierra y reconecta, y
    entonces la pasarela registra «robot no disponible». Se evitó añadir otro tiempo límite que
    además rompería el dashboard, que puede estar callado mucho tiempo.
13. **La CA se volvió a emitir con la misma clave.** La primera versión salió con el CN
    `CA Robot Lab - estudiante ` (sin número: en zsh, `set N 123` no crea la variable). Se
    reemitió con `openssl req -x509 -new -key ca-privado/ca.key ...` y se volvieron a firmar los
    cuatro certificados (`pki/refirmar.sh`).

---

## Entrega

Un zip sin secretos sale directamente de git, porque las claves no están en el repositorio:

```bash
git archive --format=zip --prefix=Practica2TLS/ -o ../Practica2TLS_entrega.zip HEAD
```

Añade al zip la captura de la demostración. Comprueba antes que no lleva ninguna clave:
`unzip -l ../Practica2TLS_entrega.zip | grep -E 'key|ca-privado|claves'` no debe sacar nada.

Para llevar la carpeta **con** las claves a otro PC (para la demostración), cópiala entera por
USB o `scp`, no por git.
