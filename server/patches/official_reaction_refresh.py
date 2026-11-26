"""Use the same versioned reaction seed path as patch_mtproto.py.

The preceding patch already installs the import and completion marker.
Do not patch the original upstream condition a second time.
"""
from pathlib import Path

p = Path("internal/app/files/seed.go")
s = p.read_text()
old = '\treactionSeedVersion := "risegram-telegram-assets-v1"'
new = '''\treactionSeedVersion := "risegram-telegram-assets-v1"
\tif raw, err := os.ReadFile(filepath.Join(root, "official-reactions.ready")); err == nil && len(raw) > 0 {
\t\treactionSeedVersion = "risegram-official-" + string(raw)
\t} else if raw, err := os.ReadFile(filepath.Join(root, "telegram-origin.ready")); err == nil && len(raw) > 0 {
\t\treactionSeedVersion = "risegram-telegram-origin-" + string(raw)
\t}'''
assert s.count(old) == 1, "Versioned reaction seed patch missing"
p.write_text(s.replace(old, new, 1))
