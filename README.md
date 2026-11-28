## Evaluation suite

The `app.evals` package runs a suite of ~30+ scripted cases against the
real pipeline. It is **offline-first**: a stub LLM replaces the provider,
and a recording `ToolManager` captures every tool call without executing
the tool body. That means the suite exercises routing, tool selection,
permission checks, loop guard, retry cap, and injection defense without
touching a real provider, real OS, or the user's real data.

### Run it

```powershell
py -m app.evals                    # full offline suite
py -m app.evals --fast             # 15 cases, quick smoke
py -m app.evals --category chat    # one category
py -m app.evals --threshold 0.9    # exit 1 below 90% pass
py -m app.evals --save-baseline    # snapshot baseline for diffing