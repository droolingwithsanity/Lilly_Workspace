import logging
import math
import os
import sys
from pathlib import Path
from typing import Optional

import torch
from datasets import Dataset
from transformers import EarlyStoppingCallback, set_seed

from .config import TrainerConfig

logger = logging.getLogger("PersonaTrainer.Train")


def _eval_and_save_kwargs(config: TrainerConfig) -> dict:
    """Eval + save on the SAME cadence (required for best-model selection)."""
    eval_steps = config.eval_steps or config.save_steps
    return {
        "lr_scheduler_type": config.lr_scheduler_type,
        "save_strategy": "steps",
        "eval_steps": eval_steps,
        "save_steps": eval_steps,
    }


def _best_model_kwargs(config: TrainerConfig) -> dict:
    if not config.load_best_model_at_end:
        return {}
    return {
        "load_best_model_at_end": True,
        "metric_for_best_model": "eval_loss",
        "greater_is_better": False,
    }


def _callbacks(config: TrainerConfig) -> list:
    patience = int(config.early_stopping_patience or 0)
    if patience <= 0:
        return []
    return [
        EarlyStoppingCallback(
            early_stopping_patience=patience,
            early_stopping_threshold=config.early_stopping_threshold,
        )
    ]


def _use_unsloth(config: TrainerConfig) -> bool:
    """Unsloth needs CUDA; on CPU we fall back to the classic PEFT path."""
    return bool(config.use_unsloth and not config.use_cpu and torch.cuda.is_available())


def train(
    dataset: Dataset,
    config: TrainerConfig,
    resume_from: Optional[str] = None,
):
    """Train a persona LoRA.

    Picks the backend automatically:
      * Unsloth (GPU)  — FastLanguageModel + UnslothTrainer (~2x faster,
        ~70% less VRAM, native GGUF export for llama.cpp/Ollama).
      * PEFT (CPU)     — classic HF Trainer fallback for GPU-less machines.
    """
    if _use_unsloth(config):
        return _train_unsloth(dataset, config, resume_from)
    return _train_peft(dataset, config, resume_from)


# ─── Unsloth backend (GPU) ──────────────────────────────────────────────
def _tokenize_dataset(dataset, tokenizer, max_length: int):
    """Pre-tokenize a raw text dataset (input_ids + labels = input_ids)."""
    return dataset.map(
        lambda x: _tokenize_function(x, tokenizer, max_length),
        batched=True,
        remove_columns=dataset.column_names,
        desc="Tokenizing",
    )


def _split_and_sample(dataset, config):
    split_ds = dataset.train_test_split(test_size=0.1, seed=42)
    train_ds = split_ds["train"]
    eval_ds = split_ds["test"]
    if config.max_train_samples and len(train_ds) > config.max_train_samples:
        train_ds = train_ds.select(range(config.max_train_samples))
    if config.max_val_samples and len(eval_ds) > config.max_val_samples:
        eval_ds = eval_ds.select(range(config.max_val_samples))
    logger.info(
        f"[Unsloth] Train samples: {len(train_ds)}, Eval samples: {len(eval_ds)}"
    )
    return train_ds, eval_ds


def _train_unsloth(
    dataset: Dataset,
    config: TrainerConfig,
    resume_from: Optional[str] = None,
):
    import inspect

    from unsloth import (
        FastLanguageModel,
        is_bfloat16_supported,
        UnslothTrainer,
        UnslothTrainingArguments,
    )

    set_seed(42)
    logger.info(f"[Unsloth] Loading base model: {config.base_model}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=config.base_model,
        max_seq_length=config.max_seq_length,
        dtype=None,  # auto: bf16 when supported, else fp16
        load_in_4bit=config.load_in_4bit,  # QLoRA
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=config.unsloth_target_modules or config.target_modules,
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    model.print_trainable_parameters()

    train_ds, eval_ds = _split_and_sample(dataset, config)

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    args = UnslothTrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=1,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        gradient_accumulation_steps=config.grad_accum_steps,
        max_steps=config.max_steps,
        learning_rate=config.learning_rate,
        warmup_steps=config.warmup_steps,
        logging_steps=config.logging_steps,
        eval_strategy="steps",
        **_eval_and_save_kwargs(config),
        **_best_model_kwargs(config),
        save_total_limit=2,
        prediction_loss_only=True,
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        run_name="persona-training",
        report_to="none",
        logging_first_step=True,
    )

    # trl changed its trainer API between versions:
    #   * trl < 0.13 — SFTTrainer takes `dataset_text_field` + `max_seq_length`
    #                  and tokenizes raw text internally.
    #   * trl >= 0.13 — tokenization moved to the args; pre-tokenized datasets
    #                  (input_ids present) are detected and used as-is.
    sft_params = inspect.signature(UnslothTrainer.__init__).parameters
    if "dataset_text_field" in sft_params:
        logger.info("[Unsloth] Using classic trl dataset_text_field tokenization")
        trainer = UnslothTrainer(
            model=model,
            tokenizer=tokenizer,
            args=args,
            train_dataset=train_ds,
            eval_dataset=eval_ds,
            dataset_text_field="text",
            max_seq_length=config.max_seq_length,
            callbacks=_callbacks(config),
        )
    else:
        from transformers import DataCollatorForLanguageModeling

        logger.info("[Unsloth] Using pre-tokenized datasets (trl >= 0.13 API)")
        train_ds, eval_ds = _split_and_sample(
            _tokenize_dataset(dataset, tokenizer, config.max_seq_length), config
        )
        trainer = UnslothTrainer(
            model=model,
            args=args,
            train_dataset=train_ds,
            eval_dataset=eval_ds,
            processing_class=tokenizer,
            data_collator=DataCollatorForLanguageModeling(
                tokenizer=tokenizer, mlm=False
            ),
            callbacks=_callbacks(config),
        )

    logger.info("[Unsloth] Starting training...")
    trainer.train(resume_from_checkpoint=resume_from)

    # Save the LoRA adapter (PersonaModel inference loads this)
    final_path = str(output_dir / "final")
    trainer.save_model(final_path)
    tokenizer.save_pretrained(final_path)
    logger.info(f"[Unsloth] LoRA adapter saved to {final_path}")

    # Merged 16-bit model — a single self-contained checkpoint
    model.save_pretrained_merged(final_path, tokenizer, save_method="merged_16bit")
    logger.info(f"[Unsloth] Merged 16-bit model saved to {final_path}")

    # Optional GGUF export for llama.cpp / Ollama (the Lilly backend)
    if config.save_gguf:
        gguf_dir = str(output_dir / "gguf")
        model.save_pretrained_gguf(
            gguf_dir, tokenizer, quantization_method=config.gguf_quant
        )
        logger.info(
            f"[Unsloth] GGUF ({config.gguf_quant}) saved to {gguf_dir} "
            f"— deploy with: cp {gguf_dir}/*.gguf ../lillyos/models/"
        )

    eval_results = trainer.evaluate()
    logger.info(
        f"[Unsloth] Evaluation: perplexity={math.exp(eval_results['eval_loss']):.2f}"
    )

    return final_path


