"""Keep upstream frozen tombstone presentation; allow text appeals to support."""
from pathlib import Path

# Only frozen accounts may send a TEXT appeal message to the dedicated native
# @youtube help account. Preserve the frozen RPC gate for every other mutation:
# one exact method is deferred to its typed handler for peer validation.
gate_path = Path("internal/rpc/frozen_gate.go")
gate_src = gate_path.read_text()
gate_old = '''func frozenMethodRequiresWriteGate(method string) bool {
	if _, blocked := frozenAlwaysBlockedMethods[method]; blocked {'''
gate_new = '''func frozenMethodRequiresWriteGate(method string) bool {
	// The typed messages.sendMessage handler checks frozen state and the
	// destination peer before any writes. Other message RPCs stay blocked.
	if method == "messages.sendMessage" {
		return false
	}
	if _, blocked := frozenAlwaysBlockedMethods[method]; blocked {'''
if gate_src.count(gate_old)!=1:
    raise SystemExit("Frozen appeal patch: gate anchor changed")
gate_path.write_text(gate_src.replace(gate_old, gate_new, 1))

messages_path = Path("internal/rpc/messages_send.go")
messages_src = messages_path.read_text()
message_old = '''func (r *Router) onMessagesSendMessage(ctx context.Context, req *tg.MessagesSendMessageRequest) (tg.UpdatesClass, error) {
	start := r.clock.Now()'''
message_new = '''const frozenAppealRecipientID int64 = 1780243212 // native RiseGram @youtube

// checkFrozenAppealMessage runs *after* typed RPC admission. A frozen user can
// write a direct, unscheduled plain-text message only to the native appeal
// account, never to a channel, forwarded peer or user chosen by username text.
func (r *Router) checkFrozenAppealMessage(ctx context.Context, req *tg.MessagesSendMessageRequest) error {
	if r == nil || r.deps.AccountFreeze == nil {
		return nil
	}
	userID, authorized := UserIDFrom(ctx)
	if !authorized || userID == 0 {
		return nil
	}
	freeze, found, err := r.deps.AccountFreeze.AccountFreeze(ctx, userID)
	if err != nil {
		return internalErr()
	}
	if !found || !freeze.Frozen {
		return nil
	}
	peer, ok := req.Peer.(*tg.InputPeerUser)
	if !ok || peer.UserID != frozenAppealRecipientID ||
		req.ScheduleDate != 0 || req.QuickReplyShortcut != nil ||
		req.RichMessage != nil || req.AllowPaidStars != 0 {
		return frozenMethodInvalidErr()
	}
	return nil
}

func (r *Router) onMessagesSendMessage(ctx context.Context, req *tg.MessagesSendMessageRequest) (tg.UpdatesClass, error) {
	if err := r.checkFrozenAppealMessage(ctx, req); err != nil {
		return nil, err
	}
	start := r.clock.Now()'''
if messages_src.count(message_old)!=1:
    raise SystemExit("Frozen appeal patch: typed handler anchor changed")
messages_path.write_text(messages_src.replace(message_old,message_new,1))

tests_path=Path("internal/rpc/frozen_gate_test.go")
test_src=tests_path.read_text()
old_case='''"messages.sendMessage":             true,'''
new_case='''"messages.sendMessage":             false, // guarded in typed handler'''
if test_src.count(old_case)!=1:
    raise SystemExit("Frozen appeal patch: method test anchor changed")
test_src=test_src.replace(old_case,new_case,1)
# A direct method-gate test now uses a different mutation. End-to-end
# dispatch tests still exercise sendMessage and must return code 420.
start_test=test_src.index("func TestFrozenMethodGateIsUserScopedAcrossSessionsAndUnfreezesImmediately")
end_test=test_src.index("func ", start_test+6)
part=test_src[start_test:end_test]
if part.count('"messages.sendMessage"') != 3:
    raise SystemExit("Frozen appeal patch: session-gate test changed")
test_src=test_src[:start_test]+part.replace('"messages.sendMessage"', '"messages.sendMedia"')+test_src[end_test:]
tests_path.write_text(test_src)

