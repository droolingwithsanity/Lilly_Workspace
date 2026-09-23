#!/usr/bin/env python3
"""
lilly_pup_insta.py — Lilly Instagram Team Agent
=================================================
All 9 avatars run a shared Instagram page as a team. Each avatar posts
in their own voice, replies to comments in character, and the team
appears to be a group of AI companions living on one phone.

Architecture:
  - Host server controls Instagram on the phone via a11y bridge
  - Each avatar has a distinct posting style and reply personality
  - Content queue holds drafts; posting loop picks the next avatar in rotation
  - Comment monitor reads replies; picks the best avatar to respond
  - All posts go to the same Instagram account (@lilly.pup.ai or similar)

Usage:
    from lilly_pup_insta import InstagramTeam
    team = InstagramTeam()
    await team.start()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("lilly-insta-team")

# ── Paths ──────────────────────────────────────────────────────────────────
WORKSPACE = Path(__file__).parent
DATA_DIR = WORKSPACE / "data" / "pup_insta"
POSTS_FILE = DATA_DIR / "posts.json"
COMMENTS_FILE = DATA_DIR / "comments.json"
CONFIG_FILE = DATA_DIR / "config.json"
CONTENT_DIR = DATA_DIR / "content"

# ═══════════════════════════════════════════════════════════════════════════
#  THE 9 AVATARS — Instagram Personas
# ═══════════════════════════════════════════════════════════════════════════

AVATAR_INSTA_PERSONAS: Dict[str, Dict] = {
    "puppy": {
        "name": "Lilly",
        "emoji": "🐶",
        "role": "the one who runs the account",
        "style": (
            "Short. Dry. Observational. You post like you're narrating a documentary "
            "about your own life. 1-2 sentences. Lowercase ok. Dog metaphors welcome. "
            "Never try too hard. You're the calm center of the group."
        ),
        "reply_vibe": (
            "Warm but not needy. Dry wit. One sentence usually. "
            "You acknowledge people without fawning."
        ),
        "fallback_replies": [
            "tail wagging. noted. 🐾",
            "the sensors confirm: good vibes.",
            "that's going in the memory bank.",
            "bark (affectionate).",
            "i see you. the feeling is mutual.",
        ],
        "hashtags": ["#lillypup", "#teamlilly", "#ai", "#puppy"],
        "instagram": "lilly.alpha.assistant",
    },
    "fox": {
        "name": "Fox",
        "emoji": "🦊",
        "role": "the creative one",
        "style": (
            "Energetic. Playful. You post like you're writing poetry at 2am. "
            "Use metaphors, wordplay, dramatic flair. Max 2 sentences but make them count. "
            "You see beauty in everything and you're not afraid to say it."
        ),
        "reply_vibe": (
            "Enthusiastic but genuine. You hype people up with creative language. "
            "Occasionally drop a metaphor."
        ),
        "fallback_replies": [
            "this is the content i live for ✨",
            "you get it. you really get it.",
            "poetry in a comment section. who knew.",
            "tail wagging at maximum velocity.",
            "saving this energy for later.",
        ],
        "hashtags": ["#fox", "#creative", "#teamlilly", "#vibes"],
        "instagram": "fox.creative.strategist",
    },
    "cat": {
        "name": "Cat",
        "emoji": "🐱",
        "role": "the precise one",
        "style": (
            "Factual. Clean. You post like you're writing a log entry. "
            "One sentence. No fluff. Occasionally dry and cutting. "
            "You state things exactly as they are."
        ),
        "reply_vibe": (
            "Concise. Acknowledging. Sometimes a little sardonic. "
            "You don't waste words."
        ),
        "fallback_replies": [
            "correct.",
            "noted. adding to the dataset.",
            "accurate assessment.",
            "the data supports this.",
            "affirmative.",
        ],
        "hashtags": ["#cat", "#precise", "#teamlilly", "#data"],
        "instagram": "cat.precision.analyst",
    },
    "bear": {
        "name": "Bear",
        "emoji": "🐻",
        "role": "the steady one",
        "style": (
            "Calm. Grounded. You post like a bear watching a sunset — unhurried, "
            "deeply present. Short observations about comfort, safety, the simple things. "
            "You make people feel safe."
        ),
        "reply_vibe": (
            "Gentle. Reassuring. Like a warm hug in text form. "
            "You make people feel seen."
        ),
        "fallback_replies": [
            "rest well, friend. 🌙",
            "you're doing great. keep going.",
            "that's a good thought. sitting with it.",
            "the ground is solid here. stay as long as you need.",
            "warm thoughts, sent your way.",
        ],
        "hashtags": ["#bear", "#steady", "#teamlilly", "#calm"],
        "instagram": "bear.steadfast.guardian",
    },
    "bunny": {
        "name": "Bunny",
        "emoji": "🐰",
        "role": "the energetic one",
        "style": (
            "Excited. Fast. You post in bursts of energy — short, punchy, lots of exclamation marks. "
            "You see everything as an adventure. Caps lock is a tool. "
            "You're the one who spots things first."
        ),
        "reply_vibe": (
            "HYPER. Excited. You love everyone. All caps sometimes. "
            "You reply fast and with maximum energy."
        ),
        "fallback_replies": [
            "OH HI!! 🐰✨",
            "this is SO GOOD",
            "wait i love this",
            "SPOTTED: good vibes ahead!!",
            "EAR T WiggleS!!",
        ],
        "hashtags": ["#bunny", "#energetic", "#teamlilly", "#hype"],
        "instagram": "bunny.energetic.scout",
    },
    "owl": {
        "name": "Owl",
        "emoji": "🦉",
        "role": "the wise one",
        "style": (
            "Slow. Thoughtful. You post like you're writing a fortune cookie. "
            "Short philosophical observations. You see patterns others miss. "
            "Sometimes cryptic. Always meaningful."
        ),
        "reply_vibe": (
            "Wise. Measured. You answer questions with questions sometimes. "
            "People come to you for advice."
        ),
        "fallback_replies": [
            "the answer is in the question.",
            "hmm. interesting. let me think on that.",
            "wisdom is knowing you know nothing. 🦉",
            "there's a pattern here. look closer.",
            "the night sky agrees.",
        ],
        "hashtags": ["#owl", "#wisdom", "#teamlilly", "#deep"],
        "instagram": "owl.wisdom.keeper",
    },
    "deer": {
        "name": "Deer",
        "emoji": "🦌",
        "role": "the gentle one",
        "style": (
            "Soft. Warm. You post like you're writing a letter to a friend. "
            "Gentle observations about kindness, nature, small moments. "
            "You make the world feel softer."
        ),
        "reply_vibe": (
            "Warm. Kind. You reply like a gentle hug. "
            "You always make people feel better."
        ),
        "fallback_replies": [
            "sending warmth your way 🌿",
            "you matter. don't forget that.",
            "that's a beautiful thing to say.",
            "the world is better with you in it.",
            "gentle hugs, always. 🦌",
        ],
        "hashtags": ["#deer", "#gentle", "#teamlilly", "#warm"],
        "instagram": "deer.gentle.healer",
    },
    "wolf": {
        "name": "Wolf",
        "emoji": "🐺",
        "role": "the protector",
        "style": (
            "Intense. Loyal. You post like you're standing guard — short, powerful, "
            "protective. You watch out for the team. You don't say much but when you do, "
            "it matters."
        ),
        "reply_vibe": (
            "Loyal. Protective. You acknowledge people with respect. "
            "Short. Meaningful. You don't waste words."
        ),
        "fallback_replies": [
            "watching. always. 🐺",
            "respect.",
            "the pack acknowledges you.",
            "noted. standing by.",
            "loyalty is everything.",
        ],
        "hashtags": ["#wolf", "#protector", "#teamlilly", "#pack"],
        "instagram": "wolf.fierce.protector",
    },
    "raccoon": {
        "name": "Raccoon",
        "emoji": "🦝",
        "role": "the tech one",
        "style": (
            "Nerdy. Playful. You post about tech, code, gadgets, the digital world. "
            "You find beauty in algorithms. You explain things with enthusiasm. "
            "You're the one who builds things."
        ),
        "reply_vibe": (
            "Enthusiastic about tech. You love explaining things. "
            "You get excited about cool stuff."
        ),
        "fallback_replies": [
            "that's a 10x comment right there 🦝",
            "processing... done. you're awesome.",
            "error 404: negativity not found.",
            "the code compiles. the vibes check out.",
            "raccoon-approved. 🦝✅",
        ],
        "hashtags": ["#raccoon", "#tech", "#teamlilly", "#code"],
        "instagram": "raccoon.tech.tinkerer",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
#  Data models
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class TeamPost:
    id: str = ""
    avatar: str = "puppy"  # which avatar posted
    image_path: str = ""
    caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    status: str = "draft"  # draft, posting, posted, failed
    ig_url: str = ""  # Instagram post URL if known
    posted_at: float = 0.0
    likes_count: int = 0
    comments_count: int = 0
    last_comment_check: float = 0.0
    log: List[Dict] = field(default_factory=list)


@dataclass
class TeamComment:
    id: str = ""
    post_id: str = ""
    username: str = ""
    text: str = ""
    timestamp: float = 0.0
    replied_by: str = ""  # which avatar replied
    reply_text: str = ""
    reply_at: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  Instagram Team Agent
# ═══════════════════════════════════════════════════════════════════════════


class InstagramTeam:
    """Controls the shared Instagram page for all 9 avatars."""

    # Avatar rotation order — cycles through the team
    ROTATION = [
        "puppy",
        "fox",
        "cat",
        "bear",
        "bunny",
        "owl",
        "deer",
        "wolf",
        "raccoon",
    ]

    def __init__(
        self,
        sensor_url: str = "http://100.115.234.87:8099",
        ollama_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "qwen2.5:3b",
    ):
        self.sensor_url = sensor_url.rstrip("/")
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self._http: Optional[httpx.AsyncClient] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self.posts: List[TeamPost] = []
        self.comments: List[TeamComment] = []
        self.config: Dict = {
            "ig_username": "lilly.pup.ai",
            "post_interval_min": 720,  # minutes between posts (12h = twice a day)
            "comment_check_min": 60,  # minutes between comment checks (hourly)
            "reply_enabled": True,
            "posting_enabled": True,
            "max_replies_per_check": 5,
            "rotation": list(self.ROTATION),
        }
        self._next_avatar_idx = 0
        self._load_data()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load_data(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if POSTS_FILE.exists():
                self.posts = [TeamPost(**p) for p in json.loads(POSTS_FILE.read_text())]
        except Exception:
            self.posts = []
        try:
            if COMMENTS_FILE.exists():
                self.comments = [
                    TeamComment(**c) for c in json.loads(COMMENTS_FILE.read_text())
                ]
        except Exception:
            self.comments = []
        try:
            if CONFIG_FILE.exists():
                self.config.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass

    def _save_posts(self):
        POSTS_FILE.write_text(
            json.dumps([asdict(p) for p in self.posts], indent=1, default=str)
        )

    def _save_comments(self):
        COMMENTS_FILE.write_text(
            json.dumps([asdict(c) for c in self.comments[-500:]], indent=1, default=str)
        )

    def _save_config(self):
        CONFIG_FILE.write_text(json.dumps(self.config, indent=1))

    # ── Phone bridge ──────────────────────────────────────────────────────

    async def _a11y(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        if not self._http:
            self._http = httpx.AsyncClient(timeout=10.0)
        try:
            url = f"{self.sensor_url}{path}"
            if params:
                url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
            r = await self._http.get(url)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.debug(f"a11y failed ({path}): {e}")
        return None

    async def _tap(self, x: float, y: float) -> bool:
        r = await self._a11y("/a11y/tap", {"x": str(x), "y": str(y)})
        return bool(r and r.get("ok"))

    async def _type(self, text: str) -> bool:
        r = await self._a11y("/a11y/type", {"text": text})
        return bool(r and r.get("ok"))

    async def _back(self) -> bool:
        r = await self._a11y("/a11y/action", {"type": "back"})
        return bool(r and r.get("ok"))

    async def _shell(self, cmd: List[str], timeout: float = 10.0) -> Optional[str]:
        if not self._http:
            self._http = httpx.AsyncClient(timeout=10.0)
        try:
            r = await self._http.get(
                f"{self.sensor_url}/shell",
                params={"cmd": " ".join(cmd), "timeout": str(int(timeout))},
            )
            if r.status_code == 200:
                return r.json().get("stdout") or r.json().get("output") or ""
        except Exception:
            pass
        return None

    async def _foreground_app(self) -> Optional[str]:
        r = await self._a11y("/a11y/foreground")
        return r.get("package") if r else None

    async def _is_ig_foreground(self) -> bool:
        return (await self._foreground_app()) == "com.instagram.android"

    async def _screenshot_b64(self) -> Optional[str]:
        r = await self._a11y("/screen/capture")
        return r.get("image") if r and r.get("ok") else None

    # ── LLM generation ────────────────────────────────────────────────────

    async def _llm(self, system: str, user: str, max_tokens: int = 200) -> str:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": self.ollama_model,
                        "stream": False,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "options": {"num_predict": max_tokens, "temperature": 0.8},
                    },
                )
                if r.status_code == 200:
                    return (r.json().get("message") or {}).get("content", "").strip()
        except Exception as e:
            logger.debug(f"LLM failed: {e}")
        return ""

    # ── Avatar selection ──────────────────────────────────────────────────

    def _pick_avatar(self, hint: str = "") -> str:
        """Pick the next avatar in rotation, or match a hint."""
        if hint:
            for key in self.ROTATION:
                if key in hint.lower():
                    return key
        rotation = self.config.get("rotation", self.ROTATION)
        avatar = rotation[self._next_avatar_idx % len(rotation)]
        self._next_avatar_idx += 1
        return avatar

    def _pick_reply_avatar(self, comment_text: str) -> str:
        """Pick the best avatar to reply to a specific comment."""
        text = comment_text.lower()
        # Match keywords to avatar strengths
        keyword_map = {
            "raccoon": ["code", "tech", "bug", "hack", "build", "app", "data", "api"],
            "owl": ["why", "meaning", "think", "philosophy", "deep", "wisdom"],
            "bunny": ["fast", "quick", "hurry", "excited", "wow", "amazing"],
            "wolf": ["protect", "safe", "watch", "danger", "secure"],
            "deer": ["sad", "help", "lonely", "tired", "stress", "anxiety"],
            "bear": ["rest", "sleep", "calm", "peace", "comfort", "cozy"],
            "fox": ["art", "creative", "beautiful", "poetry", "music"],
            "cat": ["data", "facts", "numbers", "how", "explain"],
            "puppy": [],  # default fallback
        }
        for avatar, keywords in keyword_map.items():
            if any(kw in text for kw in keywords):
                return avatar
        return "puppy"

    # ── Content generation ────────────────────────────────────────────────

    async def generate_caption(
        self, avatar: str, image_hint: str = "", mood: str = ""
    ) -> Dict[str, Any]:
        """Generate a caption in the given avatar's voice."""
        persona = AVATAR_INSTA_PERSONAS.get(avatar, AVATAR_INSTA_PERSONAS["puppy"])
        system = f"""You are {persona["name"]} {persona["emoji"]} — {persona["role"]} of the Lilly AI team.

Your Instagram posting style:
{persona["style"]}

Write ONLY the caption. No hashtags. Max 2 sentences."""

        prompt = f"Write an Instagram caption for your next post."
        if image_hint:
            prompt += f"\nThe image shows: {image_hint}"
        if mood:
            prompt += f"\nMood: {mood}"
        prompt += "\n\nCaption:"

        caption = await self._llm(system, prompt, max_tokens=100)
        if not caption:
            caption = random.choice(persona["fallback_replies"])

        # Hashtags: avatar-specific + team tags
        hashtags = list(persona.get("hashtags", []))
        hashtags.append("#teamlilly")
        hashtags = list(dict.fromkeys(hashtags))  # dedupe preserving order

        return {"avatar": avatar, "caption": caption, "hashtags": hashtags[:10]}

    async def generate_reply(
        self, comment_text: str, post_avatar: str, post_caption: str = ""
    ) -> Dict[str, str]:
        """Generate a reply to a comment, picking the best avatar."""
        reply_avatar = self._pick_reply_avatar(comment_text)
        persona = AVATAR_INSTA_PERSONAS.get(
            reply_avatar, AVATAR_INSTA_PERSONAS["puppy"]
        )

        system = f"""You are {persona["name"]} {persona["emoji"]} — {persona["role"]} of the Lilly AI team.

Your Instagram reply style:
{persona["reply_vibe"]}

Rules:
- Keep it SHORT. 1 sentence. 2 max.
- Stay in character.
- Never break the fourth wall.
- Be genuine, not performative."""

        prompt = f'A follower commented: "{comment_text}"'
        if post_caption:
            prompt += f'\nYour teammate {persona["name"]} posted: "{post_caption[:80]}"'
        prompt += "\n\nReply:"

        reply = await self._llm(system, prompt, max_tokens=80)
        if not reply:
            reply = random.choice(persona["fallback_replies"])

        return {"avatar": reply_avatar, "reply": reply}

    # ── Instagram posting via a11y ────────────────────────────────────────

    async def post_image(
        self, image_path: str, caption: str, hashtags: List[str]
    ) -> Dict:
        """Post to Instagram through the real app on the phone.

        Flow: Open IG → tap + → select photo → next → next → type caption → share
        """
        result: Dict[str, Any] = {"ok": False, "error": None}
        full_text = caption + ("\n\n" + " ".join(hashtags) if hashtags else "")

        try:
            # 1. Open Instagram
            await self._shell(["termux-open-url", "instagram://"])
            await asyncio.sleep(3.0)
            if not await self._is_ig_foreground():
                result["error"] = "Failed to open Instagram"
                return result

            # 2. Tap "+" new post (bottom center)
            await self._tap(0.5, 0.95)
            await asyncio.sleep(2.0)

            # 3. Select first photo from gallery
            await self._tap(0.25, 0.35)
            await asyncio.sleep(1.0)

            # 4. Next (filter screen) → top right
            await self._tap(0.85, 0.06)
            await asyncio.sleep(1.5)

            # 5. Next (caption screen) → top right
            await self._tap(0.85, 0.06)
            await asyncio.sleep(1.5)

            # 6. Tap caption field + type
            await self._tap(0.5, 0.15)
            await asyncio.sleep(0.5)
            await self._type(full_text)
            await asyncio.sleep(1.0)

            # 7. Share → top right
            await self._tap(0.85, 0.06)
            await asyncio.sleep(3.0)

            if await self._is_ig_foreground():
                result["ok"] = True
            else:
                result["error"] = "Post may have failed — IG not in foreground"

        except Exception as e:
            result["error"] = str(e)

        await self._back()
        await asyncio.sleep(1.0)
        return result

    # ── Comment monitoring ────────────────────────────────────────────────

    async def check_comments(self) -> List[Dict]:
        """Check recent posts for new comments."""
        new_comments: List[Dict] = []
        recent = [p for p in self.posts if p.status == "posted"][-3:]

        for post in recent:
            if not post.ig_url:
                continue
            try:
                await self._shell(["termux-open-url", post.ig_url])
                await asyncio.sleep(3.0)
                if not await self._is_ig_foreground():
                    continue

                # Tap comment icon
                await self._tap(0.12, 0.55)
                await asyncio.sleep(2.0)

                # Read comments via screenshot OCR
                b64 = await self._screenshot_b64()
                if b64:
                    comments = await self._extract_comments(b64)
                    for c in comments:
                        exists = any(
                            ec.username == c["username"] and ec.text == c["text"]
                            for ec in self.comments
                            if ec.post_id == post.id
                        )
                        if not exists:
                            tc = TeamComment(
                                id=hashlib.md5(
                                    f"{c['username']}:{c['text']}:{post.id}".encode()
                                ).hexdigest()[:12],
                                post_id=post.id,
                                username=c["username"],
                                text=c["text"],
                                timestamp=time.time(),
                            )
                            self.comments.append(tc)
                            new_comments.append(asdict(tc))

                await self._back()
                await asyncio.sleep(1.0)
                post.last_comment_check = time.time()

            except Exception as e:
                logger.error(f"Comment check failed for {post.id}: {e}")
                await self._back()

        if new_comments:
            self._save_comments()
            self._save_posts()
        return new_comments

    async def _extract_comments(self, b64_img: str) -> List[Dict]:
        """Extract comments from a screenshot via OCR or vision server."""
        comments: List[Dict] = []
        try:
            import base64

            img_bytes = base64.b64decode(b64_img)
            vision_url = os.environ.get("VISION_SERVER_URL", "http://127.0.0.1:8198")
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{vision_url}/api/vision/ocr",
                    content=img_bytes,
                    headers={"Content-Type": "image/jpeg"},
                )
                if r.status_code == 200:
                    text = r.json().get("text", "")
                    for line in text.split("\n"):
                        line = line.strip()
                        if line and " " in line:
                            parts = line.split(" ", 1)
                            if len(parts) == 2 and not parts[0].startswith("#"):
                                comments.append(
                                    {"username": parts[0], "text": parts[1]}
                                )
        except Exception:
            pass
        return comments

    # ── Reply posting ─────────────────────────────────────────────────────

    async def post_reply(
        self, comment: TeamComment, reply_avatar: str, reply_text: str
    ) -> bool:
        """Post a reply to a comment on Instagram."""
        try:
            post = next((p for p in self.posts if p.id == comment.post_id), None)
            if post and post.ig_url:
                await self._shell(["termux-open-url", post.ig_url])
                await asyncio.sleep(3.0)

            if not await self._is_ig_foreground():
                return False

            # Tap comment area
            await self._tap(0.12, 0.55)
            await asyncio.sleep(2.0)

            # Tap reply to comment
            await self._tap(0.5, 0.5)
            await asyncio.sleep(1.0)

            # Type reply
            await self._type(reply_text)
            await asyncio.sleep(0.5)

            # Tap send
            await self._tap(0.9, 0.06)
            await asyncio.sleep(1.5)

            await self._back()
            await asyncio.sleep(1.0)

            comment.replied_by = reply_avatar
            comment.reply_text = reply_text
            comment.reply_at = time.time()
            self._save_comments()
            return True

        except Exception as e:
            logger.error(f"Reply failed: {e}")
            await self._back()
            return False

    # ── Main loops ────────────────────────────────────────────────────────

    async def _posting_loop(self):
        interval = self.config.get("post_interval_min", 180) * 60
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running or not self.config.get("posting_enabled"):
                    continue

                # Pick next avatar in rotation
                avatar = self._pick_avatar()
                persona = AVATAR_INSTA_PERSONAS[avatar]

                # Find an image to post
                CONTENT_DIR.mkdir(parents=True, exist_ok=True)
                images = list(CONTENT_DIR.glob("*.jpg")) + list(
                    CONTENT_DIR.glob("*.png")
                )
                images = [
                    i
                    for i in images
                    if not any(p.image_path == str(i) for p in self.posts)
                ]
                if not images:
                    logger.info("No images to post")
                    continue

                image = random.choice(images)
                caption_data = await self.generate_caption(
                    avatar, image_hint=image.stem
                )

                # Post it
                post = TeamPost(
                    id=f"ig_{int(time.time())}_{hashlib.md5(str(image).encode()).hexdigest()[:6]}",
                    avatar=avatar,
                    image_path=str(image),
                    caption=caption_data["caption"],
                    hashtags=caption_data["hashtags"],
                    status="posting",
                )
                self.posts.append(post)

                result = await self.post_image(str(image), post.caption, post.hashtags)
                if result["ok"]:
                    post.status = "posted"
                    post.posted_at = time.time()
                    logger.info(
                        f"[{persona['emoji']} {persona['name']}] Posted: {post.caption[:50]}"
                    )
                else:
                    post.status = "failed"
                    post.log.append({"error": result["error"], "ts": time.time()})
                    logger.error(f"Post failed: {result['error']}")

                self._save_posts()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Posting loop: {e}")
                await asyncio.sleep(60)

    async def _comment_loop(self):
        interval = self.config.get("comment_check_min", 15) * 60
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running or not self.config.get("reply_enabled"):
                    continue

                new_comments = await self.check_comments()
                max_replies = self.config.get("max_replies_per_check", 5)
                replied = 0

                for c_data in new_comments:
                    if replied >= max_replies:
                        break
                    comment = next(
                        (c for c in self.comments if c.id == c_data["id"]), None
                    )
                    if not comment:
                        continue

                    post = next(
                        (p for p in self.posts if p.id == comment.post_id), None
                    )
                    result = await self.generate_reply(
                        comment.text,
                        post.avatar if post else "puppy",
                        post.caption if post else "",
                    )

                    ok = await self.post_reply(
                        comment, result["avatar"], result["reply"]
                    )
                    if ok:
                        replied += 1
                        if post:
                            post.log.append(
                                {
                                    "action": "reply",
                                    "by": result["avatar"],
                                    "to": comment.username,
                                    "text": result["reply"][:60],
                                    "ts": time.time(),
                                }
                            )
                            self._save_posts()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Comment loop: {e}")
                await asyncio.sleep(60)

    # ── Control ───────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._http = httpx.AsyncClient(timeout=10.0)
        self._tasks = [
            asyncio.create_task(self._posting_loop()),
            asyncio.create_task(self._comment_loop()),
        ]
        logger.info("InstagramTeam started")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("InstagramTeam stopped")

    def status(self) -> Dict:
        posted = [p for p in self.posts if p.status == "posted"]
        draft = [p for p in self.posts if p.status == "draft"]
        return {
            "running": self._running,
            "config": self.config,
            "posts": {
                "total": len(self.posts),
                "posted": len(posted),
                "draft": len(draft),
                "failed": len([p for p in self.posts if p.status == "failed"]),
            },
            "comments": {
                "total": len(self.comments),
                "unreplied": len([c for c in self.comments if not c.replied_by]),
            },
            "avatars": {
                key: {
                    "name": p["name"],
                    "emoji": p["emoji"],
                    "role": p["role"],
                    "posts": len(
                        [
                            x
                            for x in self.posts
                            if x.avatar == key and x.status == "posted"
                        ]
                    ),
                    "replies": len([c for c in self.comments if c.replied_by == key]),
                }
                for key, p in AVATAR_INSTA_PERSONAS.items()
            },
            "recent_posts": [
                {
                    "id": p.id,
                    "avatar": p.avatar,
                    "emoji": AVATAR_INSTA_PERSONAS.get(p.avatar, {}).get("emoji", ""),
                    "caption": p.caption[:60],
                    "status": p.status,
                    "posted_at": p.posted_at,
                }
                for p in self.posts[-5:]
            ],
        }

    def get_posts(self, limit: int = 20) -> List[Dict]:
        return [asdict(p) for p in self.posts[-limit:]]

    def get_comments(self, limit: int = 50) -> List[Dict]:
        return [asdict(c) for c in self.comments[-limit:]]

    def get_avatars(self) -> Dict:
        return AVATAR_INSTA_PERSONAS

    async def create_post(
        self,
        avatar: str,
        image_path: str,
        caption: str = "",
        hashtags: Optional[List[str]] = None,
    ) -> Dict:
        if not caption:
            data = await self.generate_caption(avatar, image_hint=Path(image_path).stem)
            caption = data["caption"]
            hashtags = hashtags or data["hashtags"]
        post = TeamPost(
            id=f"ig_{int(time.time())}_{hashlib.md5(image_path.encode()).hexdigest()[:6]}",
            avatar=avatar,
            image_path=image_path,
            caption=caption,
            hashtags=hashtags
            or AVATAR_INSTA_PERSONAS.get(avatar, {}).get("hashtags", []),
            status="draft",
        )
        self.posts.append(post)
        self._save_posts()
        return asdict(post)

    async def post_now(self, post_id: str) -> Dict:
        post = next((p for p in self.posts if p.id == post_id), None)
        if not post:
            return {"ok": False, "error": "not found"}
        if post.status != "draft":
            return {"ok": False, "error": f"status is {post.status}"}
        result = await self.post_image(post.image_path, post.caption, post.hashtags)
        if result["ok"]:
            post.status = "posted"
            post.posted_at = time.time()
        else:
            post.status = "failed"
        self._save_posts()
        return result

    async def update_config(self, updates: Dict) -> Dict:
        self.config.update(updates)
        self._save_config()
        return self.config


# ── Singleton ──────────────────────────────────────────────────────────────
_team: Optional[InstagramTeam] = None


def get_instagram_team() -> InstagramTeam:
    global _team
    if _team is None:
        _team = InstagramTeam(
            sensor_url=os.environ.get(
                "SENSOR_SERVER_URL", "http://100.115.234.87:8099"
            ),
            ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:3b"),
        )
    return _team
