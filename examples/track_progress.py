"""
Progress-tracking usage example for the generated ComfyUI workflow facade.

Requires: pip install websocket-client

Connects to ComfyUI's WebSocket before submitting the prompt so that
step-level sampler progress and per-node execution events are streamed
in real time.

Run generate_api.py first to produce generated_client.py, then run:
    python examples/track_progress.py
"""

import json
import uuid
import requests
import websocket  # pip install websocket-client
from generated_client import ComfyWorkflowAPI

COMFYUI_URL = "http://127.0.0.1:8188"
WS_URL      = "ws://127.0.0.1:8188/ws"

client_id = str(uuid.uuid4())
api = ComfyWorkflowAPI()

payload = api.build_payload(
    load_checkpoint_ckpt_name="SD1.5\\v1-5-pruned-emaonly.safetensors",
    positive_prompt_value="beautiful scenery nature glass bottle landscape",
    negative_prompt_value="text, watermark",
    width_value=512,
    height_value=512,
)
# Attach client_id so ComfyUI routes events back to our WebSocket
payload["client_id"] = client_id

# Connect first, then submit
ws = websocket.WebSocket()
ws.connect(f"{WS_URL}?clientId={client_id}")

response = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
prompt_id = response.json()["prompt_id"]
print(f"Submitted prompt {prompt_id}")

while True:
    raw = ws.recv()
    msg = json.loads(raw)
    mtype = msg.get("type")
    data  = msg.get("data", {})

    if mtype == "executing":
        print(f"  Running node: {data.get('node')}")

    elif mtype == "progress":
        pct = int(data["value"] / data["max"] * 100)
        print(f"  Sampling: {data['value']}/{data['max']}  ({pct}%)")

    elif mtype == "execution_success":
        print("Done!")
        break

    elif mtype == "execution_error":
        print(f"Error: {data}")
        break

ws.close()