# ─── Classic PEFT backend (CPU fallback) ───────────────────────────────
def _tokenize_function(examples, tokenizer, max_length: int):
    texts = examples["text"]
    outputs = tokenizer(
        texts,
        truncation=True,
        padding="max_length",
        max_length=max_length,
        return_tensors=None,
    )
    outputs["labels"] = outputs["input_ids"].copy()
    return outputs


def _train_peft(
    dataset: Dataset,
    config: TrainerConfig,
    resume_from: Optional[str] = None,
):
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        TrainingArguments,
        Trainer,
        DataCollatorForLanguageModeling,
    )
    from peft import (
        LoraConfig,
        get_peft_model,
        TaskType,
        prepare_model_for_kbit_training,
    )

    set_seed(42)
    device_map = "cpu" if config.use_cpu else "auto"

    logger.info(f"[PEFT] Loading base model: {config.base_model}")
    logger.info(f"[PEFT] Device: {'CPU' if config.use_cpu else 'auto'}")

    if config.use_cpu:
        torch.set_num_threads(min(16, os.cpu_count() or 4))

    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model,
        trust_remote_code=True,
        use_fast=True,
    )
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        trust_remote_code=True,
        device_map=device_map,
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
    )

    model.gradient_checkpointing_enable()

    lora_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=config.target_modules,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    tokenized_dataset = dataset.map(
        lambda x: _tokenize_function(x, tokenizer, config.max_seq_length),
        batched=True,
        remove_columns=dataset.column_names,
        desc="Tokenizing",
    )

    split_ds = tokenized_dataset.train_test_split(
        test_size=min(0.1, 0.1),
        seed=42,
    )
    train_ds = split_ds["train"]
    eval_ds = split_ds["test"]

    if config.max_train_samples and len(train_ds) > config.max_train_samples:
        train_ds = train_ds.select(range(config.max_train_samples))
    if config.max_val_samples and len(eval_ds) > config.max_val_samples:
        eval_ds = eval_ds.select(range(config.max_val_samples))

    logger.info(f"[PEFT] Train samples: {len(train_ds)}, Eval samples: {len(eval_ds)}")

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=1,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        gradient_accumulation_steps=config.grad_accum_steps,
        max_steps=config.max_steps,
        learning_rate=config.learning_rate,
        warmup_steps=config.warmup_steps,
        logging_steps=config.logging_steps,
        eval_strategy="steps",
        **_eval_and_save_kwargs(config),
        **_best_model_kwargs(config),
        save_total_limit=2,
        prediction_loss_only=True,
        dataloader_num_workers=2,
        fp16=config.use_fp16,
        bf16=config.use_bf16,
        run_name="persona-training",
        report_to="none",
        gradient_checkpointing=True,
        logging_first_step=True,
    )

    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer,
        mlm=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=data_collator,
        processing_class=tokenizer,
        callbacks=_callbacks(config),
    )

    logger.info("[PEFT] Starting training...")
    trainer.train(resume_from_checkpoint=resume_from)

    final_path = str(output_dir / "final")
    trainer.save_model(final_path)
    tokenizer.save_pretrained(final_path)
    logger.info(f"[PEFT] Model saved to {final_path}")

    eval_results = trainer.evaluate()
    logger.info(
        f"[PEFT] Evaluation: perplexity={math.exp(eval_results['eval_loss']):.2f}"
    )

    return final_path
