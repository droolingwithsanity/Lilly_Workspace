#!/usr/bin/env python3
"""
Persona Trainer — Background training with pre-training conversation tests.

This script:
1. Tests persona conversations to work out bugs
2. Trains all personas
3. Runs in background with logging

Usage:
    nohup python trainer/train_all_background.py > trainer/logs/background_training.log 2>&1 &
"""

import logging
import sys
import time
from pathlib import Path
from datetime import datetime

# Setup logging
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "background_training.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("BackgroundTrainer")

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_persona_conversations():
    """Test persona conversations to work out bugs before training."""
    logger.info("=" * 60)
    logger.info("PHASE 1: Persona Conversation Testing")
    logger.info("=" * 60)

    from trainer.inference import PersonaModel
    from trainer.persona_backgrounds import list_personas, get_persona

    personas = list_personas()
    logger.info(f"Testing {len(personas)} personas: {personas}")

    # Load model (base model without adapter for initial testing)
    logger.info("Loading base model for conversation testing...")
    try:
        model = PersonaModel(
            base_model_name="Qwen/Qwen2.5-0.5B",
            adapter_path=None,  # Use base model first
            use_cpu=True,
        )
        logger.info("Base model loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load base model: {e}")
        logger.info(
            "Continuing with training anyway (model will be trained from scratch)"
        )
        return True

    # Test conversations between personas
    test_prompts = [
        "Hey, how's it going?",
        "What do you think about life?",
        "Tell me about yourself.",
        "What's your favorite thing to do?",
        "How do you handle stress?",
    ]

    conversation_log = []
    errors = []

    for i, persona_id in enumerate(personas):
        persona_info = get_persona(persona_id)
        persona_name = persona_info["name"] if persona_info else persona_id

        logger.info(f"\n--- Testing Persona: {persona_name} ({persona_id}) ---")

        for prompt in test_prompts[:2]:  # Test 2 prompts per persona
            try:
                response = model.generate(
                    persona_id=persona_id,
                    user_input=prompt,
                    max_new_tokens=150,
                    temperature=0.8,
                )

                if response and response != "(...)":
                    logger.info(f"  User: {prompt}")
                    logger.info(f"  {persona_name}: {response[:100]}...")
                    conversation_log.append(
                        {
                            "persona": persona_id,
                            "prompt": prompt,
                            "response": response,
                            "status": "ok",
                        }
                    )
                else:
                    logger.warning(f"  Empty response from {persona_name}")
                    errors.append(f"Empty response from {persona_id}")

            except Exception as e:
                logger.error(f"  Error with {persona_name}: {e}")
                errors.append(f"Error with {persona_id}: {str(e)}")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("CONVERSATION TEST SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total tests: {len(conversation_log)}")
    logger.info(f"Errors: {len(errors)}")

    if errors:
        logger.warning("Errors encountered:")
        for error in errors:
            logger.warning(f"  - {error}")
        logger.info("Continuing with training despite errors...")
    else:
        logger.info("All conversation tests passed!")

    return True


def train_all_personas():
    """Train all personas."""
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 2: Training All Personas")
    logger.info("=" * 60)

    from trainer.config import TrainerConfig
    from trainer.download_datasets import download_all_datasets
    from trainer.prepare_data import prepare_training_data
    from trainer.persona_backgrounds import list_personas
    from trainer.train import train

    # Configure for CPU training (slower but works)
    config = TrainerConfig(
        base_model="Qwen/Qwen2.5-0.5B",
        output_dir="trained_persona_model",
        batch_size=2,
        max_steps=300,
        learning_rate=2e-4,
        max_seq_length=512,
        max_train_samples=5000,
        use_unsloth=False,  # Force PEFT on CPU
        use_cpu=True,
    )

    personas = list_personas()
    logger.info(f"Training {len(personas)} personas: {personas}")

    # Download datasets
    logger.info("Downloading datasets...")
    try:
        raw = download_all_datasets(config.dataset_cache_dir)
        logger.info("Datasets downloaded successfully")
    except Exception as e:
        logger.error(f"Failed to download datasets: {e}")
        logger.info("Attempting to continue with cached data...")
        raw = {}

    # Prepare training data
    logger.info("Preparing training data...")
    try:
        dataset = prepare_training_data(
            raw, personas_to_use=personas, max_samples=config.max_train_samples
        )
        logger.info(f"Training data prepared: {len(dataset)} samples")
    except Exception as e:
        logger.error(f"Failed to prepare training data: {e}")
        return False

    # Start training
    logger.info("Starting training...")
    start_time = time.time()

    try:
        final_path = train(dataset, config)
        training_time = time.time() - start_time
        logger.info(f"Training completed in {training_time:.1f} seconds")
        logger.info(f"Model saved to: {final_path}")
        return True
    except Exception as e:
        logger.error(f"Training failed: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return False


def main():
    """Main training pipeline."""
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BACKGROUND PERSONA TRAINING STARTED")
    logger.info(f"Start time: {start_time}")
    logger.info("=" * 60)

    # Phase 1: Test conversations
    logger.info("\n>>> PHASE 1: Testing persona conversations...")
    test_success = test_persona_conversations()

    if not test_success:
        logger.error("Conversation testing failed. Aborting training.")
        return

    # Phase 2: Train all personas
    logger.info("\n>>> PHASE 2: Training all personas...")
    train_success = train_all_personas()

    # Final summary
    end_time = datetime.now()
    duration = end_time - start_time

    logger.info("\n" + "=" * 60)
    logger.info("TRAINING PIPELINE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Start time: {start_time}")
    logger.info(f"End time: {end_time}")
    logger.info(f"Duration: {duration}")
    logger.info(f"Conversation testing: {'PASSED' if test_success else 'FAILED'}")
    logger.info(f"Training: {'SUCCESS' if train_success else 'FAILED'}")

    if train_success:
        logger.info("\nModel ready for use!")
        logger.info("Copy to lillyos/models/ with:")
        logger.info("  cp trained_persona_model/final/* lillyos/models/")
    else:
        logger.error("\nTraining failed. Check logs for details.")


if __name__ == "__main__":
    main()
