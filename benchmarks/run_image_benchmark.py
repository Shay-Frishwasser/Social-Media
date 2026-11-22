from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch
from diffusers import Flux2KleinPipeline

from common import GENERATED_DIR, LOG_DIR, ResourceSampler, gpu_snapshot, write_json

parser = argparse.ArgumentParser()
parser.add_argument("--model", default="black-forest-labs/FLUX.2-klein-4B")
parser.add_argument("--revision", default="e7b7dc27f91deacad38e78976d1f2b499d76a294")
parser.add_argument("--width", type=int, default=768)
parser.add_argument("--height", type=int, default=768)
parser.add_argument("--steps", type=int, default=4)
args = parser.parse_args()

prompt = (
    "Premium editorial product photograph for a neighborhood coffee brand, warm Israeli morning light, "
    "ceramic coffee cup on pale limestone table, subtle olive branch shadow, cream and deep green palette, "
    "generous clean negative space on the upper right for later typography, photorealistic, no text, no letters, "
    "no logo, no watermark"
)
sampler = ResourceSampler().start()
before = gpu_snapshot()
load_started = time.time()
model_path = Path(args.model)
load_kwargs = {"torch_dtype": torch.bfloat16, "local_files_only": model_path.exists()}
if not model_path.exists():
    load_kwargs["revision"] = args.revision
pipe = Flux2KleinPipeline.from_pretrained(args.model, **load_kwargs)
pipe.enable_model_cpu_offload()
load_seconds = time.time() - load_started
generate_started = time.time()
generator = torch.Generator(device="cpu").manual_seed(20261122)
image = pipe(
    prompt=prompt, width=args.width, height=args.height, num_inference_steps=args.steps,
    guidance_scale=1.0, generator=generator,
).images[0]
generate_seconds = time.time() - generate_started
output_path = GENERATED_DIR / f"flux2-klein-{args.width}x{args.height}.png"
image.save(output_path)
resources = sampler.stop()
metrics = {
    "model": args.model, "revision": args.revision, "dtype": "bfloat16", "offload": "model_cpu_offload",
    "width": args.width, "height": args.height, "steps": args.steps, "seed": 20261122,
    "load_seconds": load_seconds, "generate_seconds": generate_seconds, "prompt": prompt,
    "output": str(output_path), "started_gpu": before, "ended_gpu_before_exit": gpu_snapshot(),
    "resources": resources,
}
metrics_path = LOG_DIR / "flux2-klein-benchmark.json"
write_json(metrics_path, metrics)
print(metrics_path)

