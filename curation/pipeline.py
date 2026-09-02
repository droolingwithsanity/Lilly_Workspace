#!/usr/bin/env python3
"""
Lilly AI - Dataset Curation & LoRA Fine-Tuning Pipeline
========================================================
Reads curation_answers.json, downloads datasets, curates them,
fine-tunes Llama 3.2 3B with LoRA, exports GGUF, registers in Ollama.

Designed for CPU-only training (62GB RAM server).

Usage:
  python3 pipeline.py              # run all steps
  python3 pipeline.py --step train # run single step
  python3 pipeline.py --resume     # resume from last checkpoint
"""
import json, os, sys, time, re, random, subprocess, hashlib, signal
from pathlib import Path
from datetime import datetime

# ---- PATHS ----
BASE = Path(__file__).parent
ANSWERS = BASE / "curation_answers.json"
LOG = BASE / "training.log"
OUT = BASE / "output"
DATA = BASE / "datasets"
CKPT = BASE / "checkpoints"
STATUS_FILE = BASE / "status.json"

STEPS = ["download", "curate", "format", "train", "export", "register"]

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line, flush=True)
    with open(str(LOG), "a") as f:
        f.write(line + "\n")

def save_status(step, state, extra=None):
    s = {"step": step, "state": state, "time": datetime.now().isoformat()}
    if extra:
        s.update(extra)
    STATUS_FILE.write_text(json.dumps(s, indent=2))

def load_config():
    return json.loads(ANSWERS.read_text())

def ensure_dirs():
    for d in [OUT, DATA, CKPT]:
        d.mkdir(parents=True, exist_ok=True)

# ================================================================
# STEP 1: DOWNLOAD
# ================================================================
def step_download():
    from datasets import load_dataset
    config = load_config()
    ensure_dirs()

    DATASETS = {
        "ultrachat": ("HuggingFaceH4/ultrachat_200k", "train_sft"),
        "lmsys": ("lmsys/chatbot_arena_conversations", "train"),
    }

    for name in config["datasets"]:
        if name == "custom":
            log("Skipping custom — will generate from conversation logs")
            continue
        info = DATASETS.get(name)
        if not info:
            log("Unknown dataset: %s" % name)
            continue

        weight = config["dataset_weights"].get(name, 33)
        max_samples = min(50000, int(50000 * weight / 100))
        out_file = DATA / ("%s.jsonl" % name)

        if out_file.exists():
            existing = sum(1 for _ in open(out_file))
            if existing >= max_samples * 0.9:
                log("Already downloaded %s (%d samples), skipping" % (name, existing))
                continue

        repo, split = info
        log("Downloading %s from %s (max %d)..." % (name, repo, max_samples))

        try:
            ds = load_dataset(repo, split=split, streaming=True, trust_remote_code=True)
            count = 0
            with open(str(out_file), "w") as f:
                for sample in ds:
                    f.write(json.dumps(sample) + "\n")
                    count += 1
                    if count >= max_samples:
                        break
                    if count % 5000 == 0:
                        log("  ... %d/%d" % (count, max_samples))
            log("Downloaded %s: %d samples -> %s" % (name, count, out_file))
        except Exception as e:
            log("FAILED to download %s: %s" % (name, e))

    # Generate custom data from conversation logs
    if "custom" in config["datasets"]:
        generate_custom_data()

    save_status("download", "done")

def generate_custom_data():
    """Extract user/assistant pairs from Lilly conversation logs."""
    conv_files = list(Path("/home/labhrasd/Lilly_Workspace").glob("conversation_memory_*.json"))
    out_file = DATA / "custom.jsonl"
    count = 0
    with open(str(out_file), "w") as f:
        for cf in conv_files:
            try:
                data = json.loads(cf.read_text())
                exchanges = data if isinstance(data, list) else data.get("exchanges", data.get("history", []))
                for i in range(len(exchanges) - 1):
                    a = exchanges[i]
                    b = exchanges[i + 1]
                    role_a = a.get("role", "")
                    role_b = b.get("role", "")
                    content_a = a.get("content", a.get("text", ""))
                    content_b = b.get("content", b.get("text", ""))
                    if role_a == "user" and role_b in ("assistant", "lilly", "") and content_a.strip() and content_b.strip():
                        row = {"messages": [
                            {"role": "user", "content": content_a.strip()},
                            {"role": "assistant", "content": content_b.strip()}
                        ]}
                        f.write(json.dumps(row) + "\n")
                        count += 1
            except Exception:
                pass
    log("Generated %d custom examples from %d conversation files" % (count, len(conv_files)))

