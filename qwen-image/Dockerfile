FROM nvcr.io/nvidia/pytorch:26.05-py3

RUN python -m pip install --no-cache-dir \
    "git+https://github.com/huggingface/diffusers.git" \
    "transformers>=4.57" accelerate safetensors sentencepiece protobuf \
    fastapi "uvicorn[standard]" python-multipart pillow

WORKDIR /app
COPY app.py /app/app.py

ENV HF_HOME=/models/cache \
    PYTHONUNBUFFERED=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

EXPOSE 8188
CMD ["python", "/app/app.py"]
