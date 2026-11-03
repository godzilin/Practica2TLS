#!/usr/bin/env bash
# Vuelve a emitir el certificado de la CA (con la misma clave) y a firmar los 4 certificados.
# Las .key y las .csr no cambian. Pide la contraseña de la CA una sola vez.
cd "$(dirname "$0")" || exit 1     # la carpeta pki, esté donde esté
read -rsp "Contraseña de la CA: " P; echo
export P
openssl pkey -in ca-privado/ca.key -passin env:P -noout || { echo "Contraseña incorrecta: no se ha tocado nada"; exit 1; }

openssl req -x509 -new -key ca-privado/ca.key -passin env:P -sha256 -days 730 -out publico/ca.crt \
  -subj "/CN=CA Robot Lab - estudiante 45618115" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" || exit 1

firmar() {  # $1 = equipo, $2 = fichero de extensiones
  openssl x509 -req -in "$1/$1.csr" -CA publico/ca.crt -CAkey ca-privado/ca.key -passin env:P \
    -CAserial ca-privado/ca.srl -days 365 -sha256 -extfile "$2" -out "$1/$1.crt" || exit 1
}
firmar pasarela pasarela.ext
firmar cliente-a cliente.ext
firmar cliente-b cliente.ext
firmar cliente-mal pasarela.ext   # cliente con serverAuth, para la prueba P6
unset P

openssl x509 -in publico/ca.crt -noout -subject
openssl verify -CAfile publico/ca.crt pasarela/pasarela.crt cliente-a/cliente-a.crt cliente-b/cliente-b.crt cliente-mal/cliente-mal.crt
