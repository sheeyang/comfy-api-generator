# comfy-api-generator

Generate a strongly-typed Python facade from a [ComfyUI](https://github.com/comfyanonymous/ComfyUI) `workflow_api.json` file.

Nodes whose `_meta.title` starts with `API_` become typed parameters on the generated `build_payload()` method, with full validation of enums, numeric ranges, and graph connection types at runtime.

## Install

```bash
pip install comfy-api-generator
```

## Quick start

Make sure ComfyUI is running, then point the generator at your exported `workflow_api.json`:

```bash
comfy-gen --workflow workflow_api.json --out generated_client.py
```

Use the generated facade:

```python
from generated_client import ComfyWorkflowAPI, submit, run

api = ComfyWorkflowAPI()

# Fire-and-forget
payload = api.build_payload(seed=42, clip_text_encode__prompt__text="a sunset")
prompt_id = submit(payload)
print("Queued:", prompt_id)

# Async progress streaming
import asyncio

async def main():
    async for event in run(payload):
        print(event)

asyncio.run(main())
```

## CLI options

```
comfy-gen --help

  --workflow PATH   workflow_api.json to read  (default: workflow_api.json)
  --out PATH        output file path           (default: generated_client.py)
  --url URL         ComfyUI base URL           (default: http://127.0.0.1:8188)
  --examples        also write basic_usage.py and track_progress.py
  --release         strip all validation, schema, and docstrings (lean output)
  --no-validate-enums
  --no-validate-ranges
  --no-validate-connections
  --no-schema
  --no-ranges
  --no-docstrings
  --no-http         omit submit() and run() from the generated file
```

## Install from GitHub

```bash
pip install "git+https://github.com/anomalyco/comfy-api-generator.git"
```

## License

MIT
