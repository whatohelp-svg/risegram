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
