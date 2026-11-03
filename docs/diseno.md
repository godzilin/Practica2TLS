# E2 · Diseño de la pasarela

## Conexiones

```
            tramo C1: cifrado (TLS 1.3, mTLS)              tramo C2: en claro (TCP)
 ┌──────────────┐                        ┌──────────────┐                     ┌──────────────┐
 │  Cliente A   │══ TLS ═══════════════> │   Pasarela   │── TCP ────────────> │  Robot UR3e  │
 │ cliente-a.crt│   10.172.18.70:3000    │ pasarela.crt │   .73:30001         │  (URSim .73) │
 │ primaria.py  │══ TLS ═══════════════> │  SAN = IP de │── TCP ────────────> │              │
 │ ensenar_...  │   10.172.18.70:3001    │  la pasarela │   .73:29999         │              │
 └──────────────┘                        └──────────────┘   (dashboard)       └──────────────┘
   presenta cliente-a.crt                 exige certificado, valida contra
   valida pasarela.crt contra ca.crt      ca.crt, comprueba CN en la lista
   y la IP contra la SAN                  de autorizados; NO interpreta los bytes
```

Cada línea de `rutas` en `pasarela.json` es un puerto de escucha de la pasarela unido a un
puerto del robot. La obligatoria es `3000 -> 30001` (interfaz primaria: estado y órdenes
URScript). `3001 -> 29999` (dashboard) la usa `ensenar_reproducir.py` para desbloquear una
parada de protección y `medir.py` para medir tiempos.

## Hilos

```
 hilo principal ─ lee config, pide contraseña, crea el contexto TLS, abre un socket por ruta
   │               y se queda esperando Ctrl+C
   │
   ├── hilo «aceptar-3000» ─ accept() cada 1 s mira «parar» ─┐
   ├── hilo «aceptar-3001» ─ accept() ...                     │ por cada conexión nueva
   │                                                          ▼
   │      hilo «cliente-IP» (uno por cliente, P10) ───────────────────────────────────────
   │        1. handshake TLS (máx. tiempo_handshake = 10 s, P8) ── falla ─> registra y cierra TCP
   │        2. CN en «autorizados»? (P7) ───────────────────────── no ───> registra, close_notify
   │        3. conectar con el robot (P11) ─────────────────────── falla ─> registra, close_notify (P13)
   │        4. reenviar(): select() sobre [tls, robot], cada 1 s mira «parar»
   │             lo que llega del cliente -> sendall al robot
   │             lo que llega del robot   -> sendall al cliente       (P12: bytes tal cual)
   │           hasta: 0 bytes o excepción en cualquiera de los dos (P14, P15) o «parar» (P16)
   │        5. cierra robot, close_notify + cierre al cliente, registra el motivo (P9)
   │
   └── Ctrl+C ─> «parar» = Event: los aceptar dejan de aceptar y cierran su socket,
                 cada cliente sale de reenviar() en ≤ 1 s y cierra con close_notify;
                 el principal espera (join) a todos los hilos y termina (P16)
```

Hilos en total: `1 + nº de rutas + nº de clientes conectados`. Un cliente lento o mudo solo
bloquea su propio hilo (P10). Los datos compartidos entre hilos son la configuración y el
contexto TLS (solo se leen) y el `Event` «parar» (es seguro entre hilos), así que no hace falta
ningún `Lock`.

### Por qué un hilo por cliente con `select()` y no dos (uno por sentido)

El enunciado propone dos hilos por cliente, uno en cada sentido. Lo programamos así primero y
la prueba de estrés (4 clientes enviando 1 MB mientras reciben el eco) falló 1 de cada 20
rondas con `[Errno 11] Resource temporarily unavailable` al leer del cliente: un `SSLSocket`
de Python **no admite que un hilo lea mientras otro escribe** en la misma conexión (los dos
usan el mismo objeto SSL de OpenSSL, que no es seguro entre hilos). Con TCP normal sí se puede;
con TLS no.

Con `select()` un solo hilo espera a los dos sockets a la vez y lee del que tenga datos, así
que cada socket TLS lo usa un único hilo. La misma prueba pasó 20 de 20 rondas. Además el cierre
es más simple: no hay que despertar a otro hilo bloqueado en un `recv()`.

Detalle: `select()` mira el socket, pero TLS puede tener ya descifrados datos que no están en el
socket; por eso, si `tls.pending()` no es 0, se lee sin esperar a `select()`.

## Qué se cierra en cada caso

| Caso | Lo detecta | Qué se cierra | Registro (`pasarela.log`) |
|---|---|---|---|
| Cliente sin certificado, de otra CA, con serverAuth o TLS 1.2 | `wrap_socket` lanza `SSLError` | OpenSSL envía la alerta; se cierra el TCP. No se abre el robot | `IP rechazado en el handshake: <motivo de OpenSSL>` |
| Cliente que no habla | `wrap_socket` agota `tiempo_handshake` | El TCP del cliente | `IP rechazado en el handshake: The handshake operation timed out` |
| Cliente no autorizado (cliente-b) | CN no está en `autorizados` | close_notify + cierre. No se abre el robot | `IP CN=cliente-b TLSv1.3 <suite> rechazado: CN no autorizado` |
| Robot no disponible al conectar | `create_connection` falla | close_notify + cierre al cliente | `... rechazado: robot <ip:puerto> no disponible (<error>)` |
| El cliente se va | `recv` del cliente da 0 bytes o error | El TCP con el robot y el TLS del cliente | `... cerrado: el cliente ha cerrado la conexión` |
| El robot se cae o cierra | `recv` del robot da 0 bytes o error | El TCP con el robot y close_notify + cierre al cliente | `... cerrado: el robot ha cerrado la conexión` |
| Un extremo no acepta datos en 5 s | `sendall` agota `TIEMPO_ENVIO` | Los dos | `... cerrado: error al enviar ...` |
| Ctrl+C | `Event` «parar» | Sockets de escucha; cada cliente con close_notify; robot | `Parando...`, una línea `cerrado: parada de la pasarela` por cliente y `Pasarela parada` |

El cierre ordenado del lado TLS es siempre `cerrar_tls()`: `unwrap()` (envía close_notify) y
`close()`; si el cliente ya se había ido sin close_notify, `unwrap()` lanza una excepción, se
ignora y se cierra igualmente.

Limitación conocida: si el robot se queda mudo sin cerrar (cable cortado), la pasarela no lo
nota por sí misma; lo nota el cliente, porque `primaria.py` da por perdido el robot tras 5 s
sin datos, cierra y reconecta. Al reconectar, la pasarela ya no puede abrir el robot y lo
registra (caso «robot no disponible»).
