#!/usr/bin/env python3
"""
Persona Trainer — fine-tune an LLM on conversational data with persona backgrounds.
"""

import argparse
import logging
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("PersonaTrainer")


def main():
    parser = argparse.ArgumentParser(description="Persona-conditioned LLM training")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B")
    parser.add_argument("--output-dir", default="trained_persona_model")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-samples", type=int, default=5000)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument(
        "--lora-r", type=int, default=None, help="LoRA rank (default: from config)"
    )
    parser.add_argument(
        "--no-unsloth",
        action="store_true",
        help="Force the classic PEFT path instead of Unsloth (e.g. on CPU)",
    )
    parser.add_argument(
        "--load-in-4bit",
        action="store_true",
        default=None,
        help="QLoRA — load the base model in 4-bit (Unsloth, default: True)",
    )
    parser.add_argument(
        "--save-gguf",
        action="store_true",
        help="Also export the tuned model as GGUF for llama.cpp/Ollama",
    )
    parser.add_argument(
        "--gguf-quant",
        default="q8_0",
        help="GGUF quantization: q8_0, q4_k_m, q4_k_s, q5_k_m, f16 (default q8_0)",
    )
    parser.add_argument(
        "--personas",
        nargs="+",
        default=None,
        help="Personas to train on (default: all)",
    )
    parser.add_argument(
        "--skip-download", action="store_true", help="Skip dataset download"
    )
    parser.add_argument(
        "--embodiment",
        type=int,
        default=0,
        help="Mix in N embodiment self-model examples (phone=body, "
        "server=brain, camera=eyes) from real workspace data; 0 = off",
    )
    parser.add_argument(
        "--fineweb-samples",
        type=int,
        default=0,
        help="Stream N FineWeb sample-10BT docs for general knowledge "
        "(raw web text, capped at 2048 chars each; 0 = off)",
    )
    parser.add_argument(
        "--inference",
        action="store_true",
        help="Run interactive inference with trained model",
    )
    parser.add_argument(
        "--adapter-path", default=None, help="Path to LoRA adapter for inference"
    )
    parser.add_argument(
        "--persona", default="street_kid", help="Persona to use for inference"
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="Resume training from checkpoint path (e.g. trained_persona_model/checkpoint-5)",
    )
    args = parser.parse_args()

    from trainer.config import TrainerConfig
    from trainer.download_datasets import download_all_datasets
    from trainer.prepare_data import prepare_training_data
    from trainer.persona_backgrounds import list_personas
    from trainer.train import train
    from trainer.inference import PersonaModel
    from trainer.persona_backgrounds import get_persona

    if args.inference:
        adapter = args.adapter_path or (Path(args.output_dir) / "final")
        if not Path(adapter).exists():
            logger.error(f"Adapter not found at {adapter}")
            sys.exit(1)
        model = PersonaModel(
            base_model_name=args.base_model,
            adapter_path=str(adapter),
        )
        print(f"\nPersona: {args.persona}")
        print(f"PersonaModel ready. Type 'exit' to quit, 'persona <id>' to switch.\n")
        current_persona = args.persona
        history = []
        while True:
            try:
                user = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user:
                continue
            if user.lower() == "exit":
                break
            if user.lower().startswith("persona "):
                pid = user.split(" ", 1)[1].strip()
                if pid in list_personas():
                    current_persona = pid
                    history = []
                    print(f"Switched to: {pid}")
                else:
                    print(f"Unknown persona. Options: {', '.join(list_personas())}")
                continue

            resp = model.generate(
                persona_id=current_persona,
                user_input=user,
                conversation_history=history[-6:],
                temperature=1.2,
            )
            p_info = model.get_persona_info(current_persona)
            name = p_info["name"] if p_info else "AI"
            print(f"{name}: {resp}")
            history.append({"role": "user", "content": user})
            history.append({"role": "assistant", "content": resp})
        return

    config_kwargs = dict(
        base_model=args.base_model,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        max_seq_length=args.max_seq_length,
        max_train_samples=args.max_samples,
    )
    if args.lora_r is not None:
        config_kwargs["lora_r"] = args.lora_r
    if args.load_in_4bit is not None:
        config_kwargs["load_in_4bit"] = args.load_in_4bit
    config_kwargs["use_unsloth"] = not args.no_unsloth
    config_kwargs["save_gguf"] = args.save_gguf
    config_kwargs["gguf_quant"] = args.gguf_quant
    config = TrainerConfig(**config_kwargs)

    backend = "unsloth" if config.use_unsloth else "peft"
    logger.info(f"Training backend: {backend} (auto-switches to PEFT on CPU)")
    if backend == "unsloth" and not torch.cuda.is_available():
        logger.warning(
            "CUDA not available — Unsloth needs a GPU; will fall back to PEFT"
        )

    personas = args.personas or list_personas()
    logger.info(f"Personas to train: {personas}")

    raw = {}
    if not args.skip_download:
        raw = download_all_datasets(
            config.dataset_cache_dir, fineweb_samples=args.fineweb_samples
        )
    else:
        logger.info("Skipping download")

    logger.info("Preparing training data...")
    dataset = prepare_training_data(
        raw, personas_to_use=personas, max_samples=config.max_train_samples
    )

    # Mix in the embodiment self-model dataset (phone=body, server=brain,
    # camera=eyes) mined from real workspace data
    if args.embodiment > 0:
        from datasets import Dataset as _DS, concatenate_datasets

        from trainer.prepare_embodiment_data import build_embodiment_texts

        emb_texts = build_embodiment_texts(max_examples=args.embodiment)
        if emb_texts:
            dataset = concatenate_datasets(
                [dataset, _DS.from_dict({"text": emb_texts})]
            ).shuffle(seed=42)
            logger.info(f"Mixed in {len(emb_texts)} embodiment examples")

    logger.info("Starting training...")
    final_path = train(dataset, config, resume_from=args.resume_from)
    logger.info(f"Done! Model saved to {final_path}")

    logger.info("Starting interactive session...")
    model = PersonaModel(
        base_model_name=args.base_model,
        adapter_path=final_path,
    )
    print(f"\nPersonaModel ready with {len(personas)} personas.")
    print("Type 'exit' to quit, 'persona <id>' to switch.\n")
    current_persona = personas[0]
    history = []
    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() == "exit":
            break
        if user.lower().startswith("persona "):
            pid = user.split(" ", 1)[1].strip()
            if pid in list_personas():
                current_persona = pid
                history = []
                print(f"Switched to: {pid}")
            else:
                print(f"Unknown. Options: {', '.join(list_personas())}")
            continue

        resp = model.generate(
            persona_id=current_persona,
            user_input=user,
            conversation_history=history[-6:],
        )
        p_info = model.get_persona_info(current_persona)
        name = p_info["name"] if p_info else "AI"
        print(f"{name}: {resp}")
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": resp})


if __name__ == "__main__":
    main()
