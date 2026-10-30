#!/usr/bin/env bash
# Creates the self-signed code-signing identity "Fleck Local Signing" in the login keychain, once.
set -euo pipefail
NAME="Fleck Local Signing"
KC="$HOME/Library/Keychains/login.keychain-db"
if security find-certificate -c "$NAME" "$KC" >/dev/null 2>&1; then echo "$NAME already exists"; exit 0; fi
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cat > "$T/o.cnf" <<CNF
[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=$NAME
[ext]
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
basicConstraints=critical,CA:false
CNF
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout "$T/k.pem" -out "$T/c.pem" -config "$T/o.cnf" 2>/dev/null
openssl pkcs12 -export -legacy -inkey "$T/k.pem" -in "$T/c.pem" -out "$T/i.p12" -passout pass:fleck 2>/dev/null \
  || openssl pkcs12 -export -inkey "$T/k.pem" -in "$T/c.pem" -out "$T/i.p12" -passout pass:fleck
security import "$T/i.p12" -k "$KC" -P fleck -T /usr/bin/codesign
# Trust for code signing in the user domain (may ask for your login password once).
security add-trusted-cert -r trustRoot -p codeSign -k "$KC" "$T/c.pem"
security find-identity -v -p codesigning | grep "$NAME"
