"""Allow frozen RiseGram members to send TEXT ONLY to the dedicated appeal account.

The generic frozen write gate normally rejects every send RPC. We defer ONLY
messages.sendMessage to the typed handler, which re-checks freeze state
(fail-closed) and validates the destination's immutable native user ID before
allowing the normal authorization/access-hash/message-send pipeline to run.
Media, channel posts, edits, forwards, bot API and payment paths stay blocked.

Configuration: RISEGRAM_FROZEN_APPEAL_USER_ID set to the verified native
account ID. An absent or invalid ID disables this exception.
"""
from pathlib import Path

gate = Path("internal/rpc/frozen_gate.go")
s = gate.read_text()
old = '''func (r *Router) checkFrozenRPC(ctx context.Context, method string) error {
	if r == nil || r.deps.AccountFreeze == nil || !frozenMethodRequiresWriteGate(method) {
		return nil
	}
'''
new = '''func (r *Router) checkFrozenRPC(ctx context.Context, method string) error {
	if method == "messages.sendMessage" {
		// This method alone is rechecked by onMessagesSendMessage using the
		// destination's immutable user ID, not a spoofable username.
		return nil
	}
	if r == nil || r.deps.AccountFreeze == nil || !frozenMethodRequiresWriteGate(method) {
		return nil
	}
'''
if s.count(old) != 1:
    raise SystemExit("frozen appeal patch: generic gate changed upstream")
gate.write_text(s.replace(old,new,1))

send = Path("internal/rpc/messages_send.go")
s = send.read_text()
old_import = '''	"errors"
	"sort"'''
new_import = '''	"errors"
	"os"
	"strconv"
	"sort"'''
if s.count(old_import) != 1:
    raise SystemExit("frozen appeal patch: messages import changed")
s = s.replace(old_import,new_import,1)
old_func = '''func (r *Router) onMessagesSendMessage(ctx context.Context, req *tg.MessagesSendMessageRequest) (tg.UpdatesClass, error) {
	start := r.clock.Now()
	var duplicate bool
	var sendErr error
	defer func() {
		r.metrics().MessageSend(r.clock.Now().Sub(start), duplicate, sendErr)
	}()
'''
new_func = '''func (r *Router) onMessagesSendMessage(ctx context.Context, req *tg.MessagesSendMessageRequest) (tg.UpdatesClass, error) {
	start := r.clock.Now()
	var duplicate bool
	var sendErr error
	defer func() {
		r.metrics().MessageSend(r.clock.Now().Sub(start), duplicate, sendErr)
	}()
	// The generic read-only gate delegates this exact RPC to the handler so
	// any frozen account can send a text-only appeal to one audited account.
	if err := r.checkFrozenAppealMessage(ctx, req); err != nil {
		sendErr = err
		return nil, err
	}
'''
if s.count(old_func) != 1:
    raise SystemExit("frozen appeal patch: send handler changed upstream")
s=s.replace(old_func,new_func,1)
old_marker = "// Bound the number of independent writes one RPC may admit."
new_block = '''// frozenAppealUserID is immutable for the lifetime of a server process.
// Resolving names on each request would permit username swaps to redirect
// appeals, so the operator pins a verified native user ID instead.
var frozenAppealUserID = func() int64 {
	id, err := strconv.ParseInt(os.Getenv("RISEGRAM_FROZEN_APPEAL_USER_ID"), 10, 64)
	if err != nil || id <= 0 {
		return 0
	}
	return id
}()

func (r *Router) checkFrozenAppealMessage(ctx context.Context, req *tg.MessagesSendMessageRequest) error {
	if r == nil || r.deps.AccountFreeze == nil {
		return nil
	}
	userID, ok := UserIDFrom(ctx)
	if !ok || userID <= 0 {
		return nil
	}
	state, found, err := r.deps.AccountFreeze.AccountFreeze(ctx, userID)
	if err != nil {
		return internalErr()
	}
	if !found || !state.Frozen {
		return nil
	}
	if req == nil || frozenAppealUserID <= 0 || userID == frozenAppealUserID {
		return frozenMethodInvalidErr()
	}
	peer, ok := req.Peer.(*tg.InputPeerUser)
	if !ok || peer.UserID != frozenAppealUserID ||
		req.QuickReplyShortcut != nil || req.ScheduleDate != 0 ||
		req.ReplyMarkup != nil || req.RichMessage != nil ||
		strings.TrimSpace(req.Message) == "" {
		return frozenMethodInvalidErr()
	}
	// Normal message validation and destination access-hash checks follow.
	return nil
}

''' + old_marker
if s.count(old_marker)!=1:
    raise SystemExit("frozen appeal patch: marker missing")
s=s.replace(old_marker,new_block,1)
send.write_text(s)

# Existing tests intentionally expected *every* frozen send to fail at the
# generic gate. They now must verify that only sends are handed to the typed
# handler where the destination check occurs.
tests=Path("internal/rpc/frozen_gate_test.go")
t=tests.read_text()
old_test='''if err := router.checkFrozenRPC(ctx, "messages.sendMessage"); !tgerr.Is(err, "FROZEN_METHOD_INVALID") {
				t.Fatalf("session %d err = %v, want FROZEN_METHOD_INVALID", sessionID, err)
			}'''
new_test='''if err := router.checkFrozenRPC(ctx, "messages.sendMessage"); err != nil {
				t.Fatalf("session %d generic gate = %v, should defer to target guard", sessionID, err)
			}
			if err := router.checkFrozenAppealMessage(ctx, &tg.MessagesSendMessageRequest{Peer: &tg.InputPeerSelf{}, Message: "blocked"}); !tgerr.Is(err, "FROZEN_METHOD_INVALID") {
				t.Fatalf("session %d target guard = %v, want FROZEN_METHOD_INVALID", sessionID, err)
			}'''
if t.count(old_test) != 1:
    raise SystemExit("frozen appeal patch: old test expectations not found")
t=t.replace(old_test,new_test,1)
# Layer-specific tests that assert gate rejection must now enforce the
# rejected target through the typed handler; not a blanket gate.
# Their current hard-coded targets are InputPeerSelf and should always fail.
tests.write_text(t)
