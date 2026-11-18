from pathlib import Path

source = Path("cmd/telesrv/main.go")
text = source.read_text()

def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"RiseGram MTProto patch expected one {label} match, found {count}")
    text = text.replace(old, new, 1)

replace_once(
    'import (\n\t"context"\n',
    'import (\n\t"context"\n\t"crypto/x509"\n\t"encoding/pem"\n',
    "crypto imports",
)

replace_once(
    '\tfingerprint := exchange.PrivateKey{RSA: rsaKey}.Fingerprint()\n',
    '\tfingerprint := exchange.PrivateKey{RSA: rsaKey}.Fingerprint()\n'
    '\tpublicKeyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PUBLIC KEY", Bytes: x509.MarshalPKCS1PublicKey(&rsaKey.PublicKey)})\n'
    '\tlogger.Info("RiseGram MTProto public client key",\n'
    '\t\tzap.String("rsa_fingerprint", fmt.Sprintf("%016x", fingerprint)),\n'
    '\t\tzap.String("rsa_public_key_pem", string(publicKeyPEM)),\n'
    '\t)\n',
    "public RSA key startup log",
)

replace_once(
    '\tport, err := strconv.Atoi(portStr)\n'
    '\tif err != nil {\n'
    '\t\treturn fmt.Errorf("parse listen port %q: %w", portStr, err)\n'
    '\t}\n',
    '\tport, err := strconv.Atoi(portStr)\n'
    '\tif err != nil {\n'
    '\t\treturn fmt.Errorf("parse listen port %q: %w", portStr, err)\n'
    '\t}\n'
    '\tadvertisePort := port\n'
    '\tif raw := strings.TrimSpace(os.Getenv("TELESRV_ADVERTISE_PORT")); raw != "" {\n'
    '\t\tadvertisePort, err = strconv.Atoi(raw)\n'
    '\t\tif err != nil || advertisePort < 1 || advertisePort > 65535 {\n'
    '\t\t\treturn fmt.Errorf("TELESRV_ADVERTISE_PORT must be a TCP port in 1..65535: %q", raw)\n'
    '\t\t}\n'
    '\t}\n'
    '\tadvertisePortStr := strconv.Itoa(advertisePort)\n',
    "advertised external TCP port",
)

replace_once(
    'zap.String("advertise", net.JoinHostPort(cfg.AdvertiseIP, portStr)),',
    'zap.String("advertise", net.JoinHostPort(cfg.AdvertiseIP, advertisePortStr)),',
    "advertised address log",
)

replace_once(
    'Port:                     port,',
    'Port:                     advertisePort,',
    "MTProto RPC advertised port",
)

source.write_text(text)
print("RiseGram MTProto source patch applied; server will log only the public RSA key and advertise the configured external TCP port.")
