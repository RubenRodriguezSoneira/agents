# agents

## Scatter/Gather PoC in Python

The C# workflow in `ScatterGatherPoc/Program.cs` has an equivalent Python migration in:

- `ScatterGatherPoc/scatter_gather_poc.py`

It preserves the same flow:

1. Create three specialist reviewers (async, memory, parallel).
2. Run them concurrently over the same C# snippet.
3. Aggregate feedback.
4. Send original code + feedback to a refactor agent.
5. Print the final refactored C# output.

### Run

From `ScatterGatherPoc/`:

```powershell
python -m pip install -r requirements.txt
python scatter_gather_poc.py
```

Required environment variables:

- `GITHUB_TOKEN` (required)
- `GITHUB_MODEL` (optional, default: `gpt-4.1`)