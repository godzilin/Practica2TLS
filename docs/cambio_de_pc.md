# Cambiar de PC o de IP

Qué hay que tocar para que la pasarela, el cliente y las pruebas funcionen en otro equipo.
Las rutas a certificados y claves de `pasarela.json` y `cliente.json` son relativas a la carpeta
de cada JSON, así que no hay que cambiarlas al mover el repo.

## 1. Copiar lo que no está en git

`.gitignore` deja fuera las claves privadas y las CSR. Cópialas a mano (USB o `scp`, nunca por
git) a las mismas rutas:

| Fichero | Dónde hace falta |
|---|---|
| `pki/pasarela/pasarela.key` | PC de la pasarela |
| `pki/cliente-a/cliente-a.key` | PC del cliente |
| `pki/cliente-b/cliente-b.key`, `pki/cliente-mal/cliente-mal.key` | PC donde se ejecuten las pruebas |
| `pki/ca-privado/ca.key`, `pki/ca-privado/ca.srl`, `pki/*/*.csr` | Solo si vas a ejecutar `refirmar.sh` en ese PC |

> **Seguridad:** `ca.key` es el secreto más importante de la práctica. Quien la tenga (y su
> contraseña) puede emitir certificados que la pasarela aceptará. Cópiala solo a equipos tuyos y
> bórrala del USB al terminar.

Si las IP no cambian, con esto basta.

## 2. Si cambia la IP de la pasarela (ahora `10.172.18.70`)

La IP de la pasarela va dentro de su certificado (SAN). El cliente comprueba que coincide con la
IP a la que se conecta; si no, el handshake falla. Por eso hay que refirmar.

1. Edita `pki/pasarela.ext` y pon la IP nueva:
   ```
   subjectAltName = IP:<IP nueva>
   ```
2. Refirma los certificados (pide la contraseña de la CA):
   ```bash
   pki/refirmar.sh
   ```
   Al final debe salir `OK` para los 4 certificados.
3. Pon la IP nueva en `"ip_escucha"` de `pasarela/pasarela.json`.
4. Pon la IP nueva en `"pasarela"` de `cliente/cliente.json`.
5. Lleva `pki/publico/ca.crt` y todos los `.crt` nuevos a todos los equipos (commit y
   `git pull` en cada uno, o copia a mano). Las `.key` no cambian.
6. Regenera la tabla del registro de la PKI, porque cambian series, fechas y huellas:
   ```bash
   pki/registro.sh
   ```
   y pega la salida en `docs/E1_pki.md`.

## 3. Si cambia la IP del robot / URSim (ahora `10.172.18.73`)

No afecta a los certificados: no hay que refirmar.

1. Cambia `"destino"` en todas las rutas de `pasarela/pasarela.json`.
2. Cambia `"host"` en `cliente/robots.json`.

## 4. Entorno de Python

La pasarela no lo necesita: se arranca con el `python3` del sistema (3.8 o más nuevo).

El cliente sí (PyBullet). **No copies `.venv` de otro PC**: guarda rutas absolutas y la versión
exacta de Python, y en otro equipo no arranca. Créalo en el PC nuevo:

```bash
python3 -m venv .venv
.venv/bin/pip install material/ur3e_rg2-0.1.0-py3-none-any.whl
```

## Comprobación

Con el cable puesto (la prueba arranca su propio eco y su propia pasarela; las contraseñas son
las de las `.key`, ver la sección *Pruebas* del README):

```bash
read -rs PASARELA_CLAVE; export PASARELA_CLAVE
read -rs CLIENTE_CLAVE; export CLIENTE_CLAVE
python3 pruebas/prueba_pasarela.py
```
