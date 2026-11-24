"""Make voluntary RiseGram account deletion recoverable for 30 days.

Pinned upstream grpc/MTProto build integrates the existing account deletion
request, cancel link and one-time confirmation-code validation. We do NOT
tombstone users, release identity, or revoke sessions while grace is pending.
No database credentials or new insecure account-recovery endpoints are added.
"""
from pathlib import Path

path = Path("internal/app/account/lifecycle.go")
src = path.read_text()

def replace_once(old, new, label):
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"Recoverable deletion patch: {label}, found {n}, expected 1")
    src = src.replace(old, new, 1)

replace_once(
    'const accountDeletionDelay = 7 * 24 * time.Hour',
    'const accountDeletionDelay = 30 * 24 * time.Hour',
    "30 day grace period",
)

replace_once(
'''// DeleteAccount implements the official 2FA deletion decision. A supplied and
// valid SRP proof always deletes immediately. Without a proof, an account whose
// password is older than seven days and which was active during the last seven
// days gets a cancellable seven-day window; all other cases delete immediately.''',
'''// DeleteAccount schedules a 30-day recoverable deletion for human accounts.
// The existing owner-authenticated tg://confirmphone cancellation flow is kept
// intact. Permanent deletion is only executed by the lifecycle worker AFTER
// the grace period; accidental confirmation never instantly destroys the user
// profile, messages or NFTs.''',
    "comment",
)
replace_once(
'''    if password != nil && !password.Empty {
        if !snapshot.HasPassword {
            return domain.AccountDeleteOutcome{}, domain.ErrPasswordHashInvalid
        }
        if err := s.CheckPassword(ctx, userID, *password); err != nil {
            return domain.AccountDeleteOutcome{}, err
        }
        return s.executeAccountDeletion(ctx, userID, deletionSourceForReason(reason), reason, now)
    }
    if !snapshot.HasPassword {
        return s.executeAccountDeletion(ctx, userID, deletionSourceForReason(reason), reason, now)
    }
    lastActive := snapshot.User.CreatedAt
    if snapshot.User.LastSeenAt > 0 {
        seen := time.Unix(int64(snapshot.User.LastSeenAt), 0).UTC()
        if seen.After(lastActive) {
            lastActive = seen
        }
    }
    passwordOldEnough := !snapshot.PasswordUpdatedAt.IsZero() && !snapshot.PasswordUpdatedAt.After(now.Add(-accountDeletionDelay))
    recentlyActive := !lastActive.IsZero() && !lastActive.Before(now.Add(-accountDeletionDelay))
    if !passwordOldEnough || !recentlyActive {
        return s.executeAccountDeletion(ctx, userID, deletionSourceForReason(reason), reason, now)
    }
    if snapshot.Pending != nil {
        return delayedDeleteOutcome(*snapshot.Pending, now), nil
    }
'''.replace("    ","\t"), 
'''    // Validate a supplied 2FA proof even though the deletion is delayed.
    // Accepting an invalid proof would turn an unauthorized confirmation into
    // a valid scheduled deletion.
    if password != nil && !password.Empty {
        if !snapshot.HasPassword {
            return domain.AccountDeleteOutcome{}, domain.ErrPasswordHashInvalid
        }
        if err := s.CheckPassword(ctx, userID, *password); err != nil {
            return domain.AccountDeleteOutcome{}, err
        }
    }
    if snapshot.Pending != nil {
        return delayedDeleteOutcome(*snapshot.Pending, now), nil
    }
'''.replace("    ","\t"),
    "all accounts delayed with proof validation",
)
replace_once(
    '"A request was made to delete your "+branding.ProductName()+" account. If this wasn\'t you, cancel the request: tg://confirmphone?phone=%s&hash=%s",',
    '"Your "+branding.ProductName()+" account is scheduled for deletion in 30 days. If you change your mind, restore it by cancelling here: tg://confirmphone?phone=%s&hash=%s",',
    "clear recovery link text",
)
path.write_text(src)

# The native account freeze worker normally treats freeze_until as a hard
# deletion deadline. Admin-recoverable removals use a separate durable 30-day
# bot receipt, which is the *only* allowed finalization authority. Excluding
# only our specific actor prefix prevents two independent deletion workers from
# racing and lets the client show the true 30-day freeze deadline. All
# moderation freezes keep upstream behavior unchanged.
store = Path("internal/store/postgres/account_lifecycle.go")
store_src = store.read_text()
old_due = """   WHERE r.frozen = true AND r.frozen_until IS NOT NULL AND r.frozen_until <= $1
     AND u.deleted_at IS NULL AND u.is_bot = false"""
new_due = """   WHERE r.frozen = true AND r.frozen_until IS NOT NULL AND r.frozen_until <= $1
     AND r.actor NOT LIKE 'risegram-admin-archive-%'
     AND u.deleted_at IS NULL AND u.is_bot = false"""
n = store_src.count(old_due)
if n != 1:
    raise SystemExit(f"Recoverable deletion patch: freeze worker exclusion, found {n}, expected 1")
store.write_text(store_src.replace(old_due, new_due, 1))

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

# Profile/full user has two code paths (cached and uncached). Both must return
# the synthetic frozen badge, without poisoning persistent verification cache.
userfull=Path("internal/rpc/users.go")
full_src=userfull.read_text()
old_mark='''		r.applyBotVerificationToUserFull(ctx, u.ID, &full)
		applyPrivateContactRestrictionToUserFull(&full, contactRestriction)'''
new_mark='''		r.applyBotVerificationToUserFull(ctx, u.ID, &full)
		applyFrozenUserFullMark(&full, u)
		applyPrivateContactRestrictionToUserFull(&full, contactRestriction)'''
if full_src.count(old_mark)!=1:
    raise SystemExit("Recoverable deletion patch: cached full frozen badge mismatch")
full_src=full_src.replace(old_mark,new_mark,1)
old_mark2='''	r.applyBotVerificationToUserFull(ctx, u.ID, &full)
	applyPrivateContactRestrictionToUserFull(&full, contactRestriction)'''
new_mark2='''	r.applyBotVerificationToUserFull(ctx, u.ID, &full)
	applyFrozenUserFullMark(&full, u)
	applyPrivateContactRestrictionToUserFull(&full, contactRestriction)'''
if full_src.count(old_mark2)!=1:
    raise SystemExit("Recoverable deletion patch: full frozen badge mismatch")
userfull.write_text(full_src.replace(old_mark2,new_mark2,1))

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

# Update pinned upstream decision tests to enforce safe defaults.
tests = Path("internal/app/account/lifecycle_test.go")
t = tests.read_text()
old='wantKind: domain.AccountDeleteImmediate'
if t.count(old) != 3:
    raise SystemExit("Recoverable deletion tests: unexpected immediate cases")
t=t.replace(old,'wantKind: domain.AccountDeleteDelayed')
t=t.replace('"no password deletes immediately"', '"no password has recoverable grace period"')
t=t.replace('"recent password change deletes immediately"', '"recent password change has grace period"')
t=t.replace('"inactive account deletes immediately"', '"inactive account has grace period"')
tests.write_text(t)
