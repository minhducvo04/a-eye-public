# Contributing

1. Pick your package (see AGENTS.md module map and README team table). One owner per package.
2. Make a worktree: `~/Projects/calhacks-kit/wt.sh <package>` (or `git worktree add -b pkg/<package> ../a-eye-wt/<package>`).
3. Build against `contracts/schema.json`. Validate your messages with `contracts.validate()` in Python tests.
4. Add tests next to your code. Run `make test` before you push.
5. Write `RESULT.md` in your worktree: files changed, limits, `Test: <command> - PASS/FAIL (<result>)`.
6. Push `pkg/<package>` and open a PR to `main`, or ask the integrator (the team) to merge locally.
7. The integrator reads the diff (files outside your package, secrets, clip paths, contract drift), merges,
   then runs `make test` and `make smoke` on `main`.

Contract changes: a separate PR that touches only `contracts/`, updates `schema.json`, `types.ts` and
`examples.json` together, and says who consumes the changed message.

No GitHub Actions. Tests run locally.
