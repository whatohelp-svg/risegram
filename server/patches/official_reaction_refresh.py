from pathlib import Path
p=Path("internal/app/files/seed.go")
s=p.read_text()
old='\t// reactions\n\tif n, err := s.media.CountAvailableReactions(ctx); err != nil {'
new='''\t// Reimport a validated official export even when a prior placeholder catalog exists.
\tforceOfficial := false
\tofficialVersion := ""
\tif raw, err := os.ReadFile(filepath.Join(root, "official-reactions.ready")); err == nil {
\t\tofficialVersion = strings.TrimSpace(string(raw))
\t\tif officialVersion != "" {
\t\t\tmatches, err := s.seedStateMatches(ctx, "risegram_official_reactions", officialVersion)
\t\t\tif err != nil { return stats, err }
\t\t\tforceOfficial = !matches
\t\t}
\t}
\tif forceOfficial {
\t\tif err := s.seedReactions(ctx, root, &stats); err != nil { return stats, fmt.Errorf("seed official reactions: %w", err) }
\t\tif stats.Reactions == 0 { return stats, fmt.Errorf("validated official reaction export is empty") }
\t\tif err := s.putSeedState(ctx, "risegram_official_reactions", officialVersion); err != nil { return stats, err }
\t} else if n, err := s.media.CountAvailableReactions(ctx); err != nil {'''
assert s.count(old)==1
p.write_text(s.replace(old,new,1))
