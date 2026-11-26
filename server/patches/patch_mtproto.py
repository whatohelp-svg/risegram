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

# effectiveUserID is also used by the connection/session path. A zero user ID
# is only a cold-session placeholder; it must not be returned as an authorized
# identity before the durable permanent auth key has been checked.
patch_go_file(
    "internal/rpc/router.go",
    """\tif userID, ok := UserIDFrom(ctx); ok {
\t\tif r.deps.Sessions != nil {
\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, userID)
\t\t}
\t\treturn userID, true, nil
\t}
""",
    """\tif userID, ok := UserIDFrom(ctx); ok && userID != 0 {
\t\tif r.deps.Sessions != nil {
\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, userID)
\t\t}
\t\treturn userID, true, nil
\t}
""",
    "effectiveUserID zero-user reconnect fallback",
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


# The generated dispatcher prepares every incoming RPC through effectiveUserID.
# A cold/new TCP session can already have a resolved zero marker from the
# temporary-key handshake. That marker is not durable authorization state:
# re-read the permanent business key from PostgreSQL before rejecting it.
patch_go_file(
    "internal/rpc/router.go",
    """\t\tif userID, resolved := r.deps.Sessions.UserIDResolvedForAuthKey(rawAuthKeyID, sessionID); resolved {
\t\t\tif userID == 0 {
\t\t\t\tif cachedUserID, ok := r.positiveCachedAuthUser(authKeyID); ok {
\t\t\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, cachedUserID)
\t\t\t\t\tr.announceSessionOnline(ctx, cachedUserID)
\t\t\t\t\treturn cachedUserID, true, nil
\t\t\t\t}
\t\t\t}
\t\t\treturn userID, userID != 0, nil
\t\t}
""",
    """\t\tif userID, resolved := r.deps.Sessions.UserIDResolvedForAuthKey(rawAuthKeyID, sessionID); resolved && userID != 0 {
\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, userID)
\t\t\treturn userID, true, nil
\t\t} else if resolved {
\t\t\tif cachedUserID, ok := r.positiveCachedAuthUser(authKeyID); ok {
\t\t\t\tr.deps.Sessions.BindUserForAuthKey(rawAuthKeyID, sessionID, cachedUserID)
\t\t\t\tr.announceSessionOnline(ctx, cachedUserID)
\t\t\t\treturn cachedUserID, true, nil
\t\t\t}
\t\t\t// No positive cache entry: continue into lookupAuthUser below.
\t\t\t// The auth table, not the empty session marker, decides whether the
\t\t\t// permanent key is still authorized.
\t\t}
""",
    "effectiveUserID cold-session zero marker fallback",
)

# If the response to auth.signIn was lost after the database commit, Android
# may retry the same RPC while the one-time code is already consumed. A retry
# from the very same already-authorized key and same phone is safe to complete
# idempotently; this does not authorize a different user or bypass a fresh login.
patch_go_file(
    "internal/rpc/auth.go",
    """func (r *Router) onAuthSignIn(ctx context.Context, req *tg.AuthSignInRequest) (tg.AuthAuthorizationClass, error) {
\tvar (
""",
    """func (r *Router) onAuthSignIn(ctx context.Context, req *tg.AuthSignInRequest) (tg.AuthAuthorizationClass, error) {
\tstartedAt := time.Now()
\tctx, cancel := context.WithTimeout(ctx, 25*time.Second)
\tdefer cancel()
\tsessionID, _ := SessionIDFrom(ctx)
\tauthID, _ := AuthKeyIDFrom(ctx)
\tif r.log != nil {
\t\tr.log.Info("auth.signIn received",
\t\t\tzap.String("auth_key_id", fmt.Sprintf("%x", authID[:])),
\t\t\tzap.Int64("session_id", sessionID),
\t\t)
\t}
\tdefer func() {
\t\tif r.log != nil {
\t\t\tr.log.Info("auth.signIn handler finished",
\t\t\t\tzap.String("auth_key_id", fmt.Sprintf("%x", authID[:])),
\t\t\t\tzap.Int64("session_id", sessionID),
\t\t\t\tzap.Int64("duration_ms", time.Since(startedAt).Milliseconds()),
\t\t\t)
\t\t}
\t}()
\tif userID, authorized, resolveErr := r.currentUserID(ctx); resolveErr == nil && authorized && userID != 0 {
\t\tif current, userErr := r.deps.Users.Self(ctx, userID); userErr == nil && current.Phone == req.PhoneNumber {
\t\t\treturn &tg.AuthAuthorization{User: r.tgSelfUserWithUsernames(ctx, current)}, nil
\t\t}
\t}
\tvar (
""",
    "idempotent auth.signIn replay for the already-authorized same phone",
)

print("RiseGram MTProto reconnect and sign-in retry fixes patched.")



# Fixed, owner-confirmed gift order worker. No auth-key/session export.
patch_go_file(
    "cmd/telesrv/main.go",
    "\tgo rpc.NewBootstrapUpdateDispatcher(router, logger.Named(\"rpc\").Named(\"bootstrap\")).Run(ctx)\n",
    "\tgo router.RunRiseGramGiftPermits(ctx, pool, logger.Named(\"risegram-gift-permit\"))\n"
    "\tgo rpc.NewBootstrapUpdateDispatcher(router, logger.Named(\"rpc\").Named(\"bootstrap\")).Run(ctx)\n",
    "owner-confirmed gift worker",
)


# Display-only valuation preferences use authenticated native viewer IDs.
patch_go_file(
    "cmd/telesrv/main.go",
    "\tgo router.RunRiseGramGiftPermits(ctx, pool, logger.Named(\"risegram-gift-permit\"))\n",
    "\trpc.InitRiseGramGiftValuePreferences(pool)\n"
    "\tgo router.RunRiseGramGiftPermits(ctx, pool, logger.Named(\"risegram-gift-permit\"))\n",
    "gift value preferences pool",
)
patch_go_file(
    "internal/rpc/payments_star_gift_lifecycle.go",
    "\tout := &tg.PaymentsUniqueStarGiftValueInfo{Currency: info.Currency, Value: info.Value,\n",
    "\tinfo = rgDisplayGiftValue(ctx, userID, info)\n"
    "\tout := &tg.PaymentsUniqueStarGiftValueInfo{Currency: info.Currency, Value: info.Value,\n",
    "per-viewer gift valuation currency",
)


# Preserve ordinary same-collection crafting when the optional AI cross-craft
# capability is disabled. The native store still validates ownership, readiness,
# model eligibility and all consumption atomically; do not synthesize chances.
patch_go_file(
    "internal/rpc/payments_star_gifts.go",
    """		if !allowed {
			for i := range gifts {
				gift := &gifts[i]
				if gift.Owner != (domain.Peer{Type: domain.PeerTypeUser, ID: viewerUserID}) {
					continue
				}
				gift.CanCraftAt = 0
				if gift.Unique != nil {
					gift.Unique.CraftChancePermille = 0
				}
			}
		} else {
""",
    """		// A disabled cross-collection generator must not hide ordinary craft.
		// Existing CanCraftAt and CraftChancePermille are the native entitlement.
		if allowed {
""",
    "ordinary NFT crafting remains available without optional AI cross-craft",
)


# A versioned, idempotent reaction asset replacement: import the new catalog
# even when the old Noto catalog is present. Do not delete message reactions.
patch_go_file(
    "internal/app/files/seed.go",
    """	// reactions
	if n, err := s.media.CountAvailableReactions(ctx); err != nil {
		return stats, err
	} else if n == 0 {
""",
    """	// reactions
	reactionSeedVersion := "risegram-telegram-assets-v1"
	reactionSeedReady, err := s.seedStateMatches(ctx, "risegram_reaction_assets", reactionSeedVersion)
	if err != nil {
		return stats, err
	}
	if n, err := s.media.CountAvailableReactions(ctx); err != nil {
		return stats, err
	} else if n == 0 || !reactionSeedReady {
""",
    "versioned Telegram reaction asset import",
)
patch_go_file(
    "internal/app/files/seed.go",
    """	s.logSeedPhase("reactions", phaseStarted, phaseBefore, stats)
""",
    """	if stats.Reactions > phaseBefore.Reactions {
		if err := s.putSeedState(ctx, "risegram_reaction_assets", reactionSeedVersion); err != nil {
			return stats, err
		}
	}
	s.logSeedPhase("reactions", phaseStarted, phaseBefore, stats)
""",
    "record completed reaction asset import",
)


# Accept genuine crafted-only model tiers through the multipart drop publisher.
# Preserve the existing validation and do not make crafted models upgrade rolls.
patch_go_file(
    "internal/admin/service.go",
    """type StarGiftCollectibleAnimationUpload struct {
\tName           string `json:"name"`
\tRarityPermille int    `json:"rarity_permille"`
""",
    """type StarGiftCollectibleAnimationUpload struct {
\tName           string `json:"name"`
\tRarityPermille int    `json:"rarity_permille"`
\tRarityKind     string `json:"rarity_kind,omitempty"`
\tCrafted        bool   `json:"crafted,omitempty"`
""",
    "accept crafted model metadata",
)
patch_go_file(
    "internal/admin/service.go",
    """\t\t\tattributes[i] = domain.StarGiftCollectibleAttribute{
\t\t\t\tKind: kind, Name: strings.TrimSpace(uploads[i].Name), RarityKind: domain.StarGiftRarityPermille,
\t\t\t\tRarityPermille: uploads[i].RarityPermille,
""",
    """\t\t\trarityKind := domain.StarGiftRarityPermille
\t\t\tif uploads[i].Crafted {
\t\t\t\tif kind != domain.StarGiftCollectibleModel || uploads[i].RarityPermille != 0 {
\t\t\t\t\treturn nil, domain.ErrStarGiftCollectibleInvalid
\t\t\t\t}
\t\t\t\tswitch uploads[i].RarityKind {
\t\t\t\tcase "uncommon", "rare", "epic", "legendary":
\t\t\t\t\trarityKind = domain.StarGiftAttributeRarityKind(uploads[i].RarityKind)
\t\t\t\tdefault:
\t\t\t\t\treturn nil, domain.ErrStarGiftCollectibleInvalid
\t\t\t\t}
\t\t\t} else if uploads[i].RarityKind != "" && uploads[i].RarityKind != "permille" {
\t\t\t\treturn nil, domain.ErrStarGiftCollectibleInvalid
\t\t\t}
\t\t\tattributes[i] = domain.StarGiftCollectibleAttribute{
\t\t\t\tKind: kind, Name: strings.TrimSpace(uploads[i].Name), RarityKind: rarityKind,
\t\t\t\tRarityPermille: uploads[i].RarityPermille, Crafted: uploads[i].Crafted,
""",
    "publish true crafted model rarity",
)


patch_go_file(
    "internal/admin/service.go",
    """\t\t\t"name": strings.TrimSpace(upload.Name), "rarity_permille": upload.RarityPermille,
""",
    """\t\t\t"name": strings.TrimSpace(upload.Name), "rarity_permille": upload.RarityPermille,
\t\t\t"rarity_kind": upload.RarityKind, "crafted": upload.Crafted,
""",
    "report crafted metadata in validation receipts",
)


# Temporary reversible message-reaction visibility gate; no data migration.
Path("internal/rpc/risegram_hide_reactions.go").write_text('package rpc\n\nimport "os"\n\nfunc risegramHideReactions() bool { return os.Getenv("RISEGRAM_HIDE_REACTIONS") == "1" }\n')
patch_go_file("internal/rpc/stickers.go", "func (r *Router) onMessagesGetAvailableReactions(ctx context.Context, hash int) (tg.MessagesAvailableReactionsClass, error) {", "func (r *Router) onMessagesGetAvailableReactions(ctx context.Context, hash int) (tg.MessagesAvailableReactionsClass, error) {\n\tif risegramHideReactions() {\n\t\treturn &tg.MessagesAvailableReactions{Hash: 0, Reactions: []tg.AvailableReaction{}}, nil\n\t}", "temporary hide reactions")
patch_go_file("internal/rpc/convert_messages.go", "func tgMessageReactions(viewerUserID int64, in *domain.ChannelMessageReactions) *tg.MessageReactions {", "func tgMessageReactions(viewerUserID int64, in *domain.ChannelMessageReactions) *tg.MessageReactions {\n\tif risegramHideReactions() { return nil }", "temporary hide reactions")
patch_go_file("internal/rpc/messages_reactions_send.go", "func (r *Router) onMessagesSendReaction(ctx context.Context, req *tg.MessagesSendReactionRequest) (tg.UpdatesClass, error) {", "func (r *Router) onMessagesSendReaction(ctx context.Context, req *tg.MessagesSendReactionRequest) (tg.UpdatesClass, error) {\n\tif risegramHideReactions() { return nil, reactionInvalidErr() }", "temporary hide reactions")
patch_go_file("internal/rpc/messages_reactions_privacy.go", "func (r *Router) onMessagesSendPaidReaction(ctx context.Context, req *tg.MessagesSendPaidReactionRequest) (tg.UpdatesClass, error) {", "func (r *Router) onMessagesSendPaidReaction(ctx context.Context, req *tg.MessagesSendPaidReactionRequest) (tg.UpdatesClass, error) {\n\tif risegramHideReactions() { return nil, reactionInvalidErr() }", "temporary hide reactions")
patch_go_file("internal/rpc/messages_reactions_catalog.go", "func (r *Router) onMessagesGetTopReactions(ctx context.Context, req *tg.MessagesGetTopReactionsRequest) (tg.MessagesReactionsClass, error) {", "func (r *Router) onMessagesGetTopReactions(ctx context.Context, req *tg.MessagesGetTopReactionsRequest) (tg.MessagesReactionsClass, error) {\n\tif risegramHideReactions() { return messagesReactionsEmpty(req.Hash), nil }", "temporary hide reactions")
patch_go_file("internal/rpc/messages_reactions_catalog.go", "func (r *Router) onMessagesGetRecentReactions(ctx context.Context, req *tg.MessagesGetRecentReactionsRequest) (tg.MessagesReactionsClass, error) {", "func (r *Router) onMessagesGetRecentReactions(ctx context.Context, req *tg.MessagesGetRecentReactionsRequest) (tg.MessagesReactionsClass, error) {\n\tif risegramHideReactions() { return messagesReactionsEmpty(req.Hash), nil }", "temporary hide reactions")
patch_go_file("internal/rpc/messages_reactions_lists.go", "func (r *Router) onMessagesGetMessageReactionsList(ctx context.Context, req *tg.MessagesGetMessageReactionsListRequest) (*tg.MessagesMessageReactionsList, error) {", "func (r *Router) onMessagesGetMessageReactionsList(ctx context.Context, req *tg.MessagesGetMessageReactionsListRequest) (*tg.MessagesMessageReactionsList, error) {\n\tif risegramHideReactions() { return &tg.MessagesMessageReactionsList{}, nil }", "temporary hide reactions")
patch_go_file("internal/rpc/messages_reactions_lists.go", "func (r *Router) onMessagesGetUnreadReactions(ctx context.Context, req *tg.MessagesGetUnreadReactionsRequest) (tg.MessagesMessagesClass, error) {", "func (r *Router) onMessagesGetUnreadReactions(ctx context.Context, req *tg.MessagesGetUnreadReactionsRequest) (tg.MessagesMessagesClass, error) {\n\tif risegramHideReactions() { return &tg.MessagesMessages{}, nil }", "temporary hide reactions")
