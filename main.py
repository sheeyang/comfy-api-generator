"""
Example usage of the generated ComfyUI workflow facade.

Run generate_api.py first to produce generated_client.py, then use this
script to build and submit a prompt payload.
"""

import requests
from generated_client import ComfyWorkflowAPI, Latent

COMFYUI_URL = "http://127.0.0.1:8188"

api = ComfyWorkflowAPI()

# IDE provides strict autocomplete and enforces correct NewTypes.
# The @validate_connections decorator ensures tuples are strictly (str, int)
# at runtime.
payload = api.build_payload(
    sampler_seed=42,
    sampler_steps=20,
    sampler_cfg=8,
    sampler_sampler_name="euler",
    sampler_scheduler="normal",
    sampler_denoise=1.0,
    sampler_model=("4", 0),
    sampler_positive=("6", 0),
    sampler_negative=("7", 0),
    sampler_latent_image=Latent(("5", 0)),
    prompt_text="beautiful scenery nature glass bottle landscape",
    negative_prompt_text="text, watermark",
)

# This would fail static type checking AND throw a ValueError at runtime:
# payload = api.build_payload(sampler_latent_image=["node_5", "index_0"], ...)

response = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
print(response.status_code, response.json())
