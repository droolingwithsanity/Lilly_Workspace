# Lilly AI Tier Upgrade Guide

## Overview

Lilly AI uses a 4-tier subscription model that controls what features are available:

| Tier | Price | Key Unlocks |
|------|-------|-------------|
| **Free** | $0/mo | 2 training runs/day, basic YOLO, no automation, 1 phone |
| **Basic** | $9/mo | Unlimited training, 3 phones, basic automation (intent-only) |
| **Plus** | $29/mo | Full automation (tap + intents), 5 phones, A/B testing, webhooks |
| **Pro** | $99/mo | Multi-account, AI targeting, priority support, 10 phones, audit logs |

## Automation Safety Features (All Tiers)

The following safety features are active at ALL tiers — they protect your Instagram account from blocks:

| Feature | What it does |
|---------|-------------|
| **Softban detection** | Monitors empty profiles, repeated action failures, and target open failures. Auto-stops when thresholds are reached (5 empty profiles, 3 blocked actions, 5 open failures). |
| **Hardban detection** | Detects WebView/CAPTCHA screens via `dumpsys activity`. Stops immediately if Instagram is requiring account verification. |
| **Account filtering** | Skip usernames matching blacklist/whitelist patterns, skip business accounts (best-effort), filter by minimum follower count. |
| **Per-source limits** | Caps interactions per target (e.g., max 10 likes per profile) to prevent spamming the same account. |
| **Speed-adaptive pacing** | Measures network speed via shell and adjusts delay ranges — slower network gets longer human-like pauses (7-12s), faster network gets shorter ones (0-1s). Based on Insomniac's sleeper.py logic. |
| **Per-source state tracking** | Tracks likes/follows/comments per target, not just session totals, enabling accurate per-target limit enforcement. |

## Upgrading

### Via the Web Dashboard
1. Open `http://<your-server>:8098/training`
2. Click your profile icon (top-right)
3. Select **"Upgrade Plan"**
4. Choose a tier and confirm payment
5. Tier activates immediately across all connected phones

### Via API
```bash
curl -X POST http://localhost:8098/api/tier/upgrade \
  -H "Content-Type: application/json" \
  -d '{"tier": "plus"}'
```

### Via Termux (on phone)
```bash
termux-open-url "https://lilly.ai/upgrade?plan=plus"
```

## Tier Differences in Detail

### Free Tier
- 2 training runs per day (persona + YOLO combined)
- Basic YOLO object detection
- No Instagram automation (insomnia sessions blocked with clear reason)
- 1 phone connection
- 24-hour data retention
- No webhooks

### Basic Tier
- Unlimited training runs
- YOLO + 80 object classes
- Instagram automation in **intent-only mode** (no tap/swipe; uses am commands)
- 3 phones connections
- 7-day data retention
- Basic webhooks (session started, completed)
- **IG account identity**: specify which @username the bot operates as
- **Basic pacing**: gentle/normal/aggressive pace profiles, hourly breaks, session limits
- **Block detection**: automatic softban indicators (empty profiles, repeated failures, action blocked) and hardban detection (WebView/CAPTCHA) — sessions auto-stop to prevent account damage
- **Account filtering**: skip blacklisted usernames, whitelist-only mode, skip business accounts (best-effort via username patterns)
- **Per-source limits**: cap interactions per target (`max_interactions_per_source`) to avoid over-engaging the same profile
- **Speed-adaptive pacing**: measures network speed and adjusts delay ranges (fast network = faster delays, slow network = longer pauses to appear human)
- **Per-source state tracking**: tracks likes/follows/comments per target, not just session totals

### Plus Tier
- Everything in Basic, plus:
- Full automation with **tap mode** (simulates real touch interactions)
- Auto-follow, auto-comment, auto-story
- 5 phone connections
- 30-day data retention
- A/B testing for training configurations
- Webhooks (all events)
- Custom training prompts
- **All pace profiles unlocked**: aggressive mode, custom delays, flexible breaks

### Pro Tier
- Everything in Plus, plus:
- Multi-account management (up to 10 accounts)
- AI-powered targeting suggestions
- Priority support (24hr response)
- 10 phone connections
- Unlimited data retention
- Audit logs (who did what, when)
- Custom integrations via private API keys

## What Happens When Free Tier Hits Limits

When a Free tier session tries to run:
- **Training**: Queue shows "daily limit reached — upgrade for unlimited"
- **Automation**: Session starts but immediately stops with reason: "Free tier: no interaction capability"
- **Phone connections**: New phone pairing rejected with "tier limit reached"

The system provides clear, actionable error messages at every restriction point.

## Checking Your Current Tier

```bash
# API
curl http://localhost:8098/api/tier/status

# Response
{
  "tier": "basic",
  "limits": {
    "training_runs": "unlimited",
    "phones": 3,
    "automation": "intent-only",
    "data_retention_days": 7
  },
  "usage": {
    "training_runs_today": 5,
    "phones_connected": 2,
    "automation_sessions_today": 12
  }
}
```

## Payment & Billing

- Monthly billing via Stripe (card, PayPal, crypto)
- Annual billing: 2 months free
- Prorated upgrades (pay difference)
- Downgrades take effect at next billing cycle
- Refunds within 14 days, no questions asked

## Contact

- Support: support@lilly.ai
- Discord: https://discord.gg/lilly-ai
- Docs: https://docs.lilly.ai
