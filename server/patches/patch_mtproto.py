from pathlib import Path

source = Path("cmd/telesrv/main.go")
text = source.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"RiseGram MTProto patch expected one {label} match, found {count}")
    text = text.replace(old, new, 1)


def replace_many(old: str, new: str, expected: int, label: str) -> None:
    global text
    count = text.count(old)
    if count != expected:
        raise SystemExit(
            f"RiseGram MTProto patch expected {expected} {label} matches, found {count}"
        )
    text = text.replace(old, new)


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
    '\tadvertisePortStr := strconv.Itoa(advertisePort)\n'
    '\tadvertiseHost := strings.TrimSpace(os.Getenv("TELESRV_ADVERTISE_HOST"))\n'
    '\tif advertiseHost == "" { advertiseHost = cfg.AdvertiseIP }\n'
    '\tif strings.ContainsAny(advertiseHost, "/: ") { return fmt.Errorf("TELESRV_ADVERTISE_HOST must be a hostname or IP without scheme or port") }\n',
    "advertised external TCP host and port",
)

replace_many(
    'zap.String("advertise", net.JoinHostPort(cfg.AdvertiseIP, portStr)),',
    'zap.String("advertise", net.JoinHostPort(advertiseHost, advertisePortStr)),',
    2,
    "advertised address log",
)

replace_once(
    'IP:                       cfg.AdvertiseIP,',
    'IP:                       advertiseHost,',
    "MTProto advertised host",
)

replace_once(
    'Port:                     port,',
    'Port:                     advertisePort,',
    "MTProto RPC advertised port",
)


# Instrument the temporary-to-permanent binding and final phone sign-in commit.
# Auth-key IDs are safe operational identifiers; do not log phone numbers or codes.
auth_source = Path("internal/rpc/auth.go")
auth_text = auth_source.read_text()


def replace_auth_once(old: str, new: str, label: str) -> None:
    global auth_text
    count = auth_text.count(old)
    if count != 1:
        raise SystemExit(f"RiseGram auth patch expected one {label} match, found {count}")
    auth_text = auth_text.replace(old, new, 1)


replace_auth_once(
    '\tsessionID, _ := SessionIDFrom(ctx)\n\tboundState, err := r.deps.Auth.BindTempAuthKey(ctx, sessionID, domain.TempAuthKeyBinding{\n',
    '\tsessionID, _ := SessionIDFrom(ctx)\n'
    '\tpermID := authKeyIDFromInt64(req.PermAuthKeyID)\n'
    '\tif r.log != nil {\n'
    '\t\tr.log.Info("auth.bindTempAuthKey requested",\n'
    '\t\t\tzap.String("raw_auth_key_id", fmt.Sprintf("%x", id[:])),\n'
    '\t\t\tzap.String("perm_auth_key_id", fmt.Sprintf("%x", permID[:])),\n'
    '\t\t\tzap.Int64("session_id", sessionID),\n'
    '\t\t)\n'
    '\t}\n'
    '\tboundState, err := r.deps.Auth.BindTempAuthKey(ctx, sessionID, domain.TempAuthKeyBinding{\n',
    "temp auth key bind attempt logging",
)

replace_auth_once(
    '\tif err != nil {\n\t\treturn false, bindTempAuthKeyErr(err)\n\t}\n\tpermID := authKeyIDFromInt64(req.PermAuthKeyID)\n',
    '\tif err != nil {\n'
    '\t\tif r.log != nil {\n'
    '\t\t\tr.log.Warn("auth.bindTempAuthKey rejected",\n'
    '\t\t\t\tzap.String("raw_auth_key_id", fmt.Sprintf("%x", id[:])),\n'
    '\t\t\t\tzap.String("perm_auth_key_id", fmt.Sprintf("%x", permID[:])),\n'
    '\t\t\t\tzap.Int64("session_id", sessionID),\n'
    '\t\t\t\tzap.Error(err),\n'
    '\t\t\t)\n'
    '\t\t}\n'
    '\t\treturn false, bindTempAuthKeyErr(err)\n'
    '\t}\n'
    '\tif r.log != nil {\n'
    '\t\tr.log.Info("auth.bindTempAuthKey committed",\n'
    '\t\t\tzap.String("raw_auth_key_id", fmt.Sprintf("%x", id[:])),\n'
    '\t\t\tzap.String("perm_auth_key_id", fmt.Sprintf("%x", permID[:])),\n'
    '\t\t\tzap.Int64("session_id", sessionID),\n'
    '\t\t\tzap.Int("layer", boundState.Layer),\n'
    '\t\t)\n'
    '\t}\n',
    "temp auth key bind result logging",
)

