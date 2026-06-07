# Agent Instructions

## Landing the Plane (Session Completion)

**When ending a work session**, complete the relevant steps below. Work is not complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File follow-up work** - Document anything that needs follow-up in the handoff or the project's current issue tracker if one is available.
2. **Run quality gates** - If code changed, run the relevant tests, linters, and builds.
3. **PUSH TO REMOTE** - This is mandatory:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
4. **Clean up** - Clear stashes and prune remote branches when applicable.
5. **Verify** - All changes committed and pushed.
6. **Hand off** - Provide context for the next session.

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds.
- NEVER stop before pushing; that leaves work stranded locally.
- NEVER say "ready to push when you are"; you must push.
- If push fails, resolve and retry until it succeeds.

## Quality Gates

Before ending sessions, run the relevant quality gates:

```bash
# Backend tests
cd backend && source venv/bin/activate && pytest

# Frontend tests (requires Node 18+ via NVM)
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd frontend && npm run test:run   # All tests once
cd frontend && npm run lint       # ESLint
```
