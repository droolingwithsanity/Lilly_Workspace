# Lilly AI — Chat Routing Flow (Branch Analysis)

## Overview

The web UI (port 8098) has **3 distinct message routing paths** that create the
speech bubble vs chat window mismatch. This file documents each route so we can
fix the inconsistencies.

---

## ROUTE 1: Text Input (Keyboard)

```
User types message → Enter key
        │
        ▼
   sendReply(text)                    [line 17732]
        │
        ├─► showMainChat()            [switches to chat view]
        ├─► addChatMessage('user', text)  [CHAT WINDOW: shows user msg]
        │
        └─► sendStreamingReply(text)   [line 17599]
                │
                ├─► Creates assistant msgDiv placeholder in CHAT WINDOW
                │
                ├─► Fetch POST /api/cmd_stream (SSE)
                │       │
                │       ├─ token events ──► contentEl.textContent = fullReply
                │       │                    [CHAT WINDOW: streaming tokens]
                │       │
                │       ├─ done event ─────► msgDiv.innerHTML = final text
                │       │                    [CHAT WINDOW: final reply]
                │       │
                │       └─ done event ─────► displaySpeech(finalText)
                │                            [SPEECH BUBBLE: shows reply]
                │
                └─► audio event ──────────► playAudio(audio_id)
                                            [SPEAKER: plays TTS]

STATUS: ✅ CORRECT — Both chat window and speech bubble show same reply.
```

---

## ROUTE 2: Voice Input (Browser Mic)

```
Mic captures audio chunk (1500ms)
        │
        ▼
   POST /api/browser_mic              [line 17766]
   (single round-trip: STT + LLM + TTS)
        │
        ▼
   Returns: { heard, reply, audio_id }
        │
        ├─► showHeard(result.heard)   [line 17769]
        │       └─► addChatMessage('user', heard)  [CHAT WINDOW: user msg]
        │
        ├─► Creates assistant msgDiv  [CHAT WINDOW: reply]
        │   addChatMessage('assistant', result.reply)
        │
        ├─► displaySpeech(result.reply)  [SPEECH BUBBLE: shows reply]
        │
        └─► playAudio(result.audio_id)   [SPEAKER: plays TTS]

STATUS: ✅ CORRECT — Both chat window and speech bubble show same reply.
```

---

## ROUTE 3: Proactive Messages (THE BUG)

```
proactive_suggestion_loop()           [line 10131]
(runs every 2 minutes, 30% chance)
        │
        ▼
   msg = "I noticed you talk a lot about {topic}..."
        │
        ▼
   await speak(msg)                   [line 10196]
        │
        ├─► Sets LAST_SPOKEN = clean  [line 3225+]
        ├─► Generates TTS audio
        ├─► Shows toast on phone
        │
        └─► (nothing added to chat window!)
                │
                ▼
   pollState() polls /api/ui_state    [line 17959, every 600ms]
        │
        ├─► Sees d.spoken !== lastSpoken
        │       └─► displaySpeech(d.spoken)   [SPEECH BUBBLE: shows msg]
        │       └─► playAudio(d.audio_id)     [SPEAKER: plays TTS]
        │
        └─► ❌ NO addChatMessage call!
            ❌ Message NEVER appears in chat window

STATUS: ❌ BROKEN — Speech bubble shows msg, chat window does NOT.
        User sees message appear and disappear with no history.
```

---

## ROUTE 4: Sensor Delta Comments (Same Bug as Route 3)

```
check_sensor_deltas()                [line 3528]
(triggered by light/temp/accel changes)
        │
        ▼
   await speak(msg)                   [same as Route 3]
        │
        ├─► Sets LAST_SPOKEN
        ├─► Generates TTS
        │
        └─► ❌ NO addChatMessage call!

STATUS: ❌ BROKEN — Same issue as Route 3.
```

---

## ROUTE 5: Hive Group Chat

```
User types message → Enter key (hiveActive = true)
        │
        ▼
   sendHiveMessageUnified(text)      [line 16664]
        │
        ├─► addHiveUserMsg(text)      [CHAT WINDOW: user msg]
        │
        └─► Fetch POST /api/group_chat
                │
                └─► addHiveAgentMsg() [CHAT WINDOW: agent replies]
                    ❌ No displaySpeech() call

STATUS: ⚠️ DIFFERENT — Chat window only, no speech bubble.
        This is intentional for group chat mode.
```

---

## ROUTE 6: Vibecode Chat

```
User types in vibecode panel
        │
        ▼
   vcSendMessage(text)               [line 15004]
        │
        ├─► addChatMessage('user', vcRender(text), {rawHtml: true})
        │                                    [CHAT WINDOW: user msg]
        │
        └─► Fetch POST /api/vc_chat
                │
                └─► addChatMessage('assistant', vcRender(reply), {rawHtml: true})
                                    [CHAT WINDOW: reply]
                ❌ No displaySpeech() call

STATUS: ⚠️ DIFFERENT — Chat window only, no speech bubble.
        Intended for vibecode panel.
```

---

## AI Backend Routing

When a message reaches `/api/cmd` or `/api/cmd_stream`:

```
/api/cmd_stream (SSE)
        │
        ▼
   handle_intent(text)               [main intent router]
        │
        ├─► Skill match? ────────────► Execute skill, return result
        │
        ├─► Avatar switch? ──────────► Switch persona, return greeting
        │
        ├─► Delegate to teammate? ───► fox/cat/bear/etc handles it
        │       └─► delegate_to, delegate_name, delegate_emoji in response
        │
        └─► Default: LLM chat ───────► llama_backend.chat_stream()
                                        │
                                        └─► Streams tokens via SSE

The AI backend does NOT prefix "Your response:" anywhere.
The system prompt at line 11450 only mentions it in hypothetical contexts.
```

---

## The Fix Plan

### Fix 1: Reduce proactive spam
- Increase cooldown from 10min → 30min
- Reduce check frequency from 2min → 5min
- Lower random gate from 30% → 15%
- Add smart quiet hours (not just 1am-7am)

### Fix 2: Sync speech bubble with chat window
When `speak()` is called proactively, also add the message to chat window
so the user has a persistent history.

### Fix 3: Investigate "Your response:" text
- Check if it's from the AI reply itself (LLM generating it)
- Check if it's a browser extension
- Check the system prompt for instructions that cause this

---

## File Locations (Current Branch: feature/web-ui-chat-fixes)

| What | File | Lines |
|------|------|-------|
| Proactive loop | lilly_ai.py | 10131-10197 |
| speak() function | lilly_ai.py | 3118-3250+ |
| LAST_SPOKEN global | lilly_ai.py | ~936 |
| pollState() frontend | lilly_ai.py | 17959-18025 |
| displaySpeech() frontend | lilly_ai.py | 16936-16943 |
| sendReply() frontend | lilly_ai.py | 17732-17737 |
| sendStreamingReply() frontend | lilly_ai.py | 17599-17728 |
| addChatMessage() frontend | lilly_ai.py | 15883-15906 |
| KeywordLearner class | lilly_ai.py | 9802-10103 |
| System prompt (no "Your response:") | lilly_ai.py | 11450 |
