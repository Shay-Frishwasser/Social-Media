"""Isolated GPU subprocess entry point for the provisioned EC2 worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def flux(request: dict, destination: Path) -> None:
    import torch
    from diffusers import Flux2KleinPipeline

    target = Path(os.environ["BRANDAI_WORKER_ROOT"]) / "models/flux2-klein-4b"
    pipeline = Flux2KleinPipeline.from_pretrained(str(target), torch_dtype=torch.bfloat16, local_files_only=True)
    pipeline.enable_model_cpu_offload()
    width, height = request["size"]
    scale = min(1, 1024 / max(width, height))
    width, height = (max(256, 16 * round(dimension * scale / 16)) for dimension in (width, height))
    palette = ", ".join(request["palette"])
    prompt = (f"Text-free photographic background plate, {request['art_direction']}, composition {request['composition']}, "
              f"palette {palette}, blank unmarked surfaces, no lettering, logos, graphics or watermarks.")
    result = pipeline(
        prompt=prompt, width=width, height=height, num_inference_steps=4, guidance_scale=1.0,
        generator=torch.Generator(device="cpu").manual_seed(request["seed"]),
    ).images[0]
    result.save(destination, format="PNG")


def sam2(source: Path, variant: str, destination: Path) -> None:
    import numpy as np
    import torch
    from PIL import Image
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    variants = {"sam2.1_hiera_tiny": "t", "sam2.1_hiera_small": "s"}
    checkpoint = Path(os.environ["BRANDAI_WORKER_ROOT"]) / "models/sam2" / f"{variant}.pt"
    config = f"configs/sam2.1/sam2.1_hiera_{variants[variant]}.yaml"
    predictor = SAM2ImagePredictor(build_sam2(config, str(checkpoint), device="cuda"))
    with Image.open(source) as photo:
        pixels = photo.convert("RGBA")
    width, height = pixels.size
    box = np.array([.09 * width, .09 * height, .91 * width, .91 * height], dtype=np.float32)
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        predictor.set_image(np.asarray(pixels.convert("RGB")))
        masks, confidence, _ = predictor.predict(
            point_coords=np.array([[width / 2, height / 2]], dtype=np.float32),
            point_labels=np.array([1], dtype=np.int32), box=box, multimask_output=True,
        )
    candidates = [index for index, mask in enumerate(masks) if .02 < mask.mean() < .97]
    if not candidates:
        raise RuntimeError("no usable foreground mask")
    best = max(candidates, key=lambda index: confidence[index])
    pixels.putalpha(Image.fromarray(masks[best].astype("uint8") * 255))
    pixels.save(destination, format="PNG")


def main() -> None:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("capability", choices=("flux", "sam2"))
    arguments.add_argument("source", type=Path)
    arguments.add_argument("output", type=Path)
    arguments.add_argument("--variant", choices=("sam2.1_hiera_tiny", "sam2.1_hiera_small"))
    values = arguments.parse_args()
    if values.capability == "flux":
        flux(json.loads(values.source.read_text(encoding="utf-8")), values.output)
    else:
        if values.variant is None:
            arguments.error("SAM 2 requires a pinned variant")
        sam2(values.source, values.variant, values.output)


if __name__ == "__main__":
    main()