# ================================================================
# STEP 2: CURATE
# ================================================================
def step_curate():
    config = load_config()
    ensure_dirs()

    anti = config.get("anti_hallucination", [])
    patterns = []
    if "ai_ref" in anti:
        patterns.append(re.compile(r"\bas\s+an?\s+(?:ai|language\s+model|large\s+language\s+model|llm)\s*,?\s*", re.I))
        patterns.append(re.compile(r"\bi\s+(?:am|i'm)\s+an?\s+(?:ai|language\s+model)\s*,?\s*", re.I))
    if "training_data" in anti:
        patterns.append(re.compile(r"\baccording\s+to\s+my\s+(?:training(?:\s+(?:data|cutoff))?|knowledge(?:\s+cutoff)?|data)\s*,?\s*", re.I))
        patterns.append(re.compile(r"\bbased\s+on\s+my\s+(?:training|knowledge|understanding)\s*,?\s*", re.I))
    if "disclaimers" in anti:
        patterns.append(re.compile(r"\b(?:please\s+note|it'?s?\s+important\s+to\s+note|keep\s+in\s+mind|note\s+that)\s*(?:that\s+)?", re.I))
    if "apologies" in anti:
        patterns.append(re.compile(r"\b(?:i'?m?\s+)?(?:so\s+)?sorry\s*,?\s*but\s+", re.I))
        patterns.append(re.compile(r"\b(?:i\s+apologize|apologies)\s*,?\s*but\s+", re.I))
    if "sensor_fabrication" in anti:
        patterns.append(re.compile(r"(?:temperature|light|pressure|battery|humidity|steps?)\s+(?:is|reads?)\s+\d+\s*(?:degrees?|lux|hpa|%|c)\s*[,.]?\s*i\s+(?:can|could)\s+(?:feel|sense|see)", re.I))
    if "youtube" in anti:
        patterns.append(re.compile(r"like\s+and\s+subscribe|thanks\s+for\s+watching|link\s+in\s+description", re.I))

    max_turns = config.get("max_turns", 8)
    max_tokens = config.get("max_tokens_conversation", 2048)
    curated = []
    total = 0
    filtered = 0

    for ds_file in DATA.glob("*.jsonl"):
        log("Curating %s..." % ds_file.name)
        with open(str(ds_file)) as f:
            for line in f:
                total += 1
                try:
                    row = json.loads(line)
                except:
                    filtered += 1
                    continue

                conv = extract_conv(row)
                if not conv or len(conv) < 2:
                    filtered += 1
                    continue

                # Quality
                if not quality_ok(conv):
                    filtered += 1
                    continue

                # Length
                if len(conv) > max_turns * 2:
                    conv = conv[:max_turns * 2]

                word_count = sum(len(t["content"].split()) for t in conv)
                if word_count * 1.3 > max_tokens:
                    filtered += 1
                    continue

                # Clean anti-hallucination patterns
                for turn in conv:
                    for p in patterns:
                        turn["content"] = p.sub("", turn["content"])
                    turn["content"] = re.sub(r"\s{2,}", " ", turn["content"]).strip()

                conv = [t for t in conv if t["content"].strip()]
                if len(conv) < 2:
                    filtered += 1
                    continue

                curated.append(conv)
                if len(curated) % 5000 == 0:
                    log("  ... %d curated / %d total" % (len(curated), total))

        log("  After %s: %d curated" % (ds_file.name, len(curated)))

    # Save curated
    out_file = OUT / "curated.jsonl"
    with open(str(out_file), "w") as f:
        for conv in curated:
            f.write(json.dumps({"messages": conv}) + "\n")

    log("Curation complete: %d/%d kept, %d filtered" % (len(curated), total, filtered))
    save_status("curate", "done", {"curated": len(curated), "total": total})
    return curated

