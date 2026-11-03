# E1 · Registro de la PKI

> Borrador generado durante el desarrollo (2026-11-03) con la PKI de pruebas, SAN = 10.172.18.70.
> Si en el laboratorio se vuelve a firmar el certificado de la pasarela con otra IP, regenera la
> tabla con `pki/registro.sh`. Las explicaciones de las opciones hay que redactarlas **con tus
> palabras** (lo pide el enunciado): las de aquí son una guía.

## Árbol de la PKI

```
CA Robot Lab - estudiante 45618115     publico/ca.crt (autofirmado, CA:TRUE)   clave: ca-privado/ca.key
├── pasarela-ur3e                      serverAuth, SAN IP:10.172.18.70
├── cliente-a                          clientAuth, autorizado en la pasarela
├── cliente-b                          clientAuth, NO autorizado (prueba P7)
└── cliente-mal                        serverAuth: certificado de cliente mal emitido (prueba P6)

CA de otro                             otra-ca/ca.crt: CA ajena, para la prueba "CA de otro estudiante"
```

## Tabla de certificados emitidos

| Titular | Emisor | N.º de serie | Válido desde | Válido hasta | Usos | SAN | Huella SHA-256 | .crt en | .key en |
|---|---|---|---|---|---|---|---|---|---|
| CA Robot Lab - estudiante 45618115 | CA Robot Lab - estudiante 45618115 | `21AFF909A4F0CB8A9568A4C64F630997374C436D` | Nov  3 10:18:30 2026 GMT | Nov  2 10:18:30 2028 GMT | CA:TRUE; Certificate Sign, CRL Sign | — | `61:5F:39:DC:04:43:4F:26:BE:1B:17:EB:3C:1C:C9:11:13:B8:C9:FC:73:AE:C2:DC:C9:91:99:57:1B:CA:FA:0B` | pasarela, clientes | ninguno: solo ca-privado/ (este PC) |
| pasarela-ur3e | CA Robot Lab - estudiante 45618115 | `78653FD149174593D721900CC863DD21D9691072` | Nov  3 10:18:30 2026 GMT | Nov  3 10:18:30 2027 GMT | CA:FALSE; Digital Signature; TLS Web Server Authentication | IP Address:10.172.18.70 | `AB:62:C2:18:41:2C:C8:C3:39:FC:93:A9:EA:13:7F:38:BB:E4:E9:A0:A3:DF:FE:67:98:39:B0:C1:22:6A:92:98` | pasarela | pasarela |
| cliente-a | CA Robot Lab - estudiante 45618115 | `78653FD149174593D721900CC863DD21D9691073` | Nov  3 10:18:30 2026 GMT | Nov  3 10:18:30 2027 GMT | CA:FALSE; Digital Signature; TLS Web Client Authentication | — | `B3:57:5D:B0:DD:47:8E:F3:D5:6D:9D:F7:39:E2:94:E6:D4:6C:44:A7:DD:AA:56:46:AA:73:97:83:6C:AE:CD:50` | cliente A | cliente A |
| cliente-b | CA Robot Lab - estudiante 45618115 | `78653FD149174593D721900CC863DD21D9691074` | Nov  3 10:18:30 2026 GMT | Nov  3 10:18:30 2027 GMT | CA:FALSE; Digital Signature; TLS Web Client Authentication | — | `82:EF:B5:7E:93:3A:2F:09:63:DC:8D:97:7F:54:4B:74:64:12:E0:DC:AC:B5:B8:73:16:10:EC:F0:9F:24:6B:84` | cliente B | cliente B |
| cliente-mal | CA Robot Lab - estudiante 45618115 | `78653FD149174593D721900CC863DD21D9691075` | Nov  3 10:18:30 2026 GMT | Nov  3 10:18:30 2027 GMT | CA:FALSE; Digital Signature; TLS Web Server Authentication | IP Address:10.172.18.70 | `1C:A6:1B:7E:C4:25:D8:8A:C8:81:66:AB:72:B6:A1:29:FD:43:81:5C:03:13:B8:BA:8F:0A:8B:5D:9B:04:D2:AE` | cliente de pruebas | cliente de pruebas |

## openssl verify

```
$ openssl verify -CAfile publico/ca.crt pasarela/pasarela.crt cliente-a/cliente-a.crt cliente-b/cliente-b.crt cliente-mal/cliente-mal.crt
pasarela/pasarela.crt: OK
cliente-a/cliente-a.crt: OK
cliente-b/cliente-b.crt: OK
cliente-mal/cliente-mal.crt: OK

$ openssl verify -CAfile otra-ca/ca.crt pasarela/pasarela.crt        # con la CA de otro
CN=pasarela-ur3e
error 20 at 0 depth lookup: unable to get local issuer certificate
```

Cada clave corresponde a su certificado (misma huella de la clave pública):

