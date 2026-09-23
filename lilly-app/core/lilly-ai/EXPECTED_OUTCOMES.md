# Expected Outcomes by Training Type

## Persona Training 🧠

### What it does
LoRA fine-tunes Qwen2.5-0.5B per avatar using personality-conditioned conversations. Each avatar gets its own adapter that shifts the model's voice to match their character.

### Will it work right now?
**No — blocked by one argument bug.** Every launch passes `--max-steps N` to `train_all_avatars.py`, which doesn't accept that flag. Every run dies at argparse before training begins. This has happened 30+ times.

### What will happen when you click Start Persona
1. **Immediately**: Scheduler tries to launch `train_all_avatars.py --max-steps N` → **argparse rejection** → log shows `error: unrecognized arguments: --max-steps N`
2. **No model is produced**. No `trained_avatars/` directory will exist.
3. **Instagram keeps using base model**: All avatar DM/comment generation falls back to `qwen2.5:3b` (noted in logs as "trained model not registered in Ollama; using base model")

### Fix needed (1 line)
Add `--max-steps` argument to `train_all_avatars.py` that maps to `AVATAR_CONFIGS[avatar]["training_steps"]`

### After fix — expected outcome
| Metric | Value |
|--------|-------|
| Per-avatar time (CPU) | ~3-8 min |
| All 9 avatars (CPU) | ~30-80 min |
| All 9 avatars (GPU) | ~5-15 min |
| Output | `trained_avatars/<key>/final/adapter_model.bin` |
| LLM usage | Can't deploy to Ollama without GPU (no GGUF) |
| Instagram effect | Slightly more persona-like replies via Ollama |

### Success indicator in logs
```
✅ 🐶 Lilly trained successfully!
   Adapter: trained_avatars/puppy/final
[PEFT] Evaluation: perplexity=23.41
```

---

## YOLO Vision Training 👁

### What it does
Self-trains a YOLOv8 detection model on collected photos. Learns to recognize objects (people, cars, etc.) from your phone gallery and scraped images.

### Will it work right now?
**Not until deployed.** `yolo_trainer_api.py` needs to be copied to xced and started. Also needs `pip install ultralytics` on xced.

### What will happen when you click Start YOLO
1. **Currently**: `yolo_trainer_api.py` not running on :8199 → dashboard shows "down" → no training can start
2. **After deployment**: API receives training request → YOLOv8 trains on 279 samples → produces model in `trained_models/`

### After deployment — expected outcome
| Metric | Value |
|--------|-------|
| Import photos (279) | ~2 seconds |
| First training run (50 epochs) | ~5-15 min on 4 CPU cores |
| Output | `trained_models/yolov8n_last/` + `best.pt` |
| Detection accuracy | ~72-84% estimated (based on 279 samples) |
| Dashboard update | Stats show class distribution, detection counts |

### Success indicator
```
✓ YOLO training started on :8199 (50 epochs)
```

---

## Instagram Insomnia / Automation 🤖

### What it does
NOT model training. UI automation that performs Instagram engagement actions (follow, like, comment, story) on targets you specify.

### Will it work right now?
**Partially.** The InsomniaProvider source file is missing from the codebase. The UI exists in training.html but the backend provider can't be loaded. Existing logs show mixed results:
- Follow + likes: completed but with accessibility failures
- Comments: failed (accessibility bridge unavailable)
- Phone-dependent: requires Termux/a11y bridge on phone

### What will happen when you click Start Session
1. Provider loads (or fails to load if source missing)
2. Targets are processed sequentially at chosen speed
3. Actions logged in live action log
4. Follows/likes/comments attempted against targets

### Expected outcome when working
| Action | Expected |
|--------|----------|
| Follow | Target follows you back (or already does) |
| Like | Post liked, recorded in log |
| Comment | Post commented on (needs accessibility bridge) |
| Story | Story viewed/互动 |

### Current blocker
`insomnia_provider.py` doesn't exist in the workspace. Need to create or restore it.

---

## Instagram Team 📸

### What it does
Persistent service running all 9 Instagram accounts. Posts content, checks/responds to comments, reads DMs, cross-avatar interactions. Runs 24/7 once started.

### Will it work right now?
**Yes for wolf and raccoon** (running in dm-worker). **Fox just verified** (6 DM threads, login OK). Not running as a Team service — only as individual DM workers.

### What will happen when you click Start Team on dashboard
1. All 9 avatars begin rotation posting schedule (~12h intervals)
2. Comment checks every 1-3 hours
3. DM inbox checks run repeatedly
4. Teammate interactions (avatars chatting with each other)

### Expected outcome
| Metric | Current State |
|--------|---------------|
| wolf | Active DM worker, 0 DMs in dm-worker |
| raccoon | Active DM worker, verified working |
| fox | Active DM worker, 6 DM threads verified |
| Other 6 | Sessions exist, not running as workers |

---

## Scraping 📡

### What it does
Scrapes Instagram hashtags and usernames, auto-populates targets from followers/interactions, builds training datasets for YOLO.

### Will it work right now?
**Needs investigation.** The scraper endpoint exists in `training.html` but backend support depends on `instagram_scraper.py` being properly mounted.

### Expected outcome
| Action | Result |
|--------|--------|
| Scrape #tech hashtag | Returns list of profile URLs |
| Import for training | Photos added to `training_data/samples/` |
| Auto-populate | Followers/interactions auto-targeted |

---

## OSINT 🕵️

### What it does
Reverse face search from uploaded images. Footprint analysis from training console.

### Will it work right now?
**Needs `osint_face_lookup` module.** Check if available: `python3 -c "import osint_face_lookup"`.

### Expected outcome
| Action | Result |
|--------|--------|
| Upload face photo | Reverse search across internet |
| Crop analysis | Face aligned, normalized crops |
| Job created | Tracking in OSINT jobs list |

---

## Summary Table

| Type | Ready? | Time to Result | Output | Blocker |
|------|--------|---------------|--------|---------|
| Persona | ❌ Needs fix | 30-80 min (CPU) | LoRA adapters | `--max-steps` arg |
| YOLO | ❌ Not deployed | 5-15 min | `.pt` model | Missing API server |
| Insomnia | ❌ Provider missing | Real-time | Engagement actions | No provider source |
| Team | ⚠️ Partial | Real-time | Posts/comments/DMs | 6/9 accounts inactive |
| Scraper | ❓ Unknown | Minutes | Training data | Unverified |
| OSINT | ❓ Unknown | Minutes | Search results | Module not found |

### Priority order to get working
1. **Persona fix** — add `--max-steps` to `train_all_avatars.py` (1 line, unlocks all persona training)
2. **Deploy YOLO** — copy `yolo_trainer_api.py` to xced, install ultralytics
3. **Insomnia provider** — create `insomnia_provider.py` from existing code/logs
4. **Activate remaining 6 accounts** — lilly, cat, bear, bunny, owl, deer