replace_auth_once(
    '\tif err != nil {\n'
    '\t\tif errors.Is(err, domain.ErrSessionPasswordNeeded) && u.ID != 0 {\n',
    '\tif err != nil {\n'
    '\t\tif r.log != nil {\n'
    '\t\t\trawID, _ := RawAuthKeyIDFrom(ctx)\n'
    '\t\t\tauthID, _ := AuthKeyIDFrom(ctx)\n'
    '\t\t\tsessionID, _ := SessionIDFrom(ctx)\n'
    '\t\t\tr.log.Info("auth.signIn did not complete",\n'
    '\t\t\t\tzap.String("raw_auth_key_id", fmt.Sprintf("%x", rawID[:])),\n'
    '\t\t\t\tzap.String("auth_key_id", fmt.Sprintf("%x", authID[:])),\n'
    '\t\t\t\tzap.Int64("session_id", sessionID),\n'
    '\t\t\t\tzap.Int64("user_id", u.ID),\n'
    '\t\t\t\tzap.Bool("need_sign_up", needSignUp),\n'
    '\t\t\t\tzap.Error(err),\n'
    '\t\t\t)\n'
    '\t\t}\n'
    '\t\tif errors.Is(err, domain.ErrSessionPasswordNeeded) && u.ID != 0 {\n',
    "sign-in failure diagnostics",
)

sign_in_start = auth_text.index("func (r *Router) finishAuthSignIn")
sign_in_end = auth_text.index("func (r *Router) onAuthResendCode", sign_in_start)
sign_in_text = auth_text[sign_in_start:sign_in_end]
sign_in_old = (
    '\tif id, ok := AuthKeyIDFrom(ctx); ok {\n'
    '\t\tr.setAuthUserCache(id, u.ID, true)\n'
    '\t}\n'
    '\tr.bindSessionUser(ctx, u.ID)\n'
)
sign_in_new = (
    '\trawID, _ := RawAuthKeyIDFrom(ctx)\n'
    '\tauthID, _ := AuthKeyIDFrom(ctx)\n'
    '\tsessionID, _ := SessionIDFrom(ctx)\n'
    '\tif r.log != nil {\n'
    '\t\tr.log.Info("auth.signIn authorization committed",\n'
    '\t\t\tzap.String("raw_auth_key_id", fmt.Sprintf("%x", rawID[:])),\n'
    '\t\t\tzap.String("auth_key_id", fmt.Sprintf("%x", authID[:])),\n'
    '\t\t\tzap.Int64("session_id", sessionID),\n'
    '\t\t\tzap.Int64("user_id", u.ID),\n'
    '\t\t)\n'
    '\t}\n'
    '\tif id, ok := AuthKeyIDFrom(ctx); ok {\n'
    '\t\tr.setAuthUserCache(id, u.ID, true)\n'
    '\t}\n'
    '\tr.bindSessionUser(ctx, u.ID)\n'
)
if sign_in_text.count(sign_in_old) != 1:
    raise SystemExit("RiseGram MTProto patch expected one successful sign-in block inside finishAuthSignIn")
sign_in_text = sign_in_text.replace(sign_in_old, sign_in_new, 1)
auth_text = auth_text[:sign_in_start] + sign_in_text + auth_text[sign_in_end:]

auth_source.write_text(auth_text)
source.write_text(text)
print(
    "RiseGram MTProto source patch applied; both advertised-address log records "
    "use the external TCP endpoint."
)
