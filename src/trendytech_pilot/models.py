"""Pinned model weights: changing a revision changes the extraction identity."""

LOCAL_REVISIONS = {
    "mlx-community/whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
    "mlx-community/Qwen2.5-7B-Instruct-4bit": "c26a38f6a37d0a51b4e9a1eb3026530fa35d9fed",
    "mlx-community/Qwen3-8B-4bit": "545dc4251c05440727734bcd94334791f6ab0192",
    "mlx-community/Qwen3.5-9B-4bit": "8b2b98c00a6b4d291155e4890773ca8f769aee53",
    "mlx-community/Qwen3.5-27B-4bit": "45797d2985a12c55e6473686e9ea91b95e959553",
    "mlx-community/Qwen3-14B-4bit": "a4d9b2df59d2c150bef02fcbe0d91046b7ca33a4",
}


def local_revision(model):
    if model not in LOCAL_REVISIONS:
        raise ValueError("Register the model's immutable Hugging Face revision before inference")
    return LOCAL_REVISIONS[model]


def local_model_path(model):
    """Use complete, immutable cached weights without a network metadata request."""
    import json
    from pathlib import Path

    from huggingface_hub import snapshot_download

    revision = local_revision(model)
    path = Path.home() / ".cache/huggingface/hub" / ("models--" + model.replace("/", "--")) / "snapshots" / revision
    required = ["config.json"]
    index = path / "model.safetensors.index.json"
    if "whisper" in model:
        required.append("weights.safetensors")
    elif index.exists():
        required.extend(set(json.loads(index.read_text())["weight_map"].values()))
    else:
        required.append("model.safetensors")
    if all((path / name).is_file() for name in required):
        return str(path)
    return snapshot_download(model, revision=revision)
