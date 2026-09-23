from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrainerConfig:
    base_model: str = "Qwen/Qwen2.5-0.5B"
    output_dir: str = "trained_persona_model"
    dataset_cache_dir: str = "dataset_cache"

    lora_r: int = 8
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: list = field(
        default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj"]
    )

    # ─── Unsloth (GPU) training ─────────────────────────────────────────
    # Unsloth gives ~2x faster LoRA/QLoRA with ~70% less VRAM. It is used
    # automatically whenever CUDA is available (and use_cpu is off); on CPU
    # we fall back to the classic PEFT path.
    use_unsloth: bool = True
    load_in_4bit: bool = True  # QLoRA — 4-bit quantized base model
    unsloth_target_modules: list = field(
        default_factory=lambda: [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ]
    )
    save_gguf: bool = False  # also export merged model as GGUF (llama.cpp/Ollama)
    gguf_quant: str = "q8_0"  # q8_0, q4_k_m, q4_k_s, q5_k_m, f16, ...

    batch_size: int = 2
    grad_accum_steps: int = 4
    max_steps: int = 300
    learning_rate: float = 2e-4
    lr_scheduler_type: str = "cosine"
    warmup_steps: int = 50
    logging_steps: int = 10
    save_steps: int = 200
    max_seq_length: int = 512

    # Early stopping / best-checkpoint selection. Requires eval + save on the
    # same cadence, which train.py enforces.
    early_stopping_patience: int = 3
    early_stopping_threshold: float = 0.0
    eval_steps: Optional[int] = None
    load_best_model_at_end: bool = True

    use_quantization: bool = False
    use_cpu: bool = True
    use_fp16: bool = False
    use_bf16: bool = False

    max_train_samples: Optional[int] = 5000
    max_val_samples: Optional[int] = 200
