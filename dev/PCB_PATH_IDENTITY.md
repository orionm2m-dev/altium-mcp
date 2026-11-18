# Native PCB path identity

Altium AD26.9.1 was observed to load C:/... and C:\... as two different PCB objects backed by one file. This can cause divergent memory state and subsequent saves replacing one another. Opening and saving must not assume separator equivalence in the native GetDocumentByPath API.

The native helper now canonicalizes paths, enumerates loaded PCB documents, refuses duplicate normalized identities, and never falls back to an unrelated current PCB. Opening new PCB files uses canonical Windows separators. Saving resolves one unique loaded document. The outline tool reports a refused save as an error.

Verified 2026-11-10 through public MCP: the pre-existing duplicate pair is refused; a fresh file opened first through forward slashes then backslashes returns was_open=false followed by was_open=true with one canonical path. Duplicate save is refused (not_saved). These checks do not yet qualify outline persistence. Existing duplicate views are not automatically closed or discarded.

Qualification evidence is kept outside the repository. No Windows UI/focus handling. Prior files are retained.
