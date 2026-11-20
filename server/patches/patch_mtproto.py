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


# Resolve a previously signed-in user's persistent business auth key when a
# reconnect creates a fresh TCP/session context. The raw temp key may be new,
# but the permanent auth key still identifies the same account. Do not reject
# the first ordinary RPC merely because the per-connection UserID cache is cold.
def patch_go_file(path: str, old: str, new: str, label: str) -> None:
    file = Path(path)
    content = file.read_text()
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"RiseGram auth-resume patch expected one {label} match in {path}, found {count}")
    file.write_text(content.replace(old, new, 1))


patch_go_file(
    "internal/rpc/layer_dispatch.go",
    """\tif r.deps.Auth != nil {
\t\tif _, authorized := UserIDFrom(ctx); !authorized && !rpcAllowedWithoutAuthorization(canonicalID) {
\t\t\tfields := append([]zap.Field{
\t\t\t\tzap.String("method", method),
\t\t\t\tzap.String("type_id", fmt.Sprintf("%#x", call.WireID())),
\t\t\t\tzap.Int("profile", int(call.Profile())),
\t\t\t}, r.contextLogFields(ctx)...)
\t\t\tr.log.Info("RPC rejected before authorization", fields...)
\t\t\treturn nil, method, authKeyUnregisteredErr()
\t\t}
\t}
""",
    """\tif r.deps.Auth != nil {
\t\tcachedUserID, authorized := UserIDFrom(ctx)
\t\tif (!authorized || cachedUserID == 0) && !rpcAllowedWithoutAuthorization(canonicalID) {
\t\t\t// On a reconnect the TCP session cache is cold even though the
\t\t\t// permanent business auth key remains authorized in the database.
\t\t\t// Resolve it once here and seed this request context before applying
\t\t\t// the normal authorization gate.
\t\t\tuserID, resolved, resolveErr := r.currentUserID(ctx)
\t\t\tif resolveErr != nil {
\t\t\t\tif r.log != nil {
\t\t\t\t\tr.log.Warn("resolve reconnect authorization failed",
\t\t\t\t\t\tzap.String("method", method),
\t\t\t\t\t\tzap.Error(resolveErr),
\t\t\t\t\t)
\t\t\t\t}
\t\t\t\treturn nil, method, internalErr()
\t\t\t}
\t\t\tif !resolved || userID == 0 {
\t\t\t\tfields := append([]zap.Field{
\t\t\t\t\tzap.String("method", method),
\t\t\t\t\tzap.String("type_id", fmt.Sprintf("%#x", call.WireID())),
\t\t\t\t\tzap.Int("profile", int(call.Profile())),
\t\t\t\t}, r.contextLogFields(ctx)...)
\t\t\t\tif r.log != nil {
\t\t\t\t\tr.log.Info("RPC rejected before authorization", fields...)
\t\t\t\t}
\t\t\t\treturn nil, method, authKeyUnregisteredErr()
\t\t\t}
\t\t\tctx = WithUserID(ctx, userID)
\t\t}
\t}
""",
    "layer-dispatch reconnect authorization gate",
)


