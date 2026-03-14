"""
Basic usage example for the generated ComfyUI workflow facade.

Run generate_api.py first to produce generated_client.py, then run:
    python examples/basic_usage.py
"""

from generated_client import ComfyWorkflowAPI, submit

COMFYUI_URL = "http://127.0.0.1:8188"

api = ComfyWorkflowAPI()

payload = api.build_payload(
    load_checkpoint_ckpt_name="SD1.5\\v1-5-pruned-emaonly.safetensors",
    clip_text_encode__prompt__text_6="beautiful scenery nature glass bottle landscape",
    clip_text_encode__prompt__text_7="text, watermark",
    empty_latent_image_width=512,
    empty_latent_image_height=512,
)
prompt_id = submit(payload, comfyui_url=COMFYUI_URL)
print("Queued prompt:", prompt_id)