# Describe the synthetic snowflake badge in the user's language. The badge is
# a viewer-scoped projection and never overwrites a real username or verification.
frozen_labels = Path("internal/rpc/bot_verification_projection.go")
label_src = frozen_labels.read_text()
old_label = 'accountFrozenMarkDescription       = "The account was frozen"'
new_label = 'accountFrozenMarkDescription       = "Аккаунт заморожен"'
if label_src.count(old_label) != 1:
    raise SystemExit("Frozen badge description anchor differs from pinned MTProto")
frozen_labels.write_text(label_src.replace(old_label, new_label, 1))

# The stock Android frozen-account sheet still has an @SpamBot hyperlink
# baked into the client language. This is NOT a public @spambot username:
# only while the caller is frozen, resolve that old hyperlink to the actual
# RiseGram support account @youtube. No global username is registered, and
# every other user resolves usernames normally.
contacts_path = Path("internal/rpc/contacts.go")
contacts_src = contacts_path.read_text()
contacts_old = '''func (r *Router) onContactsResolveUsername(ctx context.Context, req *tg.ContactsResolveUsernameRequest) (*tg.ContactsResolvedPeer, error) {
	userID, _, err := r.currentUserID(ctx)
	if err != nil {
		return nil, internalErr()
	}
	if svc, ok := r.deps.Users.(UserIdentityService); ok {
		u, found, err := svc.ResolveUsername(ctx, userID, req.Username)'''
contacts_new = '''func (r *Router) onContactsResolveUsername(ctx context.Context, req *tg.ContactsResolveUsernameRequest) (*tg.ContactsResolvedPeer, error) {
	userID, _, err := r.currentUserID(ctx)
	if err != nil {
		return nil, internalErr()
	}
	if req == nil {
		return nil, usernameNotOccupiedErr()
	}
	username := req.Username
	// legacyFreezeAppealAlias routes only a frozen caller's built-in Telegram
	// SpamBot link to their designated RiseGram appeal recipient. The actual
	// profile clearly identifies itself as RiseGram support, not Telegram.
	if userID != 0 && strings.EqualFold(strings.TrimPrefix(strings.TrimSpace(username), "@"), "spambot") && r.deps.AccountFreeze != nil {
		freeze, found, freezeErr := r.deps.AccountFreeze.AccountFreeze(ctx, userID)
		if freezeErr != nil {
			return nil, internalErr()
		}
		if found && freeze.Frozen {
			username = "youtube"
		}
	}
	if svc, ok := r.deps.Users.(UserIdentityService); ok {
		u, found, err := svc.ResolveUsername(ctx, userID, username)'''
if contacts_src.count(contacts_old)!=1:
    raise SystemExit("Frozen appeal alias patch: contacts handler differs from pinned upstream")
contacts_src=contacts_src.replace(contacts_old,contacts_new,1)
# Make sure a stale @SpamBot query can never be reused for channel search.
method_start=contacts_src.index("func (r *Router) onContactsResolveUsername")
method_end=contacts_src.index("func (r *Router) onContactsResolvePhone",method_start)
snippet=contacts_src[method_start:method_end]
old_search='r.deps.Channels.ResolvePublicUsername(ctx, userID, req.Username)'
if snippet.count(old_search)!=1:
    raise SystemExit("Frozen appeal alias patch: channel lookup changed")
contacts_src=contacts_src[:method_start]+snippet.replace(old_search,"r.deps.Channels.ResolvePublicUsername(ctx, userID, username)",1)+contacts_src[method_end:]
contacts_path.write_text(contacts_src)

# For new/old freeze overlays, supply an internal protocol deep link rather
# than sending users into the unrelated Telegram app via https://t.me/YouTube.
# The client may continue displaying its baked-in @SpamBot wording; the alias
# above ensures tapping the old username reaches the native support account.
help_path=Path("internal/app/help/service.go")
help_src=help_path.read_text()
help_old='appeal, _ := json.Marshal(freeze.AppealURL)'
help_new='appeal, _ := json.Marshal("tg://resolve?domain=youtube")'
if help_src.count(help_old)!=1:
    raise SystemExit("Frozen appeal URL overlay anchor changed")
help_path.write_text(help_src.replace(help_old,help_new,1))