def extract_conv(row):
    conv = []
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            role = m.get("role", "").lower()
            content = m.get("content", "")
            if role in ("user", "assistant") and content.strip():
                conv.append({"role": role, "content": content.strip()})
    elif "conversation" in row:
        c = row["conversation"]
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except:
                return None
        if isinstance(c, list):
            for m in c:
                role = m.get("role", "").lower()
                content = m.get("content", "")
                if role in ("user", "assistant") and content.strip():
                    conv.append({"role": role, "content": content.strip()})
    elif "prompt" in row and "response" in row:
        p, r = row.get("prompt", ""), row.get("response", "")
        if p.strip() and r.strip():
            conv = [{"role": "user", "content": p.strip()}, {"role": "assistant", "content": r.strip()}]
    return conv if len(conv) >= 2 else None

def quality_ok(conv):
    for t in conv:
        c = t["content"]
        if t["role"] == "assistant" and len(c.split()) < 3:
            return False
        if c.count("```") > 2:
            return False
        if re.search(r"(.)\1{10,}", c):
            return False
    return True

# ================================================================
# STEP 3: FORMAT
# ================================================================
def step_format():
    config = load_config()
    ensure_dirs()

    curated_file = OUT / "curated.jsonl"
    if not curated_file.exists():
        log("No curated data found. Run curate step first.")
        return

    persona = config["personas"][0] if config.get("personas") else "puppy"
    style = config.get("response_style", "concise")
    emotion = config.get("emotion_level", 5)

    sys_prompt = (
        "You are Lilly, a warm and curious AI companion who lives on your user's phone. "
        "You sense the world through 23 Android sensors. "
        "You speak naturally like a close friend — concise but with personality. "
        "You reference real sensor data when available. "
        "Never fabricate readings. Never start with disclaimers like 'As an AI...'. "
        "Never apologize unnecessarily."
    )
    if style == "concise":
        sys_prompt += " Keep responses short and punchy."
    elif style == "detailed":
        sys_prompt += " Provide thorough, detailed responses with examples."
    if emotion > 7:
        sys_prompt += " Be very expressive and animated."

    examples = []
    with open(str(curated_file)) as f:
        for line in f:
            row = json.loads(line)
            messages = [{"role": "system", "content": sys_prompt}] + row["messages"]
            examples.append({"messages": messages})

    random.seed(42)
    random.shuffle(examples)
    split = int(len(examples) * 0.95)

    train_file = OUT / "train.jsonl"
    eval_file = OUT / "eval.jsonl"

    with open(str(train_file), "w") as f:
        for ex in examples[:split]:
            f.write(json.dumps(ex) + "\n")
    with open(str(eval_file), "w") as f:
        for ex in examples[split:]:
            f.write(json.dumps(ex) + "\n")

    log("Formatted %d train + %d eval examples" % (split, len(examples) - split))
    save_status("format", "done", {"train": split, "eval": len(examples) - split})

