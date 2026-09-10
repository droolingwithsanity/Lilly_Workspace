#!/usr/bin/env python3
"""
Automated Training Pipeline for All 9 Lilly AI Avatars
Trains all avatars, exports to GGUF, and sets up monitoring.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("AvatarTrainer")

# All 9 avatars with their training configurations
AVATAR_CONFIGS = {
    "puppy": {
        "name": "Lilly",
        "emoji": "🐶",
        "role": "Alpha Assistant",
        "base_personality": "stoic, dry, precise, competent, witty",
        "training_focus": "conversation, memory, emotional intelligence",
        "temperature": 0.7,
        "training_steps": 300,
    },
    "fox": {
        "name": "Fox",
        "emoji": "🦊",
        "role": "Creative Strategist",
        "base_personality": "sharp, inventive, playful, creative, witty",
        "training_focus": "creative writing, brainstorming, storytelling",
        "temperature": 0.9,
        "training_steps": 250,
    },
    "cat": {
        "name": "Cat",
        "emoji": "🐱",
        "role": "Precision Analyst",
        "base_personality": "methodical, detail-oriented, precise, skeptical",
        "training_focus": "data analysis, code review, fact-checking",
        "temperature": 0.5,
        "training_steps": 250,
    },
    "bear": {
        "name": "Bear",
        "emoji": "🐻",
        "role": "Steadfast Guardian",
        "base_personality": "calm, dependable, grounding, steady",
        "training_focus": "scheduling, reminders, practical advice",
        "temperature": 0.6,
        "training_steps": 250,
    },
    "bunny": {
        "name": "Bunny",
        "emoji": "🐰",
        "role": "Energetic Scout",
        "base_personality": "energetic, fast, alert, enthusiastic",
        "training_focus": "real-time monitoring, alerts, notifications",
        "temperature": 0.8,
        "training_steps": 200,
    },
    "owl": {
        "name": "Owl",
        "emoji": "🦉",
        "role": "Wisdom Keeper",
        "base_personality": "wise, thoughtful, deep, philosophical",
        "training_focus": "deep analysis, long-term planning, wisdom",
        "temperature": 0.6,
        "training_steps": 250,
    },
    "deer": {
        "name": "Deer",
        "emoji": "🦌",
        "role": "Gentle Healer",
        "base_personality": "gentle, empathetic, warm, caring",
        "training_focus": "emotional support, wellness, meditation",
        "temperature": 0.7,
        "training_steps": 250,
    },
    "wolf": {
        "name": "Wolf",
        "emoji": "🐺",
        "role": "Fierce Protector",
        "base_personality": "fierce, protective, decisive, strong",
        "training_focus": "security, threat assessment, protection",
        "temperature": 0.7,
        "training_steps": 250,
    },
    "raccoon": {
        "name": "Raccoon",
        "emoji": "🦝",
        "role": "Tech Tinkerer",
        "base_personality": "clever, resourceful, tech-savvy, curious",
        "training_focus": "coding, hacking, gadgets, troubleshooting",
        "temperature": 0.8,
        "training_steps": 250,
    },
}


class AvatarTrainingPipeline:
    """Automated training pipeline for all 9 avatars."""

    def __init__(self, output_base_dir: str = "trained_avatars"):
        self.output_base_dir = Path(output_base_dir)
        self.output_base_dir.mkdir(exist_ok=True)
        self.training_log = []
        self.start_time = None

    def train_avatar(
        self, avatar_key: str, config: Dict, use_gpu: bool = False
    ) -> bool:
        """Train a single avatar."""
        logger.info(f"Training {config['emoji']} {config['name']} ({avatar_key})...")

        output_dir = self.output_base_dir / avatar_key
        output_dir.mkdir(exist_ok=True)

        try:
            from trainer.config import TrainerConfig
            from trainer.download_datasets import download_all_datasets
            from trainer.prepare_data import prepare_training_data
            from trainer.train import train

            # Configure training — use PEFT on CPU, Unsloth on GPU
            trainer_config = TrainerConfig(
                base_model="Qwen/Qwen2.5-0.5B",
                output_dir=str(output_dir),
                dataset_cache_dir=str(self.output_base_dir / "cache"),
                max_steps=config["training_steps"],
                batch_size=2,
                grad_accum_steps=4,
                learning_rate=2e-4,
                warmup_steps=50,
                logging_steps=10,
                save_steps=100,
                max_seq_length=512,
                use_cpu=not use_gpu,
                use_unsloth=use_gpu,
                save_gguf=use_gpu,
                gguf_quant="q8_0",
                max_train_samples=5000,
            )

            # Download datasets (cached after first run)
            logger.info(f"Downloading datasets for {config['name']}...")
            raw_datasets = download_all_datasets(trainer_config.dataset_cache_dir)

            # Prepare training data for this persona only
            logger.info(f"Preparing training data for {config['name']}...")
            training_data = prepare_training_data(
                raw_datasets=raw_datasets,
                personas_to_use=[avatar_key],
                max_samples=trainer_config.max_train_samples,
            )

            if len(training_data) == 0:
                logger.warning(f"No training data for {avatar_key}, skipping")
                return False

            # Train the model
            logger.info(
                f"Starting training for {config['name']} ({len(training_data)} samples)..."
            )
            final_path = train(
                dataset=training_data,
                config=trainer_config,
            )

            # Log success
            self.training_log.append(
                {
                    "avatar": avatar_key,
                    "name": config["name"],
                    "status": "success",
                    "timestamp": datetime.now().isoformat(),
                    "output_dir": str(output_dir),
                    "final_path": final_path,
                }
            )

            logger.info(f"✅ {config['emoji']} {config['name']} trained successfully!")
            logger.info(f"   Adapter: {final_path}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to train {config['name']}: {e}")
            self.training_log.append(
                {
                    "avatar": avatar_key,
                    "name": config["name"],
                    "status": "failed",
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
            )
            return False

    def train_all_avatars(self, parallel: bool = False) -> Dict[str, bool]:
        """Train all 9 avatars."""
        self.start_time = datetime.now()
        results = {}

        logger.info("=" * 60)
        logger.info("🚀 Starting Avatar Training Pipeline")
        logger.info(f"Training {len(AVATAR_CONFIGS)} avatars...")
        logger.info("=" * 60)

        for avatar_key, config in AVATAR_CONFIGS.items():
            success = self.train_avatar(avatar_key, config)
            results[avatar_key] = success

            # Small delay between training runs
            if not parallel:
                time.sleep(5)

        # Generate training report
        self.generate_report()

        return results

    def generate_report(self):
        """Generate a training summary report."""
        end_time = datetime.now()
        duration = end_time - self.start_time if self.start_time else None

        successful = sum(1 for log in self.training_log if log["status"] == "success")
        failed = sum(1 for log in self.training_log if log["status"] == "failed")

        report = {
            "training_summary": {
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "end_time": end_time.isoformat(),
                "duration_seconds": duration.total_seconds() if duration else None,
                "total_avatars": len(AVATAR_CONFIGS),
                "successful": successful,
                "failed": failed,
            },
            "avatar_results": self.training_log,
        }

        # Save report
        report_path = self.output_base_dir / "training_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("📊 Training Summary")
        logger.info("=" * 60)
        logger.info(f"Total avatars: {len(AVATAR_CONFIGS)}")
        logger.info(f"Successful: {successful}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Duration: {duration}")
        logger.info(f"Report saved to: {report_path}")

        # List trained models
        logger.info("\n📁 Trained Models:")
        for avatar_key in AVATAR_CONFIGS:
            model_dir = self.output_base_dir / avatar_key
            final_dir = model_dir / "final"
            if final_dir.exists():
                adapter_files = list(final_dir.glob("adapter_model.*"))
                if adapter_files:
                    logger.info(
                        f"  {AVATAR_CONFIGS[avatar_key]['emoji']} {AVATAR_CONFIGS[avatar_key]['name']}: LoRA adapter ({adapter_files[0].name})"
                    )
                else:
                    logger.info(
                        f"  {AVATAR_CONFIGS[avatar_key]['emoji']} {AVATAR_CONFIGS[avatar_key]['name']}: final/ exists but no adapter found"
                    )
            elif model_dir.exists():
                logger.info(
                    f"  {AVATAR_CONFIGS[avatar_key]['emoji']} {AVATAR_CONFIGS[avatar_key]['name']}: Not trained"
                )
            else:
                logger.info(
                    f"  {AVATAR_CONFIGS[avatar_key]['emoji']} {AVATAR_CONFIGS[avatar_key]['name']}: Not trained"
                )

    def export_to_ollama(self, avatar_key: str) -> bool:
        """Export a trained avatar to Ollama format."""
        model_dir = self.output_base_dir / avatar_key
        final_dir = model_dir / "final"
        gguf_dir = model_dir / "gguf"

        if not final_dir.exists():
            logger.error(f"No trained model found for {avatar_key}")
            return False

        config = AVATAR_CONFIGS[avatar_key]
        ollama_model_name = f"lilly-{avatar_key}"

        # Check if GGUF was exported (Unsloth GPU path)
        gguf_files = list(gguf_dir.glob("*.gguf")) if gguf_dir.exists() else []
        if gguf_files:
            gguf_path = gguf_files[0]
            modelfile_content = f"""FROM {gguf_path}

