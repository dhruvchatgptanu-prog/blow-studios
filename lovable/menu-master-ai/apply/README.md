# Apply bundles

Three messages that bring the Lovable project from its current state (round 2 plus the
partial round 3+4 edits) to the GitHub state. Send message 1, wait for it to finish, then
message 2, then message 3. Each is under Lovable's 100k-character message limit. The build
is expected to fail between message 1 and message 3; the Lovable agent is told not to
"fix" it in between.

Free alternative: connect the Lovable project to GitHub (Settings → GitHub) and copy the
`src/` tree from `lovable/menu-master-ai/src/` into that repository; Lovable syncs it.

Keep the bundles current: `node apply/regenerate.mjs` (from `lovable/menu-master-ai`) rewrites
every file block from the repo files and prints each message's size against the 100k limit.

## Preferred: fetch from GitHub (round 6 onwards)
The repository is public, so the Lovable agent can download every file itself:
send it the contents of `apply/fetch-from-github.sh` (pin `HALFTONE_SHA` to the commit you
want) and ask it to run the script, then `bunx tsgo --noEmit`. The agent emits only the script,
so the whole upgrade costs about one credit instead of twenty. The three message bundles above
remain as a fallback for a private repository.
