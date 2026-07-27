import logging
import math
import os
import sys
from pathlib import Path
from typing import Optional

import torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
    set_seed,
)
from peft import (
    LoraConfig,
    get_peft_model,
    TaskType,
    prepare_model_for_kbit_training,
)

from .config import TrainerConfig

logger = logging.getLogger("PersonaTrainer.Train")


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


def train(
    dataset: Dataset,
    config: TrainerConfig,
    resume_from: Optional[str] = None,
):
    set_seed(42)
    device_map = "cpu" if config.use_cpu else "auto"

    logger.info(f"Loading base model: {config.base_model}")
    logger.info(f"Device: {'CPU' if config.use_cpu else 'auto'}")

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

    logger.info(f"Train samples: {len(train_ds)}, Eval samples: {len(eval_ds)}")

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
        save_steps=config.save_steps,
        eval_strategy="steps",
        eval_steps=config.save_steps,
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
    )

    logger.info("Starting training...")
    trainer.train(resume_from_checkpoint=resume_from)

    final_path = str(output_dir / "final")
    trainer.save_model(final_path)
    tokenizer.save_pretrained(final_path)
    logger.info(f"Model saved to {final_path}")

    eval_results = trainer.evaluate()
    logger.info(f"Evaluation: perplexity={math.exp(eval_results['eval_loss']):.2f}")

    return str(final_path)