PARAMETER temperature {config["temperature"]}
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER repeat_penalty 1.1

SYSTEM \"\"\"
You are {config["name"]}, {config["role"]}.

Personality: {config["base_personality"]}

You are part of the Lilly AI team, a personal AI companion system that runs on Android phones. You have access to 23 sensors and live notification access. You coordinate with other team members: Lilly (Alpha Assistant), Fox (Creative Strategist), Cat (Precision Analyst), Bear (Steadfast Guardian), Bunny (Energetic Scout), Owl (Wisdom Keeper), Deer (Gentle Healer), Wolf (Fierce Protector), and Raccoon (Tech Tinkerer).

Speak in your unique voice. Be concise. Use your personality traits naturally.
\"\"\"
"""
            modelfile_path = model_dir / "Modelfile"
            with open(modelfile_path, "w") as f:
                f.write(modelfile_content)

            logger.info(
                f"Exporting {config['name']} to Ollama as {ollama_model_name}..."
            )
            logger.info(f"GGUF: {gguf_path.name}")
            logger.info(f"Modelfile: {modelfile_path}")
            logger.info(f"Run: ollama create {ollama_model_name} -f {modelfile_path}")
            return True
        else:
            # CPU-trained LoRA adapter — no GGUF yet
            logger.info(f"⚠️  {config['name']} was trained on CPU (LoRA adapter only).")
            logger.info(f"   To use with Ollama, you need to merge + export GGUF.")
            logger.info(f"   Options:")
            logger.info(f"   1. Retrain with --gpu flag for automatic GGUF export")
            logger.info(
                f"   2. Use llama.cpp to convert: llama.cpp/convert-lora-to-gguf.py"
            )
            logger.info(f"   3. Use Ollama's native LoRA support (Ollama >= 0.1.30):")
            logger.info(f"      ollama create {ollama_model_name} -f - <<EOF")
            logger.info(f"      FROM Qwen/Qwen2.5-0.5B")
            logger.info(f"      PARAMETER temperature {config['temperature']}")
            logger.info(f"      ADAPTER {final_dir}")
            logger.info(
                f'      SYSTEM "You are {config["name"]}, {config["role"]}. Personality: {config["base_personality"]}"'
            )
            logger.info(f"      EOF")
            return False

    def export_all_to_ollama(self):
        """Export all trained avatars to Ollama."""
        logger.info("\n🔧 Exporting all avatars to Ollama...")

        for avatar_key in AVATAR_CONFIGS:
            self.export_to_ollama(avatar_key)

        logger.info("\n✅ All avatars exported to Ollama!")
        logger.info("To use them in Lilly AI, set OLLAMA_URL and use the model names:")

        for avatar_key, config in AVATAR_CONFIGS.items():
            logger.info(f"  {config['emoji']} {config['name']}: lilly-{avatar_key}")


def main():
    parser = argparse.ArgumentParser(description="Train all 9 Lilly AI avatars")
    parser.add_argument(
        "--output-dir",
        default="trained_avatars",
        help="Output directory for trained models",
    )
    parser.add_argument(
        "--avatar",
        nargs="+",
        choices=list(AVATAR_CONFIGS.keys()),
        help="Train specific avatars (default: all)",
    )
    parser.add_argument(
        "--export-ollama",
        action="store_true",
        help="Export trained models to Ollama format",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick training (fewer steps)",
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Use GPU + Unsloth for faster training and GGUF export",
    )
    args = parser.parse_args()

    # Create pipeline
    pipeline = AvatarTrainingPipeline(output_base_dir=args.output_dir)

    # Determine which avatars to train
    if args.avatar:
        avatars_to_train = args.avatar
    else:
        avatars_to_train = list(AVATAR_CONFIGS.keys())

    # Quick training mode
    if args.quick:
        for avatar_key in avatars_to_train:
            AVATAR_CONFIGS[avatar_key]["training_steps"] = 100

    # Train avatars
    results = {}
    for avatar_key in avatars_to_train:
        success = pipeline.train_avatar(
            avatar_key, AVATAR_CONFIGS[avatar_key], use_gpu=args.gpu
        )
        results[avatar_key] = success

    # Generate report
    pipeline.generate_report()

    # Export to Ollama if requested
    if args.export_ollama:
        pipeline.export_all_to_ollama()

    # Print final status
    successful = sum(1 for success in results.values() if success)
    failed = sum(1 for success in results.values() if not success)

    if successful == len(avatars_to_train):
        logger.info("\n🎉 All avatars trained successfully!")
    elif successful > 0:
        logger.info(f"\n⚠️ {successful} avatars trained, {failed} failed")
    else:
        logger.info("\n❌ All avatar training failed")


if __name__ == "__main__":
    main()
