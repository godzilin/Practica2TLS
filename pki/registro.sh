#!/usr/bin/env bash
# Registro de certificados (Fase 1, paso 9 / entregable E1): saca de cada .crt los datos de la
# tabla en formato Markdown. No necesita contraseñas: solo lee certificados, que son públicos.
cd "$(dirname "$0")" || exit 1

echo "| Titular | Emisor | N.º de serie | Válido desde | Válido hasta | Usos | SAN | Huella SHA-256 | .crt en | .key en |"
echo "|---|---|---|---|---|---|---|---|---|---|"
fila() {  # $1 = fichero .crt, $2 = dónde está el .crt, $3 = dónde está el .key
  local c=$1
  local titular emisor serie desde hasta usos san huella
  titular=$(openssl x509 -in "$c" -noout -subject -nameopt multiline | sed -n 's/ *commonName *= //p')
  emisor=$(openssl x509 -in "$c" -noout -issuer -nameopt multiline | sed -n 's/ *commonName *= //p')
  serie=$(openssl x509 -in "$c" -noout -serial | cut -d= -f2)
  desde=$(openssl x509 -in "$c" -noout -startdate | cut -d= -f2)
  hasta=$(openssl x509 -in "$c" -noout -enddate | cut -d= -f2)
  usos=$(openssl x509 -in "$c" -noout -ext keyUsage,extendedKeyUsage,basicConstraints 2>/dev/null \
         | grep -v '^ *X509v3' | sed 's/^ *//' | paste -sd ';' | sed 's/;/; /g')
  san=$(openssl x509 -in "$c" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | sed 's/^ *//')
  huella=$(openssl x509 -in "$c" -noout -fingerprint -sha256 | cut -d= -f2)
  echo "| $titular | $emisor | \`$serie\` | $desde | $hasta | $usos | ${san:-—} | \`$huella\` | $2 | $3 |"
}
fila publico/ca.crt          "pasarela, clientes" "ninguno: solo ca-privado/ (este PC)"
fila pasarela/pasarela.crt   "pasarela"           "pasarela"
fila cliente-a/cliente-a.crt "cliente A"          "cliente A"
fila cliente-b/cliente-b.crt "cliente B"          "cliente B"
fila cliente-mal/cliente-mal.crt "cliente de pruebas" "cliente de pruebas"
