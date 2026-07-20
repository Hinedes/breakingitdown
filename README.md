# Breaking It Down (BID)

Local-first agent harness for running long-horizon tasks on small edge language models through sequential, disposable model contexts.

## Quick start (with mock backend)

```bash
pip install httpx pytest

# Initialize a project with mock backend (no model needed)
BID_BACKEND=mock python bid.py init "Research three local inference backends suitable for SmolLM3-3B, compare them, and produce a recommendation in docs/final.md."

# Run the project
BID_BACKEND=mock python bid.py run
```

## Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `BID_MODEL_ENDPOINT` | `http://127.0.0.1:8080/v1/chat/completions` | llama.cpp endpoint |
| `BID_MODEL_NAME` | `smollm3-3b` | Model identifier |
| `BID_MAX_AGENT_TURNS` | `50` | Max turns per agent session |
| `BID_MAX_TOKENS` | `512` | Max tokens per model response |
| `BID_WORKSPACE` | `./workspace` | Project workspace path |
| `BID_REQUEST_TIMEOUT` | `120` | HTTP request timeout in seconds |
| `BID_BACKEND` | (unset) | Set to `mock` for testing |
| `BID_TEXT_TOOLS` | `1` | Use text-based tool calling |

## CLI commands

```bash
python bid.py init "TASK"    # Initialize project
python bid.py run             # Run or continue project
python bid.py status          # Show current state
python bid.py resume          # Alias for run
python bid.py vc log          # Show VC history
python bid.py vc rollback sN  # Restore state
```

## Architecture

- **Manager**: Breaks tasks into numbered TODO items, reviews Worker output, declares completion.
- **Worker N**: Reads TODO, performs Task TN, checks its box, terminates.
- **Harness**: Sequential loop — Manager init → Worker 1...N → Manager review → repeat until DONE.
- **VC**: Simple linear snapshot system in `.bid/`. Each agent finish creates a named state.

## Running with SmolLM3-3B

1. Start llama.cpp server:
   ```bash
   ./llama-server -m smollm3-3b-q4_k_m.gguf --port 8080
   ```
2. Set `BID_MODEL_ENDPOINT` if different from default.
3. Run `python bid.py init "your task"` and `python bid.py run`.

Requires a machine fast enough to run a 3B model (GPU or fast CPU). On ARM Cortex-A76 at 1.5 tok/s, a single agent turn takes 5-10 minutes.

## Experimental findings (SmolLM3-3B on ARM64)

Live testing against SmolLM3-3B (Q4_K_M) on a 4-core ARM Cortex-A76 revealed:

- **OpenAI-format `tools` parameter ignored** by SmolLM3. The model does not output native tool calls in OpenAI format. Fixed via `BID_TEXT_TOOLS=1` (default), which embeds tool descriptions in the system prompt as text and parses JSON from the model's text output.
- **Reasoning mode eats token budget.** With prompts >~200 system tokens, SmolLM3 enters a reasoning mode that fills `reasoning_content` and produces no `content`. Mitigated by keeping prompts short and `BID_MAX_TOKENS=512`.
- **Multi-JSON output.** The model outputs sequential JSON objects on separate lines for multiple tool calls in one turn. The parser handles this.
- **Inference speed.** ~1.5 tok/s on ARM Cortex-A76. A full Manager→Workers→Manager cycle would take hours. Use hardware with GPU or fast CPU for real use.

## Test suite

38 tests cover all harness mechanics without a real model, using `MockBackend`:

- Full Manager→3 Workers→Manager review→DONE cycle
- Permission enforcement (Worker blocked from task.md/project-status.md)
- Worker can only check own task
- Manager blocked from writing Worker artifacts
- VC restore after crash, rollback cleanup
- Manager reopen/recheck cycle
- Context isolation between agents

```bash
cd BreakItDown
python -m pytest tests/ -v
```

## Project tree

```
BreakItDown/
├── bid.py                  # CLI entry point
├── bid/
│   ├── __init__.py
│   ├── cli.py              # CLI dispatch
│   ├── harness.py           # Control loop
│   ├── model.py             # Backends (LlamaCpp, Mock)
│   ├── permissions.py       # Path safety, role enforcement
│   ├── session.py           # Agent session loop
│   ├── todo.py              # Markdown TODO parser
│   ├── tools.py             # File tools with role validation
│   └── vc.py                # Linear snapshot VC
├── prompts/
│   ├── manager.md
│   └── worker.md
├── tests/                   # 38 tests
├── pyproject.toml
└── README.md
```
