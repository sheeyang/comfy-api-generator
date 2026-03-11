"""
Basic usage example for the generated ComfyUI workflow facade.

Run generate_api.py first to produce generated_client.py, then run:
    python examples/basic_usage.py
"""

import requests
from generated_client import ComfyWorkflowAPI

COMFYUI_URL = "http://127.0.0.1:8188"

api = ComfyWorkflowAPI()

payload = api.build_payload(
    load_checkpoint_ckpt_name="SD1.5\\v1-5-pruned-emaonly.safetensors",
    clip_text_encode__positive__text="beautiful scenery nature glass bottle landscape",
    clip_text_encode__negative__text="text, watermark",
    empty_latent_image_width=512,
    empty_latent_image_height=512,
)

response = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
print(response.status_code, response.json())