patch_go_file(
    "internal/rpc/router.go",
    """\t\tif r.deps.Auth != nil {
\t\t\tif _, ok := UserIDFrom(ctx); !ok && !rpcAllowedWithoutAuthorization(id) {
\t\t\t\tfields := append([]zap.Field{
\t\t\t\t\tzap.String("method", tlTypeName(id)),
\t\t\t\t\tzap.String("type_id", fmt.Sprintf("%#x", id)),
\t\t\t\t}, r.contextLogFields(ctx)...)
\t\t\t\tr.log.Info("RPC rejected before authorization", fields...)
\t\t\t\treturn nil, authKeyUnregisteredErr()
\t\t\t}
\t\t}
""",
    """\t\tif r.deps.Auth != nil {
\t\t\tcachedUserID, ok := UserIDFrom(ctx)
\t\t\tif (!ok || cachedUserID == 0) && !rpcAllowedWithoutAuthorization(id) {
\t\t\t\tuserID, resolved, resolveErr := r.currentUserID(ctx)
\t\t\t\tif resolveErr != nil {
\t\t\t\t\tif r.log != nil {
\t\t\t\t\t\tr.log.Warn("resolve reconnect authorization failed",
\t\t\t\t\t\t\tzap.String("method", tlTypeName(id)),
\t\t\t\t\t\t\tzap.Error(resolveErr),
\t\t\t\t\t\t)
\t\t\t\t\t}
\t\t\t\t\treturn nil, internalErr()
\t\t\t\t}
\t\t\t\tif !resolved || userID == 0 {
\t\t\t\t\tfields := append([]zap.Field{
\t\t\t\t\t\tzap.String("method", tlTypeName(id)),
\t\t\t\t\t\tzap.String("type_id", fmt.Sprintf("%#x", id)),
\t\t\t\t\t}, r.contextLogFields(ctx)...)
\t\t\t\t\tif r.log != nil {
\t\t\t\t\t\tr.log.Info("RPC rejected before authorization", fields...)
\t\t\t\t\t}
\t\t\t\t\treturn nil, authKeyUnregisteredErr()
\t\t\t\t}
\t\t\t\tctx = WithUserID(ctx, userID)
\t\t\t}
\t\t}
""",
    "router reconnect authorization gate",
)

# A freshly-bound TCP session can explicitly cache user_id=0 before the
# permanent auth key's authorization is copied into the session. Treat that
# zero as a cold-session placeholder, not as proof that the permanent key is
# unregistered: fall through to the durable auth-key lookup below. Revoked or
# never-authorized keys still fail because lookupAuthUser checks PostgreSQL.
patch_go_file(
    "internal/rpc/convert_auth.go",
    """\t\t\tif userID, resolved := r.deps.Sessions.UserIDResolvedForAuthKey(rawAuthKeyID, sessionID); resolved {
\t\t\t\tif userID == 0 {
\t\t\t\t\tif authKeyID, ok := AuthKeyIDFrom(ctx); ok {
\t\t\t\t\t\tif cachedUserID, ok := r.positiveCachedAuthUser(authKeyID); ok {
\t\t\t\t\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, cachedUserID)
\t\t\t\t\t\t\tr.announceSessionOnline(ctx, cachedUserID)
\t\t\t\t\t\t\treturn cachedUserID, true, nil
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\treturn userID, userID != 0, nil
\t\t\t}
""",
    """\t\t\tif userID, resolved := r.deps.Sessions.UserIDResolvedForAuthKey(rawAuthKeyID, sessionID); resolved {
\t\t\t\tif userID != 0 {
\t\t\t\t\treturn userID, true, nil
\t\t\t\t}
\t\t\t\tif authKeyID, ok := AuthKeyIDFrom(ctx); ok {
\t\t\t\t\tif cachedUserID, ok := r.positiveCachedAuthUser(authKeyID); ok {
\t\t\t\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, cachedUserID)
\t\t\t\t\t\tr.announceSessionOnline(ctx, cachedUserID)
\t\t\t\t\t\treturn cachedUserID, true, nil
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\t// The connection cache may contain a pre-sign-in zero marker even
\t\t\t\t// after auth.bindTempAuthKey attached a durable business key.
\t\t\t\t// Fall through to lookupAuthUser instead of returning 401 here.
\t\t\t}
""",
    "cold session zero-user fallback to durable authorization",
)

patch_go_file(
    "internal/rpc/convert_auth.go",
    """\tif userID, ok := UserIDFrom(ctx); ok {
\t\treturn userID, true, nil
\t}
""",
    """\tif userID, ok := UserIDFrom(ctx); ok && userID != 0 {
\t\treturn userID, true, nil
\t}
""",
    "ignore zero cached user marker on reconnect",
)

print("RiseGram MTProto reconnect authorization fallback patched.")
