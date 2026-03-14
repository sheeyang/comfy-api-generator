"""
Progress-tracking usage example for the generated ComfyUI workflow facade.

Requires: pip install aiohttp

Uses the async run() generator to stream progress events in real time.
Suitable for piping into SSE or any streaming response in your API server.

Run generate_api.py first to produce generated_client.py, then run:
    python examples/track_progress.py
"""

import asyncio
from generated_client import ComfyWorkflowAPI

COMFYUI_URL = "http://127.0.0.1:8188"


async def main():
    api = ComfyWorkflowAPI()

    async for event in api.run(
        comfyui_url=COMFYUI_URL,
        load_checkpoint_ckpt_name="SD1.5\\v1-5-pruned-emaonly.safetensors",
        clip_text_encode__prompt__text_6="beautiful scenery nature glass bottle landscape",
        clip_text_encode__prompt__text_7="text, watermark",
        empty_latent_image_width=512,
        empty_latent_image_height=512,
    ):
        if event["type"] == "queued":
            print(f"Submitted prompt {event['prompt_id']}")

        elif event["type"] == "executing":
            print(f"  Running node: {event['node_id']}")

        elif event["type"] == "progress":
            print(f"  Sampling: {event['step']}/{event['max']}  ({event['pct']}%)")

        elif event["type"] == "execution_success":
            print("Done!")

        elif event["type"] == "execution_error":
            print(f"Error: {event['detail']}")


asyncio.run(main())
