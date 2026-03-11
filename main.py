"""
Example usage of the generated ComfyUI workflow facade.

Run generate_api.py first to produce generated_client.py, then use this
script to build and submit a prompt payload.
"""

import requests
from generated_client import ComfyWorkflowAPI

COMFYUI_URL = "http://127.0.0.1:8188"

api = ComfyWorkflowAPI()

payload = api.build_payload(
    negative_prompt_value="text, watermark",
    positive_prompt_value="beautiful scenery nature glass bottle landscape",
    width_value=512,
    height_value=512,
)

response = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
print(response.status_code, response.json())