"""Render frozen peers with their actual identity and frozen badge, not Deleted Account."""
from pathlib import Path

# Frozen is NOT deleted: peers must continue seeing the real name and photo,
# with a frozen mark, while the owner sees the app's full-screen read-only UI.
# The pinned upstream intentionally projected frozen peers as tombstones,
# which caused the confusing "Deleted Account" despite retained messages.
projection = Path("internal/app/userprojection/projection.go")
view_src = projection.read_text()
old_view = '''func applyAccountFreezeProjection(user domain.User, viewerUserID int64, freeze domain.AccountFreeze) domain.User {
	// Frozen accounts are presented to peers as deleted accounts: the client
	// renders "Deleted account" instead of the frozen persona. The owner and
	// unauthenticated/system views keep the live account. The tombstone carries
	// FrozenForViewer so the RPC boundary can attach the synthetic "frozen"
	// third-party mark alongside the deleted presentation.
	if freeze.Frozen && viewerUserID != 0 && user.ID != 0 && user.ID != viewerUserID && user.ID != domain.OfficialSystemUserID {
		user.Deleted = true
		tombstone := user.DeletedTombstone()
		tombstone.FrozenForViewer = true
		return tombstone
	}
	if freeze.Frozen {
		user.Status = domain.UserStatus{Kind: domain.UserStatusEmpty}
		user.LastSeenAt = 0
	}
	user.RestrictionReasons = nil
	return user
}'''
new_view = '''func applyAccountFreezeProjection(user domain.User, viewerUserID int64, freeze domain.AccountFreeze) domain.User {
	// A frozen account is read-only, not deleted. Keep identity and chat history
	// stable for peers. Give other authenticated viewers the synthetic frozen
	// mark; the owner still sees their own live account and freeze app config.
	if freeze.Frozen {
		user.FrozenForViewer = viewerUserID != 0 && user.ID != viewerUserID && user.ID != domain.OfficialSystemUserID
		user.Status = domain.UserStatus{Kind: domain.UserStatusEmpty}
		user.LastSeenAt = 0
	} else {
		user.FrozenForViewer = false
	}
	user.RestrictionReasons = nil
	return user
}'''
if view_src.count(old_view) != 1:
    raise SystemExit("Recoverable deletion patch: frozen projection not at pinned version")
projection.write_text(view_src.replace(old_view,new_view,1))

converter = Path("internal/rpc/convert_users.go")
conv_src = converter.read_text()
start=conv_src.index("func tgUser(u domain.User)")
finish=conv_src.index("// applyFrozenUserMark", start)
part=conv_src[start:finish]
old_tail='''	if photo := tgUserProfilePhoto(u); photo != nil {
		out.Photo = photo
	}
	return out
}'''
new_tail='''	if photo := tgUserProfilePhoto(u); photo != nil {
		out.Photo = photo
	}
	applyFrozenUserMark(out, u)
	return out
}'''
if part.count(old_tail) != 1:
    raise SystemExit("Recoverable deletion patch: missing normal frozen user conversion")
conv_src=conv_src[:start]+part.replace(old_tail,new_tail,1)+conv_src[finish:]
converter.write_text(conv_src)

# Full profile responses need the frozen badge too. The wallet patch is already
# applied; insert after the normal contact-restriction projection so both
# the cached and uncached profile paths retain the existing wallet behavior.
userfull=Path("internal/rpc/users.go")
full_src=userfull.read_text()
old_cached='''		applyPrivateContactRestrictionToUserFull(&full, contactRestriction)
		chats := r.applyPersonalChannelToUserFull(ctx, currentUserID, u.PersonalChannelID, &full)'''
new_cached='''		applyPrivateContactRestrictionToUserFull(&full, contactRestriction)
		applyFrozenUserFullMark(&full, u)
		chats := r.applyPersonalChannelToUserFull(ctx, currentUserID, u.PersonalChannelID, &full)'''
if full_src.count(old_cached)!=1:
    raise SystemExit("Frozen profiles patch: cached profile insertion mismatched")
full_src=full_src.replace(old_cached,new_cached,1)
old_uncached='''	applyPrivateContactRestrictionToUserFull(&full, contactRestriction)
	chats := r.applyPersonalChannelToUserFull(ctx, currentUserID, u.PersonalChannelID, &full)'''
new_uncached='''	applyPrivateContactRestrictionToUserFull(&full, contactRestriction)
	applyFrozenUserFullMark(&full, u)
	chats := r.applyPersonalChannelToUserFull(ctx, currentUserID, u.PersonalChannelID, &full)'''
if full_src.count(old_uncached)!=1:
    raise SystemExit("Frozen profiles patch: normal profile insertion mismatched")
userfull.write_text(full_src.replace(old_uncached,new_uncached,1))

# Change the upstream tests with the projection so regressions are caught.
proj_tests=Path("internal/app/userprojection/projection_test.go")
test_src=proj_tests.read_text()
old_assert='''if !got.Deleted {
		t.Fatalf("other-view Deleted = false, want true (frozen account shown as deleted to peers)")
	}'''
new_assert='''if got.Deleted {
		t.Fatalf("other-view Deleted = true, frozen account should retain identity")
	}'''
if test_src.count(old_assert)!=1:
    raise SystemExit("Recoverable deletion patch: upstream projected deletion test changed")
test_src=test_src.replace(old_assert,new_assert,1)
old_identity='''if got.FirstName != "" || got.Username != "" || len(got.RestrictionReasons) != 0 {
		t.Fatalf("other-view kept persona fields: %+v, want tombstone", got)
	}'''
new_identity='''if got.FirstName != "Frozen" || len(got.RestrictionReasons) != 0 || got.Status.Kind != domain.UserStatusEmpty {
		t.Fatalf("other-view missing original profile / empty presence: %+v", got)
	}'''
if test_src.count(old_identity)!=1:
    raise SystemExit("Recoverable deletion patch: upstream identity test changed")
test_src=test_src.replace(old_identity,new_identity,1)
old_batch='''if peer := projectionUser(t, batch[otherViewer], frozenUserID); !peer.Deleted || !peer.FrozenForViewer || len(peer.RestrictionReasons) != 0 {
		t.Fatalf("batch other-view = %+v, want deleted tombstone with frozen mark", peer)
	}'''
new_batch='''if peer := projectionUser(t, batch[otherViewer], frozenUserID); peer.Deleted || !peer.FrozenForViewer || peer.FirstName != "Frozen" || len(peer.RestrictionReasons) != 0 {
		t.Fatalf("batch other-view = %+v, want named frozen peer with frozen mark", peer)
	}'''
if test_src.count(old_batch)!=1:
    raise SystemExit("Recoverable deletion patch: upstream batch test changed")
proj_tests.write_text(test_src.replace(old_batch,new_batch,1))

