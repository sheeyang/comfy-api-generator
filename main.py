"""
Example usage of the generated ComfyUI workflow facade.

Run generate_api.py first to produce generated_client.py, then use this
script to build and submit a prompt payload.
"""

import requests
from generated_client import ComfyWorkflowAPI, Clip, Conditioning, Latent, Model

COMFYUI_URL = "http://127.0.0.1:8188"

api = ComfyWorkflowAPI()

payload = api.build_payload(
    sampler_seed=42,
    sampler_steps=20,
    sampler_cfg=8,
    sampler_sampler_name="euler",
    sampler_scheduler="normal",
    sampler_denoise=1.0,
    sampler_model=Model(("4", 0)),
    sampler_positive=Conditioning(("6", 0)),
    sampler_negative=Conditioning(("7", 0)),
    sampler_latent_image=Latent(("5", 0)),
    positive_prompt_text="beautiful scenery nature glass bottle landscape",
    positive_prompt_clip=Clip(("4", 1)),
    negative_prompt_text="text, watermark",
    negative_prompt_clip=Clip(("4", 1)),
)

response = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
print(response.status_code, response.json())
