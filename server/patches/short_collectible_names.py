"""Allow collectible aliases 1–32 letters/digits/underscore (Latin or Russian).
Editable usernames retain their original validation. Applied to pinned upstream."""
from pathlib import Path
p=Path("internal/domain/collectible_username.go")
s=p.read_text()
old="MinCollectibleUsernameLength = 4"
assert s.count(old)==1
s=s.replace(old,"MinCollectibleUsernameLength = 1")
old="return validUsernameChars(username, MinCollectibleUsernameLength, MaxCollectibleUsernameLength)"
assert s.count(old)==1
s=s.replace(old,"""runes := []rune(username)
	if len(runes) < MinCollectibleUsernameLength || len(runes) > MaxCollectibleUsernameLength {
		return false
	}
	var latin, russian bool
	for i, c := range runes {
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z':
			latin = true
		case c >= 'а' && c <= 'я', c >= 'А' && c <= 'Я', c == 'ё', c == 'Ё':
			russian = true
		case c >= '0' && c <= '9', c == '_':
			if i == 0 { return false }
		default:
			return false
		}
	}
	// Prevent mixed-script lookalike aliases.
	return !(latin && russian)""")
p.write_text(s)