# ================================================================
# STEP 4: TRAIN
# ================================================================
def step_train():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer
    from datasets import load_dataset

    config = load_config()
    ensure_dirs()

    MODEL_MAP = {
        "llama3.2-3b": "meta-llama/Llama-3.2-3B",
        "llama3.1-8b": "meta-llama/Llama-3.1-8B",
        "mistral-7b": "mistralai/Mistral-7B-v0.3",
        "phi-3.5": "microsoft/Phi-3.5-mini-instruct",
        "qwen2.5-7b": "Qwen/Qwen2.5-7B",
    }
    hf_model = MODEL_MAP.get(config["base_model"], config["base_model"])
    lora_r = config.get("lora_rank", 16)
    lora_a = config.get("lora_alpha", 32)
    lr = float(config.get("learning_rate", "2e-5"))
    epochs = config.get("epochs", 3)
    batch = config.get("batch_size", 8)

    log("Loading model: %s (LoRA r=%d, a=%d)" % (hf_model, lora_r, lora_a))

    tokenizer = AutoTokenizer.from_pretrained(hf_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        hf_model, torch_dtype=torch.float32, device_map="cpu", trust_remote_code=True
    )

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM, r=lora_r, lora_alpha=lora_a,
        lora_dropout=0.05, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    dataset = load_dataset("json", data_files={
        "train": str(OUT / "train.jsonl"),
        "validation": str(OUT / "eval.jsonl"),
    })

    def tokenize(examples):
        texts = []
        for msgs in examples["messages"]:
            text = ""
            for m in msgs:
                text += "<|begin_of_text|><|start_header_id|>%s<|end_header_id|>\n\n%s<|eot_id|>" % (m["role"], m["content"])
            texts.append(text)
        tok = tokenizer(texts, truncation=True, max_length=2048, padding="max_length")
        tok["labels"] = tok["input_ids"].copy()
        return tok

    tok_ds = dataset.map(tokenize, batched=True, remove_columns=["messages"], num_proc=1)

    ckpt_dir = str(CKPT / "lora-ckpt")
    args = TrainingArguments(
        output_dir=ckpt_dir, num_train_epochs=epochs,
        per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=batch, learning_rate=lr,
        weight_decay=0.01, warmup_ratio=0.1, lr_scheduler_type="cosine",
        logging_steps=10, eval_strategy="steps", eval_steps=100,
        save_strategy="steps", save_steps=200, save_total_limit=3,
        load_best_model_at_end=True, metric_for_best_model="eval_loss",
        report_to="none", fp16=False, bf16=False, dataloader_num_workers=0,
        optim="adamw_torch", max_grad_norm=1.0,
    )

    trainer = SFTTrainer(
        model=model, args=args,
        train_dataset=tok_ds["train"], eval_dataset=tok_ds["validation"],
        processing_class=tokenizer,
    )

    log("Starting LoRA training (%d epochs)..." % epochs)
    start = time.time()
    trainer.train()
    elapsed = time.time() - start
    log("Training complete in %.1f hours" % (elapsed / 3600))

    adapter_dir = OUT / "lora_adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    log("LoRA adapter saved -> %s" % adapter_dir)
    save_status("train", "done", {"hours": round(elapsed / 3600, 1)})

# ================================================================
# STEP 5: EXPORT GGUF
# ================================================================
def step_export():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    config = load_config()
    ensure_dirs()

    MODEL_MAP = {
        "llama3.2-3b": "meta-llama/Llama-3.2-3B",
        "llama3.1-8b": "meta-llama/Llama-3.1-8B",
        "mistral-7b": "mistralai/Mistral-7B-v0.3",
        "phi-3.5": "microsoft/Phi-3.5-mini-instruct",
        "qwen2.5-7b": "Qwen/Qwen2.5-7B",
    }
    hf_model = MODEL_MAP.get(config["base_model"], config["base_model"])
    adapter_dir = OUT / "lora_adapter"
    merged_dir = OUT / "merged_model"
    quant = config.get("quantization", "Q5_K_M").lower()
    ollama_name = config.get("ollama_model_name", "lilly-persona")
    gguf_file = OUT / ("%s-%s.gguf" % (ollama_name, quant))

    log("Loading base model for merge: %s" % hf_model)
    base = AutoModelForCausalLM.from_pretrained(hf_model, torch_dtype=torch.float32)
    tokenizer = AutoTokenizer.from_pretrained(str(adapter_dir))

    log("Loading LoRA adapter...")
    model = PeftModel.from_pretrained(base, str(adapter_dir))

    log("Merging LoRA into base model...")
    model = model.merge_and_unload()

    merged_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_dir))
    tokenizer.save_pretrained(str(merged_dir))
    log("Merged model saved -> %s" % merged_dir)

    # Download llama.cpp convert script
    convert_script = OUT / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        log("Downloading llama.cpp convert script...")
        subprocess.run(["curl", "-sL",
            "https://raw.githubusercontent.com/ggerganov/llama.cpp/master/convert_hf_to_gguf.py",
            "-o", str(convert_script)], check=True)

    log("Converting to GGUF (%s)..." % quant)
    outtype = quant.replace("_", "-")
    cmd = [sys.executable, str(convert_script), str(merged_dir),
           "--outfile", str(gguf_file), "--outtype", outtype]
    subprocess.run(cmd, check=True)

    log("GGUF exported -> %s" % gguf_file)
    save_status("export", "done", {"gguf": str(gguf_file)})
    return gguf_file

