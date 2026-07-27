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
    target_modules: list = field(default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj"])

    batch_size: int = 2
    grad_accum_steps: int = 4
    max_steps: int = 300
    learning_rate: float = 2e-4
    warmup_steps: int = 50
    logging_steps: int = 10
    save_steps: int = 200
    max_seq_length: int = 512

    use_quantization: bool = False
    use_cpu: bool = True
    use_fp16: bool = False
    use_bf16: bool = False

    max_train_samples: Optional[int] = 5000
    max_val_samples: Optional[int] = 200
