import asyncio
import base64
import gc
import io
import os
import time
import uuid
from pathlib import Path

import torch
from diffusers import QwenImageEditPlusPipeline, QwenImagePipeline
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image

T2I_MODEL = os.getenv("T2I_MODEL", "Qwen/Qwen-Image-2512")
I2I_MODEL = os.getenv("I2I_MODEL", "Qwen/Qwen-Image-Edit-2511")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/outputs"))
API_KEY = os.getenv("IMAGE_API_KEY")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Qwen Image API", version="1.0")
lock = asyncio.Lock()
pipeline = None
pipeline_kind = None


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    supplied = request.headers.get("authorization", "")
    bearer = supplied[7:] if supplied.lower().startswith("bearer ") else ""
    key = request.headers.get("x-api-key") or bearer or request.query_params.get("api_key")
    if not API_KEY or key != API_KEY:
        return JSONResponse({"detail": "invalid API key"}, status_code=401)
    return await call_next(request)


class GenerationRequest(BaseModel):
    prompt: str
    model: str = "qwen-image-2512"
    n: int = Field(1, ge=1, le=1)
    size: str = "1024x1024"
    response_format: str = "url"
    seed: int | None = None
    steps: int = Field(30, ge=1, le=50)
    guidance_scale: float = Field(4.0, ge=0, le=10)
    negative_prompt: str = ""


def parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except Exception as exc:
        raise HTTPException(400, "size must look like 1024x1024") from exc
    if width < 256 or height < 256 or width > 2048 or height > 2048:
        raise HTTPException(400, "width and height must be between 256 and 2048")
    return width, height


def unload_pipeline() -> None:
    global pipeline, pipeline_kind
    pipeline = None
    pipeline_kind = None
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()


def get_pipeline(kind: str):
    global pipeline, pipeline_kind
    if pipeline is not None and pipeline_kind == kind:
        return pipeline
    unload_pipeline()
    if kind == "generate":
        pipeline = QwenImagePipeline.from_pretrained(T2I_MODEL, torch_dtype=torch.bfloat16)
    else:
        pipeline = QwenImageEditPlusPipeline.from_pretrained(I2I_MODEL, torch_dtype=torch.bfloat16)
    pipeline.to("cuda")
    pipeline.set_progress_bar_config(disable=False)
    pipeline_kind = kind
    return pipeline


def save_result(image: Image.Image, request: Request, response_format: str):
    name = f"{int(time.time())}-{uuid.uuid4().hex}.png"
    path = OUTPUT_DIR / name
    image.save(path, format="PNG")
    if response_format == "b64_json":
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return {"b64_json": base64.b64encode(buffer.getvalue()).decode("ascii")}
    return {"url": str(request.base_url).rstrip("/") + f"/outputs/{name}?api_key={API_KEY}"}


@app.get("/health")
def health():
    return {"status": "ok", "loaded_pipeline": pipeline_kind, "cuda": torch.cuda.is_available()}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [
        {"id": "qwen-image-2512", "object": "model", "owned_by": "Qwen"},
        {"id": "qwen-image-edit-2511", "object": "model", "owned_by": "Qwen"},
    ]}


@app.get("/outputs/{name}")
def output(name: str):
    path = OUTPUT_DIR / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(path, media_type="image/png")


@app.post("/v1/images/generations")
async def generate(body: GenerationRequest, request: Request):
    width, height = parse_size(body.size)
    seed = body.seed if body.seed is not None else int.from_bytes(os.urandom(4), "big")
    async with lock:
        pipe = await asyncio.to_thread(get_pipeline, "generate")
        def run():
            with torch.inference_mode():
                return pipe(
                    prompt=body.prompt, negative_prompt=body.negative_prompt,
                    width=width, height=height, num_inference_steps=body.steps,
                    true_cfg_scale=body.guidance_scale,
                    generator=torch.Generator(device="cuda").manual_seed(seed),
                ).images[0]
        image = await asyncio.to_thread(run)
    return {"created": int(time.time()), "data": [save_result(image, request, body.response_format)], "seed": seed}


@app.post("/v1/images/edits")
async def edit(
    request: Request,
    image: list[UploadFile] = File(...),
    prompt: str = Form(...),
    model: str = Form("qwen-image-edit-2511"),
    size: str = Form("1024x1024"),
    response_format: str = Form("url"),
    seed: int | None = Form(None),
    steps: int = Form(30),
    guidance_scale: float = Form(4.0),
    negative_prompt: str = Form(" "),
):
    width, height = parse_size(size)
    if not 1 <= len(image) <= 3:
        raise HTTPException(400, "upload between one and three reference images")
    inputs = []
    for upload in image:
        try:
            inputs.append(Image.open(io.BytesIO(await upload.read())).convert("RGB"))
        except Exception as exc:
            raise HTTPException(400, f"invalid image: {upload.filename}") from exc
    seed = seed if seed is not None else int.from_bytes(os.urandom(4), "big")
    async with lock:
        pipe = await asyncio.to_thread(get_pipeline, "edit")
        def run():
            with torch.inference_mode():
                result = pipe(
                    image=inputs, prompt=prompt, negative_prompt=negative_prompt,
                    num_inference_steps=max(1, min(steps, 50)),
                    true_cfg_scale=guidance_scale, guidance_scale=1.0,
                    generator=torch.Generator(device="cuda").manual_seed(seed),
                ).images[0]
                return result.resize((width, height), Image.Resampling.LANCZOS) if result.size != (width, height) else result
        result = await asyncio.to_thread(run)
    return {"created": int(time.time()), "data": [save_result(result, request, response_format)], "seed": seed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8188)