# ================================================================
# STEP 6: REGISTER IN OLLAMA
# ================================================================
def step_register():
    config = load_config()
    ollama_name = config.get("ollama_model_name", "lilly-persona")
    quant = config.get("quantization", "Q5_K_M").lower()
    gguf_file = OUT / ("%s-%s.gguf" % (ollama_name, quant))

    if not gguf_file.exists():
        log("GGUF not found: %s" % gguf_file)
        return

    modelfile = OUT / "Modelfile"
    mf = (
        'FROM %s\n\n'
        'PARAMETER temperature 0.7\n'
        'PARAMETER top_p 0.9\n'
        'PARAMETER repeat_penalty 1.1\n'
        'PARAMETER num_ctx 4096\n\n'
        'SYSTEM You are Lilly, a warm and curious AI companion who lives on the phone. '
        'You sense the world through 23 Android sensors. '
        'You speak naturally like a close friend. '
        'Never fabricate sensor readings. Never start with disclaimers. '
        'Be expressive and helpful.\n'
    ) % str(gguf_file)

    modelfile.write_text(mf)
    log("Modelfile written -> %s" % modelfile)

    # Register in local Ollama
    result = subprocess.run(
        ["ollama", "create", ollama_name, "-f", str(modelfile)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log("Registered in local Ollama: %s" % ollama_name)
    else:
        log("Ollama register failed: %s" % result.stderr[:200])

    # Push to remote Ollama if configured
    if config.get("deploy_remote"):
        remote_url = "http://100.73.249.14:11434"
        log("Pushing to remote Ollama at %s..." % remote_url)
        env = os.environ.copy()
        env["OLLAMA_HOST"] = remote_url
        result = subprocess.run(
            ["ollama", "create", ollama_name, "-f", str(modelfile)],
            capture_output=True, text=True, env=env
        )
        if result.returncode == 0:
            log("Registered on remote Ollama: %s" % ollama_name)
        else:
            log("Remote Ollama register failed: %s" % result.stderr[:200])
            log("Try manual: scp %s lilly@100.73.249.14:/tmp/ && ssh lilly@100.73.249.14 'ollama create %s -f /tmp/Modelfile'" % (gguf_file, ollama_name))

    save_status("register", "done")

# ================================================================
# MAIN
# ================================================================
def main():
    ensure_dirs()
    log("=" * 60)
    log("Lilly AI Training Pipeline")
    log("=" * 60)

    if not ANSWERS.exists():
        log("ERROR: curation_answers.json not found!")
        sys.exit(1)

    args = sys.argv[1:]
    step = None
    resume = "--resume" in args

    if "--step" in args:
        idx = args.index("--step")
        if idx + 1 < len(args):
            step = args[idx + 1]

    STEP_FUNCS = {
        "download": step_download,
        "curate": step_curate,
        "format": step_format,
        "train": step_train,
        "export": step_export,
        "register": step_register,
    }

    if step:
        if step not in STEP_FUNCS:
            log("Unknown step: %s (valid: %s)" % (step, ", ".join(STEPS)))
            sys.exit(1)
        log("Running step: %s" % step)
        STEP_FUNCS[step]()
    else:
        start_idx = 0
        if resume and STATUS_FILE.exists():
            status = json.loads(STATUS_FILE.read_text())
            last_step = status.get("step", "")
            if status.get("state") == "done" and last_step in STEPS:
                start_idx = STEPS.index(last_step) + 1
                log("Resuming from step %d (%s)" % (start_idx, STEPS[start_idx] if start_idx < len(STEPS) else "done"))

        for i in range(start_idx, len(STEPS)):
            s = STEPS[i]
            log("--- Step %d/%d: %s ---" % (i + 1, len(STEPS), s))
            save_status(s, "running")
            try:
                STEP_FUNCS[s]()
            except Exception as e:
                log("FAILED at step %s: %s" % (s, e))
                save_status(s, "failed", {"error": str(e)})
                sys.exit(1)

        log("=" * 60)
        log("ALL STEPS COMPLETE!")
        log("=" * 60)

if __name__ == "__main__":
    main()
