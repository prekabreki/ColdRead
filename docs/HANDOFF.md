# ColdRead — Session Handoff (2026-07-23)

**Next session focus:** run `/foreman-dispatch-waves` against the wave plan below,
then (separately) the deferred visual `/gui-audit`. This doc is the dispatch brief;
it deliberately does **not** repeat content that lives in artifacts — read those:

- Direction & the two epics: `docs/PLAN.md`
- Full repo audit (findings, code-judo, remediation order): `AUDIT.md`
- Foreman config: `.foreman.local` (gitignored), CLAUDE.md `foreman:` block
- Issues: https://github.com/prekabreki/ColdRead/issues

## What happened this session

1. **Grilled the direction** → ColdRead is the maintainer's **daily-driver** tool
   for **two YouTube channels** (solo, performs all characters → color-coding is a
   voice-switch cue), read off an **iPad in the booth**. Two epics captured in
   `docs/PLAN.md`: **#3** meaning-first breath breaker, **#4** iPad HTML scroll
   read-view (file export + publish-to-URL; #4 blocked by #3). Everything else in
   `docs/ColdRead-roadmap.md` was deliberately cut for this use.
2. **Foreman onboarded** (`foreman-init`): `.foreman.local`, 6 labels, guardrail
   `.foreman/foreman.py`, CLAUDE.md block, excludes.
3. **`/full-repo-audit`** run as 6 parallel deep-dives → `AUDIT.md` + **76 issues**
   (#5–#80). **`/gui-audit` deferred** (needs live screen + screenshots) → tracked
   as **#81**.
4. **Promoted 54 issues to `ready-for-agent`**; held 24 as `scoped` + 3 `needs-human`.

## ⚠ Blockers before dispatch

- **`DEEPSEEK_API_KEY` is NOT in the shell profile.** It was set only in an
  interactive terminal; a fresh Bash shell (and foreman dispatch) can't see it.
  **Fix first:** add `export DEEPSEEK_API_KEY=...` to `~/.bashrc` (or `~/.zshrc`),
  then confirm `[ -n "$DEEPSEEK_API_KEY" ]` in a new shell. Dispatch will fail
  without this.
- ~~`FOREMAN_EXECUTOR_CMD_PRO` is empty~~ **DONE** — set to
  `opencode run --model deepseek/deepseek-v4-pro` in `.foreman.local`, and the 21
  held pro-tier issues (#3, #6, #7, #16, #23, #24, #27, #29, #30, #31, #37–#44,
  #53, #54, #66) are labeled `exec:pro`. They remain `scoped` — promote to
  `ready-for-agent` at Phase 2 dispatch time.
- **`AUDIT.md` and `docs/PLAN.md` are untracked / uncommitted**, as are the CLAUDE.md
  edits and `.foreman/`. Decide whether to commit AUDIT.md + PLAN.md before the
  crew starts opening PRs against `main`.
- **Worktree isolation:** flat-layout editable install; dispatch guards executors
  via `PYTHONPATH` (`FOREMAN_SRC_SUBDIR="."`), but local `.claude/worktrees`
  subagents are not guarded (noted, unfixed).

## Wave plan for `/foreman-dispatch-waves`

**The governing rule: file ownership.** Cheap executors run in parallel and each
opens a PR on `foreman/issue-<N>`; two PRs touching the **same file** conflict at
merge. So same-file issues go in **successive** waves, never the same one. Waves
run sequentially; within a wave, `FOREMAN_MAX_PARALLEL=5` queues the rest.

### Phase 1 — cheap executor sweep (the 54 `ready-for-agent` issues)

Each file below is a **serial chain**; wave *N* dispatches the *N*-th issue of
every chain at once (all different files → no conflict). Max chain length = 8, so
8 waves. Chains (in intended order):

| File | Issue chain (→ = later waves) |
|---|---|
| `preflight.py` | 5 → 10 → 11 |
| `claude_code_backend.py` | 8 → 9 |
| `backend.py` | 12 |
| `formatter.py` | 13 → 14 → 15 → 18 → 19 → 20 → 21 → 22 |
| `cli.py` | 25 → 26 → 28 → 33 → 35 |
| `pdf_writer.py` | 32 → 34 → 36 |
| `gui.py` | 45 → 46 → 47 → 48 → 49 → 50 → 51 → 52 |
| `cold_read.py` | 58 → 61 → 63 → 62 |
| `toggles.py` | 59 → 60 → 64 → 65 |
| `colors.py` | 56 → 57 |
| `parser.py` | 55 |
| `pyproject.toml` | 74 → 75 |
| `CLAUDE.md` | 77 → 78 |
| `.github/workflows/` | 67 |
| `ColdRead.spec` | 76 |
| `README.md` | 79 |
| tests (distinct files, parallel) | 69, 70, 71, 72, 73, 80 |

- **Wave 1** = fronts: 5, 8, 12, 13, 25, 32, 45, 58, 59, 56, 55, 74, 77, 67, 76, 79
  + test issues 69/70/72/73 (new files). *(≈20 issues, runs 5 at a time.)*
- **Wave 2** = 10, 9, 14, 26, 34, 46, 61, 60, 57, 75, 78, plus 71/80 (edit existing
  test files — dispatch **after** the source fix they cover has merged).
- **Waves 3–8** = walk each chain to exhaustion (formatter.py and gui.py are the
  8-deep tail).
- **Sequencing caveat:** the new-test issues that pin *current* behavior (69
  cold_read, 71 formatter) are best dispatched **after** that file's fixes land, so
  they test fixed behavior — not before.

### Phase 2 — pro executor / by hand (the 24 held `scoped`), serial, one at a time

Do these **after** Phase 1 on the same file, or first if you'd rather refactor then
patch. Recommended order:

1. **GUI threading/safety (High, danger-zone) — do NOT give to flash:** #37 (teardown),
   #39 (`after` guard), #40 (cache race), #38 (preview fires paid API), #41 (cancel),
   #42/#43/#44. These are freeze/data/cost bugs; pro executor with independent
   verification, or by hand.
2. **Pipeline seam #27** (run_pipeline + default_preflight) — unblocks GUI dedup and
   fixes batch-pronunciation drift; then #29 (main() split), #30+#31 (RENDER-OP,
   which also **de-risks read-view epic #4**).
3. **Backend refactor** #6, #7. **Formatter refactor** #23 (absorbs the dup evidence)
   then #24. **GUI decomposition** #53. **Cross-file** #54 (geometry), #66 (hygiene).
4. **Coupled** #16 (do with/after #23).

### Phase 3 — the epics

- **#3 breaker** — sizable; pro executor. It likely subsumes #61 (PIPE-8) and #63
  (PIPE-10); if you dispatch #3 first, drop those from the cold_read chain.
- **#4 read-view** — an epic; **decompose into its 6 sub-issues first** (listed in
  the issue body) via `gh-issues-writing`, then dispatch. Blocked by #3; wants
  RENDER-OP (#31) landed first.

### Never auto-dispatch (leave as-is)

- `needs-human`: **#17** (data-loss behavior decision), **#68** (AGPL/MIT license
  decision), **#81** (visual gui-audit — needs your screen).

## Suggested skills for the next session

- **`foreman-dispatch-waves`** — execute Phase 1 (and Phase 2 if a pro executor is
  configured) using the chains above.
- **`foreman-status`** — run at session open (a wave will be in flight); review REAL
  diffs, merge, bounce, escalate. Executors never merge; you gate on the diff.
- **`gh-issues-writing`** — to decompose epic **#4** into its sub-issues before
  dispatching the read-view.
- **`gui-audit`** — the deferred visual pass (#81) when a screen is free.
- **`grill-me` / `writing-plans`** — if scoping the #4 read-view build in depth.

## Redaction note

No secrets are stored in-repo. `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` live only in
the environment (see `.foreman.local` comment). Nothing in this doc contains keys.
