## Pre-Query Graph Hook

Before reading files or answering any question about the project codebase, structure, architecture, or concepts:

1. Check if `graphify-out/graph.json` exists
2. If yes, run `/graphify query "<question>" --budget 1500` to get the relevant subgraph
3. Use the graph output to identify the *specific* files and nodes needed — then read only those files, not a broad scan
4. If the graph doesn't have enough info, fall back to normal file search

This saves tokens by replacing broad file scans (~35k words) with targeted 1.5k-token graph queries.

## Guidelines

### Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If something is unclear, stop. Name what's confusing. Ask.

### Simplicity First
- Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked. No abstractions for single-use code.
- If you write 200 lines and it could be 50, rewrite it.

### Surgical Changes
- Touch only what you must. Don't "improve" adjacent code.
- Match existing style, even if you'd do it differently.
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

### Goal-Driven Execution
- Define verifiable success criteria before starting.
- For multi-step tasks, state a brief plan and verify each step.

## Code Search

Use `ast-grep` (via skill `sg`) instead of `grep` for all code search tasks:
- Finding function definitions, calls, imports
- Searching for patterns across the codebase
- Any task that `grep` would traditionally be used for

Run `sg -h` to see available commands. Pattern syntax: `$FUNCTION_CALL()` for function calls, `$CLASS` for class definitions, etc.