```
pasarela.crt  a840c5efed33f691c6479f2d83608235946f21fa12b5060ac7246600b05bb767
pasarela.key  a840c5efed33f691c6479f2d83608235946f21fa12b5060ac7246600b05bb767
cliente-a.crt 6d653b9d288ec40cbba10fcd66d524041cb6bcbad6a9545a14b62c24822fc43e
cliente-a.key 6d653b9d288ec40cbba10fcd66d524041cb6bcbad6a9545a14b62c24822fc43e
```

## Permisos de las claves

```
drwx------ ca-privado
-rw------- ca-privado/ca.key
-rw------- pasarela/pasarela.key
-rw------- cliente-a/cliente-a.key
-rw------- cliente-b/cliente-b.key
-rw------- cliente-mal/cliente-mal.key
```

`700` en `ca-privado` y `600` en cada `.key`: solo el propietario puede leerlas. Además todas
las claves están cifradas con contraseña (primera línea `-----BEGIN ENCRYPTED PRIVATE KEY-----`;
`openssl pkey -in pasarela/pasarela.key -noout` pide la contraseña).

## Prueba de humo (s_server / s_client)

Caso correcto: `Verify return code: 0 (ok)`, `TLSv1.3`, `TLS_AES_256_GCM_SHA384`, el servidor
ve `subject=CN=cliente-a` y lo que se escribe en un terminal aparece en el otro.

| Variante | Error en el cliente (s_client) | Error en el servidor (s_server) |
|---|---|---|
| Sin `-cert` ni `-key` | `tlsv13 alert certificate required` (alerta 116) | `peer did not return a certificate` |
| `-CAfile` de otra CA | `verify error:num=19:self-signed certificate in certificate chain` | `tlsv1 alert unknown ca` (alerta 48) |
| `-verify_ip 1.2.3.4` | `verify error:num=64:IP address mismatch` | `ssl/tls alert bad certificate` (alerta 42) |
| `-tls1_2` | `tlsv1 alert protocol version` (alerta 70) | `unsupported protocol` |

## Qué hace cada opción de OpenSSL

| Orden / opción | Qué hace |
|---|---|
| `openssl req` | Trabaja con peticiones de certificado (CSR). |
| `-x509` | En lugar de una petición, genera directamente un certificado autofirmado (solo para la CA). |
| `-new` | Crea una petición nueva. |
| `-newkey rsa:2048` | Genera a la vez un par de claves RSA de 2048 bits. |
| `-key fichero` | Usa una clave que ya existe (se usó para volver a emitir la CA con la misma clave al corregir su nombre). |
| `-sha256` | La firma se hace sobre un resumen SHA-256 (SHA-1 ya no es seguro). |
| `-days N` | Validez: 730 días la CA, 365 los equipos. |
| `-keyout` / `-out` | Dónde se guardan la clave privada y el certificado o la CSR. |
| `-subj "/CN=..."` | Nombre del titular. En la CA, como es autofirmada, también es el emisor. |
| `-addext` | Añade una extensión desde la línea de órdenes: `basicConstraints=critical,CA:TRUE` (es una CA) y `keyUsage=critical,keyCertSign,cRLSign` (solo firma certificados y CRL). |
| `-noenc` | Deja la clave sin cifrar. **No** se usa en las claves reales; solo en la CA falsa de pruebas, que se tira. |
| `openssl x509 -req -in` | Toma una CSR y la convierte en certificado firmado por la CA. |
| `-CA` / `-CAkey` | Certificado y clave de la CA que firma (pide la contraseña de la CA). |
| `-CAserial` / `-CAcreateserial` | Fichero con el último número de serie de la CA; la primera vez se crea con un valor aleatorio. |
| `-extfile f.ext` | Extensiones que impone la CA: `CA:FALSE`, `digitalSignature`, `serverAuth` o `clientAuth`, la SAN (solo la pasarela) y los identificadores de clave. |
| `openssl x509 -noout -text` | Muestra el certificado en texto (Issuer, Subject, Validity, extensiones). |
| `openssl req -verify` | Comprueba la firma de la CSR: demuestra que quien la pide tiene la clave privada. |
| `openssl pkey -noout` | Abre la clave; si pide contraseña, está cifrada. |
| `openssl verify -CAfile` | Comprueba la firma de la CA y que la cadena llega al ancla de confianza. |
| `-pubkey` / `-pubout` + `openssl sha256` | Huella de la clave pública sacada del certificado y de la clave: si coinciden, son pareja. |
| `s_server -accept 3000` | Servidor TLS de pruebas en el puerto 3000. |
| `-tls1_3` | Solo TLS 1.3. |
| `-cert` / `-key` | Certificado y clave propios. |
| `-CAfile` | Ancla de confianza para validar al otro extremo. |
| `-Verify 1` | (Servidor) exige certificado al cliente; `-verify` en minúscula solo lo pediría. |
| `-verify_ip` | (Cliente) comprueba que la IP está en la SAN del servidor. |
| `-verify_return_error` | Corta la conexión si la validación falla, en lugar de solo avisar. |
| `-fingerprint -sha256`, `-serial`, `-dates` | Datos para el registro de certificados (`pki/registro.sh`). |
