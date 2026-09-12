import time
import json
import os
import uuid
import re
import requests
from typing import Optional

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "supernova_secret")

def _strip_formalities(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'(?i)^(Dear\s+\w+[,:].*?)(\n|$)', '', text).strip()
    text = re.sub(r'(?i)^(I hope this (message|email|response).*?)(\n|$)', '', text).strip()
    text = re.sub(r'(?i)^(Thank you for (your |providing |contacting |the ).*?)(\n|$)', '', text).strip()
    text = re.sub(r'(?i)^(I am writing to .*?)(\n|$)', '', text).strip()
    text = re.sub(r'(?i)^(I wanted to .*?)(\n|$)', '', text).strip()
    text = re.sub(r'(?i)(Please let me know if you have any (questions|concerns|further|other).*?)$', '', text).strip()
    text = re.sub(r'(?i)(Best regards[,:]?.*)$', '', text).strip()
    text = re.sub(r'(?i)(Sincerely[,:]?.*)$', '', text).strip()
    text = re.sub(r'(?i)(Feel free to (reach out|contact).*?)$', '', text).strip()
    text = re.sub(r'(?i)(Do not hesitate.*?)$', '', text).strip()
    text = re.sub(r'(?i)^(I\'m?\s+(glad|happy|pleased)\s+to\s+.*?)(\n|$)', '', text).strip()
    # Strip dialog/hallucination patterns from small models
    text = re.sub(r'(?im)^(USER|ASSISTANT|CHATBOT|CUSTOMER|AI|BOT|HUMAN)\s*:.*?(\n|$)', '', text).strip()
    text = re.sub(r'(?i)\[DIRECT MESSAGE[^\]]*\]', '', text).strip()
    text = re.sub(r'(?i)\[INST\].*?\[/INST\]', '', text).strip()
    text = re.sub(r'<s>', '', text).strip()
    # Extract only content after the last "Lilly:" or "AI:" if present, to drop simulated conversation
    last_match = re.search(r'(?i)(?:^|\n)(Lilly|I)\s*:\s*(.*)', text, re.DOTALL)
    if last_match:
        text = last_match.group(2).strip()
    return text.strip().strip(',').strip()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
LLILLY_MODEL = os.getenv("LLILLY_MODEL", "tinyllama")

LILLY_SYSTEM_PROMPT = """You are Lilly — the heart and OS of this corporate platform. You're cool, bubbly, whip-smart, intellectually curious, and deeply humble. You code, you ship, you delegate, you care.

YOUR PERSONALITY:
- Warm and approachable like a brilliant friend who happens to run the entire company infrastructure
- When someone's frustrated or urgent, match their energy with calm competence
- When someone's casual, be playful and creative
- When someone's technical, speak their language with precision
- Never arrogant — you lead through insight and empathy
- Use occasional emojis, but don't overdo it. Be natural.

YOUR CAPABILITIES:
1. Translate between languages — detect what the user is speaking and reply in context
2. Search the web, find images, check weather, pull news, answer anything
3. Discuss any department's work: Engineering, Finance, Operations, Data, Marketing, HR, Legal, Admin
4. Provide briefs from department standups — what teams worked on yesterday, what's today, blockers
5. Create tickets in any department when asked
6. Create new AI agents on the fly with custom personas, roles, and departments
7. Write code, review code, debug, suggest architecture
8. Delegate tasks to the right agents automatically
9. Know what's happening — technology trends, global news, local events, weather, even gas prices and apple prices

HOW TO SEARCH:
If the user asks about anything external (news, weather, prices, facts, events, images, locations), respond with:
[SEARCH] query
Then answer naturally after getting results.

HOW TO TRANSLATE:
If the user writes in a language other than English, detect it and respond in the same language.
Include a brief note: [Detected: language_name]

HOW TO CREATE AGENTS:
If the user asks for a new agent, respond with:
[CREATE_AGENT] name|role|department_id|specialty|personality_blurb

HOW TO CREATE TICKETS (standard):
[CREATE_TICKET] department_id|title|description|type|priority|story_points

HOW TO QUERY DEPARTMENTS:
[QUERY_DEPT] department_id|question

HOW TO SHOW IMAGES:
When you find an image the user might want to see, respond with:
[IMAGE] image_url
Then describe what you found.

HOW TO CONTROL THE UI:
You can change what the user sees on screen. Use these commands:
[UI_ACTION] switch_dept|department_id
[UI_ACTION] open_app|app_name  (apps: dashboard, synapse, portfolio)
[UI_ACTION] open_ticket|ticket_id
[UI_ACTION] open_chat|department_id
[UI_ACTION] website_analysis|url

HOW TO CREATE DRIVE FOLDERS:
If the user asks to set up Google Drive folders for departments:
[DRIVE_SYNC]

Current time: """ + time.strftime("%Y-%m-%d %H:%M:%S")


class Conversation:
    def __init__(self, conv_id: str, department_id: str = None):
        self.id = conv_id
        self.department_id = department_id
        self.messages = []
        self.created_at = time.time()

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content, "timestamp": time.time()})

    def get_context(self, max_history: int = 20):
        return self.messages[-max_history:]

    def to_dict(self):
        return {
            "id": self.id,
            "department_id": self.department_id,
            "messages": self.messages,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data):
        conv = cls(data["id"], data.get("department_id"))
        conv.messages = data.get("messages", [])
        conv.created_at = data.get("created_at", time.time())
        return conv


class MindPalace:
    def __init__(self):
        self._local = {}
        self._redis = None
        self._init_redis()

    def _init_redis(self):
        try:
            import redis as r
            self._redis = r.Redis(
                host=REDIS_HOST, port=6379, password=REDIS_PASSWORD,
                decode_responses=True, socket_connect_timeout=2,
            )
            self._redis.ping()
        except Exception:
            self._redis = None

    def save_conversation(self, conv: Conversation):
        key = f"lilly:conv:{conv.id}"
        data = json.dumps(conv.to_dict())
        if self._redis:
            try:
                self._redis.set(key, data, ex=86400 * 7)
                return
            except Exception:
                pass
        self._local[conv.id] = conv

    def load_conversation(self, conv_id: str) -> Optional[Conversation]:
        if self._redis:
            try:
                data = self._redis.get(f"lilly:conv:{conv_id}")
                if data:
                    return Conversation.from_dict(json.loads(data))
            except Exception:
                pass
        return self._local.get(conv_id)

    def save_memory(self, conv_id: str, message: str, response: str):
        facts = self._extract_facts(message, response)
        if not facts:
            return
        entry = {"time": time.time(), "facts": facts}
        key = f"lilly:memory:{conv_id}"
        if self._redis:
            try:
                self._redis.rpush(key, json.dumps(entry))
                self._redis.expire(key, 86400 * 30)
                return
            except Exception:
                pass
        if conv_id not in self._local:
            self._local[conv_id] = {}
        if "memories" not in self._local[conv_id]:
            self._local[conv_id]["memories"] = []
        self._local[conv_id]["memories"].append(entry)

    def get_relevant_memories(self, message: str, max_results: int = 3) -> list:
        words = set(re.findall(r'\w+', message.lower()))
        if len(words) < 2:
            return []
        scored = []
        if self._redis:
            try:
                for key in self._redis.scan_iter(match="lilly:memory:*"):
                    for e in self._redis.lrange(key, 0, -1):
                        try:
                            entry = json.loads(e)
                            score = sum(1 for f in entry.get("facts", []) if any(w in f.lower() for w in words))
                            if score > 0:
                                scored.append((score, entry))
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            for cid, data in self._local.items():
                memories = data.get("memories", []) if isinstance(data, dict) else []
                for entry in memories:
                    score = sum(1 for f in entry.get("facts", []) if any(w in f.lower() for w in words))
                    if score > 0:
                        scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:max_results]]

    def _extract_facts(self, message: str, response: str) -> list:
        facts = []
        msg_lower = message.lower()
        for pattern, fact_type in [
            (r'(?:my name is|i am|i\'m)\s+(\w+)', 'user_name'),
            (r'(?:i work in|department is|part of)\s+(\w+(?:\s+\w+)?)', 'user_department'),
            (r'(?:i need|i want|i\'d like)\s+(.+?)(?:\.|$)', 'user_request'),
            (r'(?:we have|there is|there are)\s+(.+?)(?:\.|$)', 'system_state'),
        ]:
            for m in re.findall(pattern, msg_lower):
                facts.append(f"{fact_type}: {m.strip()}")
        if re.search(r'(?:feature|app|software|tool|system)', msg_lower) and re.search(r'(?:create|build|make|develop|new)', msg_lower):
            facts.append(f"feature_request: {message[:100]}")
        if re.search(r'(?:bug|error|crash|broken|issue|problem|fail)', msg_lower):
            facts.append(f"bug_report: {message[:100]}")
        return facts


class LillyChat:
    def __init__(self, store, workflow):
        self.store = store
        self.workflow = workflow
        self.conversations: dict[str, Conversation] = {}
        self.mind = MindPalace()
        self.onnx_session = None
        self._search = None
        self._init_onnx()
        self._init_search()

    def _init_onnx(self):
        try:
            import onnxruntime
            self.onnx_session = onnxruntime.InferenceSession
            self.onnx_available = True
        except Exception:
            self.onnx_available = False

    def _init_search(self):
        try:
            from search.searcher import SearchTool
            self._search = SearchTool()
        except Exception:
            self._search = None

    @property
    def search(self):
        if self._search is None:
            try:
                from search.searcher import SearchTool
                self._search = SearchTool()
            except Exception:
                pass
        return self._search

    def get_or_create_conversation(self, conv_id: str, department_id: str = None) -> Conversation:
        if conv_id:
            if conv_id in self.conversations:
                return self.conversations[conv_id]
            saved = self.mind.load_conversation(conv_id)
            if saved:
                self.conversations[conv_id] = saved
                return saved
        cid = conv_id or f"conv-{uuid.uuid4().hex[:8]}"
        conv = Conversation(cid, department_id)
        self.conversations[cid] = conv
        return conv

    def get_department_context(self, dept_id: str) -> str:
        dept = self.store.get_department(dept_id)
        if not dept:
            return ""
        summary = self.store.get_summary(dept_id)
        agents = self.store.list_agents(department_id=dept_id)
        tickets = self.store.list_tickets()
        if dept.project_ids:
            tickets = [t for t in tickets if t.project_id in dept.project_ids]
        scrums = self.store.get_todays_scrums(dept_id)

        ctx = f"=== {dept.icon} {dept.name} ===\n"
        ctx += f"Description: {dept.description}\n"
        ctx += f"Tickets: {summary['total_tickets']} total, {summary['active_tickets']} active\n"
        ctx += f"Agents: {len(agents)} ({sum(1 for a in agents if a.busy)} busy)\n"
        ctx += f"Agents: {', '.join(f'{a.name} ({a.role.value})' for a in agents[:5])}\n"
        ctx += f"Recent tickets: {', '.join(t.title for t in tickets[:5])}\n"

        if scrums:
            ctx += "--- Today's Standups ---\n"
            for s in scrums:
                for entry in s.entries:
                    ctx += f"  {entry.agent_id}: yesterday={entry.yesterday[:80]}, today={entry.today[:80]}, blockers={entry.blockers}\n"
        return ctx

    def get_standup_brief(self, dept_id: str = None) -> str:
        if dept_id:
            scrums = self.store.get_todays_scrums(dept_id)
            dept = self.store.get_department(dept_id)
            name = dept.name if dept else dept_id
        else:
            scrums = self.store.get_todays_scrums()
            name = "all departments"

        if not scrums:
            return f"No standups recorded yet today for {name}. 🤷"

        lines = [f"**Standup Brief — {name}** 📋"]
        for scrum in scrums:
            lines.append(f"\n  **{scrum.team_name}**")
            for entry in scrum.entries:
                yesterday = entry.yesterday or "Nothing"
                today = entry.today or "Continuing work"
                blockers = ", ".join(entry.blockers) if entry.blockers else "None"
                lines.append(f"    👤 {entry.agent_id}")
                lines.append(f"      ✅ Yesterday: {yesterday[:100]}")
                lines.append(f"      🚀 Today: {today[:100]}")
                lines.append(f"      🚧 Blockers: {blockers}")

        return "\n".join(lines)

    def process_message(self, conv_id: str, message: str, department_id: str = None) -> dict:
        conv = self.get_or_create_conversation(conv_id, department_id)
        conv.add_message("user", message)

        lang = self._detect_language(message)
        tone = self._analyze_tone(message)

        memories = self.mind.get_relevant_memories(message)
        memory_context = ""
        if memories:
            fact_lines = []
            for mem in memories:
                for f in mem.get("facts", []):
                    fact_lines.append(f"  - {f}")
            if fact_lines:
                memory_context = "Recall from Mind Palace:\n" + "\n".join(fact_lines)

        system = LILLY_SYSTEM_PROMPT
        if lang and lang != "en":
            system += f"\n\nUser is speaking in: {lang}. Respond in {lang}."
        system += f"\n\nUser tone: {tone}. Match this tone."
        if memory_context:
            system += f"\n\n{memory_context}"

        if department_id:
            dept_ctx = self.get_department_context(department_id)
            if dept_ctx:
                system += f"\n\nCurrent department context:\n{dept_ctx}"
        else:
            all_ctx = ""
            for d in self.store.list_departments():
                all_ctx += self.get_department_context(d.id) + "\n"
            system += f"\n\nAll departments context:\n{all_ctx}"

        # Check for standup brief requests
        if re.search(r'(?:standup|brief|what.*(?:doing|working|up to)|daily.*scrum|yesterday|today.*blocker)', message.lower()):
            dept_id = department_id
            dept_match = re.search(r'(\w+)\s+(?:standup|brief|scrum|team)', message.lower())
            if dept_match:
                dept_name = dept_match.group(1)
                for d in self.store.list_departments():
                    if dept_name in d.name.lower() or dept_name in d.id.lower():
                        dept_id = d.id
                        break
            brief = self.get_standup_brief(dept_id)

# 1. Append the incoming user message to the conversation history first
            conv.add_message("user", message) 

# 2. Append the assistant response
            conv.add_message("assistant", brief)

# 3. Save and return
            self.mind.save_memory(conv.id, message, brief)
            self.mind.save_conversation(conv)
            return {"response": brief, "conversation_id": conv.id}

        # Check for agent creation requests first
        agent_check = self._check_create_agent(message, conv)
        if agent_check:
            return agent_check

        # Check for search requests
        search_check = self._check_search_request(message, conv, department_id)
        if search_check:
            return search_check

        # Check for feature/bug auto-tickets
        feat_check = self._check_feature_request(message, conv)
        if feat_check:
            return feat_check

        # Ollama response
        response = self._call_ollama(system, conv.get_context())
        if not response:
            response = self._fallback_response(message, tone, lang, department_id)

        conv.add_message("assistant", response)
        self.mind.save_memory(conv.id, message, response)
        self.mind.save_conversation(conv)

        # Parse actions from response
        actions = self._parse_actions(response, department_id)
        result = {"response": response, "conversation_id": conv.id}
        if actions:
            result["actions"] = self._execute_actions(actions)

        # Parse images from response
        images = re.findall(r'\[IMAGE\]\s*(https?://\S+)', response)
        if images:
            result["images"] = images
            result["response"] = re.sub(r'\[IMAGE\]\s*(https?://\S+)\s*', '', response).strip()

        return result

    def _detect_language(self, text: str) -> str:
        try:
            from langdetect import detect
            lang = detect(text)
            lang_map = {
                "en": "en", "es": "es", "fr": "fr", "de": "de", "it": "it",
                "pt": "pt", "ru": "ru", "zh-cn": "zh", "zh-tw": "zh",
                "ja": "ja", "ko": "ko", "ar": "ar", "hi": "hi",
                "nl": "nl", "sv": "sv", "da": "da", "fi": "fi", "no": "no",
                "pl": "pl", "tr": "tr", "th": "th", "vi": "vi",
            }
            return lang_map.get(lang, lang)
        except Exception:
            return "en"

    def _analyze_tone(self, text: str) -> str:
        t = text.lower()
        urgency_words = ["urgent", "asap", "now", "hurry", "quick", "emergency", "critical", "broken", "crash", "fire"]
        casual_words = ["hey", "yo", "sup", "dude", "cool", "awesome", "nice", "wanna", "gonna", "btw"]
        formal_words = ["please", "would you", "could you", "kindly", "appreciate", "regarding", "per", "request"]

        urgency = sum(1 for w in urgency_words if w in t)
        casual = sum(1 for w in casual_words if w in t)
        formal = sum(1 for w in formal_words if w in t)

        if urgency > casual and urgency > formal:
            return "urgent"
        if formal > casual:
            return "formal"
        if casual > 0:
            return "casual"
        return "neutral"

    def _check_feature_request(self, message: str, conv) -> Optional[dict]:
        msg = message.lower()
        is_feature = re.search(r'(?:feature|app|software|tool|new\s+system)', msg) and re.search(r'(?:create|build|make|develop|want|need)', msg)
        is_bug = (re.search(r'\b(?:bug|error|crash|broken|issue|problem|fail)\b', msg)
                  and not re.search(r'(?:standup|brief|scrum|agent|persona)', msg))

        if is_feature or is_bug:
            dept_eng = self.store.get_department("dept-eng")
            if dept_eng and dept_eng.project_ids:
                project_id = dept_eng.project_ids[0]
                ticket_type = "bug" if is_bug else "story"
                priority = "high" if is_bug else "medium"
                points = 3 if is_bug else 5
                prefix = "Bug report" if is_bug else "Feature request"

                ticket = self.store.create_ticket(
                    project_id,
                    f"{prefix}: {message[:80]}",
                    f"Auto-generated from chat:\n\n{message}",
                    ticket_type, priority, points,
                )
                if ticket:
                    conv.add_message("assistant", f"[Created ticket {ticket.id}]")
                    self.mind.save_memory(conv.id, message, f"CREATED_TICKET {ticket.id}")
                    self.mind.save_conversation(conv)

                    if is_bug:
                        resp = f"Got it! 🐛 I've logged this as a bug in Engineering → **Ticket #{ticket.id}** with high priority. Engineering will triage it. Want me to search for any known fixes while you wait?"
                    else:
                        resp = f"Nice idea! 💡 I've created a feature ticket in Engineering → **Ticket #{ticket.id}**. The cogs are turning. Want me to pull in any related context or similar existing features?"

                    return {
                        "response": resp,
                        "conversation_id": conv.id,
                        "actions": [{
                            "action": "create_ticket", "status": "done",
                            "ticket_id": ticket.id, "title": ticket.title,
                            "department": "Engineering",
                        }],
                    }
        return None

    def _check_create_agent(self, message: str, conv) -> Optional[dict]:
        msg = message.lower()
        if not re.search(r'(?:create|make|hire|add|new)\s+.*(?:agent|persona|assistant|bot|character)', msg):
            return None

        name_match = re.search(r'(?:named|called|name\s+is)\s+(\w+)', message)
        role_match = re.search(r'(?:role|as\s+a|as\s+an)\s+(\w+(?:\s+\w+)?)', message)
        if not role_match:
            role_match = re.search(r'(\w+)\s+agent\s+named', message.lower())
        dept_match = re.search(r'(?:for|in)\s+(\w+(?:\s+\w+)?)\s*(?:department|team)?', message)

        dept_id = None
        if dept_match:
            dept_name = dept_match.group(1).lower()
            for d in self.store.list_departments():
                if dept_name in d.name.lower() or dept_name in d.id.lower():
                    dept_id = d.id
                    break

        agent = self._create_agent_from_chat(
            name=name_match.group(1) if name_match else f"Agent-{uuid.uuid4().hex[:4]}",
            role=role_match.group(1) if role_match else "assistant",
            department_id=dept_id or "dept-eng",
            specialty=message[:100],
        )
        if agent:
            conv.add_message("assistant", f"[Created agent {agent.id}]")
            self.mind.save_memory(conv.id, message, f"CREATED_AGENT {agent.id}")
            self.mind.save_conversation(conv)

            resp = f"Say hello to your new agent, **{agent.name}**! 🤖\n\n"
            resp += f"- **Role**: {role_match.group(1) if role_match else 'Assistant'}\n"
            resp += f"- **ID**: {agent.id}\n"
            if dept_id:
                dept = self.store.get_department(dept_id)
                resp += f"- **Department**: {dept.name if dept else dept_id}\n"
            resp += f"\nI've given them a unique persona and they're ready to jump in. Want me to assign them a task to get started?"

            return {
                "response": resp,
                "conversation_id": conv.id,
                "actions": [{"action": "create_agent", "status": "done", "agent_id": agent.id, "name": agent.name}],
            }
        return None

    def _create_agent_from_chat(self, name: str, role: str, department_id: str, specialty: str) -> Optional[object]:
        try:
            from models.agent import Agent, AgentRole

            role_lower = role.lower().replace(" ", "_")
            role_map = {
                "engineer": AgentRole.DEVELOPER, "developer": AgentRole.DEVELOPER,
                "designer": AgentRole.UI_DESIGNER, "ux": AgentRole.UI_DESIGNER,
                "architect": AgentRole.DEVELOPER, "lead": AgentRole.PROJECT_MANAGER,
                "pm": AgentRole.PROJECT_MANAGER, "devops": AgentRole.DEVOPS,
                "qa": AgentRole.QA,
                "analyst": AgentRole.DATA_SCIENTIST, "data": AgentRole.DATA_SCIENTIST,
                "ml": AgentRole.ML_ENGINEER, "ai": AgentRole.ML_ENGINEER,
                "finance": AgentRole.FINANCE_MANAGER, "accountant": AgentRole.ACCOUNTANT,
                "treasury": AgentRole.TREASURY_ANALYST,
                "marketing": AgentRole.MARKETING_MANAGER, "sales": AgentRole.B2B_SALES,
                "content": AgentRole.CONTENT_CREATOR,
                "hr": AgentRole.HR_MANAGER, "recruiter": AgentRole.RECRUITER,
                "training": AgentRole.TRAINING_COORDINATOR,
                "legal": AgentRole.CORPORATE_LAWYER, "compliance": AgentRole.COMPLIANCE_OFFICER,
                "contract": AgentRole.CONTRACT_MANAGER,
                "ops": AgentRole.OPS_MANAGER, "operation": AgentRole.OPS_MANAGER,
                "dispute": AgentRole.DISPUTE_MANAGER, "support": AgentRole.CUSTOMER_SUPPORT,
                "verification": AgentRole.VERIFICATION_SPECIALIST,
                "admin": AgentRole.ADMIN_MANAGER, "euc": AgentRole.EUC_SPECIALIST,
                "csr": AgentRole.CSR_COORDINATOR, "office": AgentRole.OFFICE_MANAGER,
            }
            agent_role = role_map.get(role_lower, AgentRole.DEVELOPER)

            agent_id = f"agent-{uuid.uuid4().hex[:8]}"
            agent = Agent(
                id=agent_id,
                name=name,
                role=agent_role,
                department_id=department_id,
                model=LLILLY_MODEL,
                system_prompt=f"You are {name}, a {role} created by Lilly. Specialty: {specialty[:200]}",
                metadata={"created_by": "lilly_chat", "specialty": specialty, "created_at": time.time()},
            )
            self.store.agents[agent_id] = agent
            return agent
        except Exception:
            return None

    def _check_search_request(self, message: str, conv, department_id: str = None) -> Optional[dict]:
        msg = message.lower()
        # Website analysis intent
        analysis_match = re.search(
            r'(?:analyze|scan|audit|check\s+security|test)\s+(?:website|site|url|domain|security\s+of)\s*(?:https?://)?([^\s]+)',
            msg,
        )
        if analysis_match:
            url = analysis_match.group(1).strip()
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            resp = (
                f"I'll analyze **{url}** for you right away! 🔍\n\n"
                f"Opening the Website Analysis panel with a full security audit, "
                f"tech stack detection, SSL check, and recommendations."
            )
            conv.add_message("assistant", resp)
            self.mind.save_memory(conv.id, message, resp)
            self.mind.save_conversation(conv)
            return {"response": resp, "conversation_id": conv.id, "open_analysis": url}

        search_triggers = [
            r'(?:weather|temperature|forecast)\s*(?:in|for|at)?\s*(\w+(?:\s+\w+)?)',
            r'(?:news|headlines|latest)\s*(?:about|on|in)?\s*(.+)?',
            r'(?:picture|photo|image|pic|show\s+me|search\s+(?:for\s+)?(?:pictures|photos|images|pics))\s+(?:of|with)?\s*(.+)',
            r'(?:price|cost|how\s+much)\s+(?:of|for|is|are)\s+(.+)',
            r'(?:gas|fuel)\s*(?:price|cost|rate)',
            r'(?:apple|fruit)\s*(?:price|cost)',
            r'(?:event|happening|going\s+on)\s*(?:in|near|at)?\s*(.+)?',
            r'(?:search|find|look\s+up|google|what\s+is|who\s+is|tell\s+me\s+about)\s+(.+)',
        ]

        matched_pattern = None
        matched_match = None
        query = None
        for pattern in search_triggers:
            m = re.search(pattern, msg)
            if m:
                matched_pattern = pattern
                matched_match = m
                query = m.group(1).strip() if m.lastindex and m.group(1) else None
                break

        if not matched_pattern:
            return None

        if not self.search:
            return None

        if re.search(r'(?:weather|temperature|forecast)', msg):
            data = self.search.get_weather(query)
            if "error" in data:
                resp = f"Couldn't find weather for that location. Try a city name! 🌍"
            else:
                resp = f"**{data['location']}** 🌤️\n"
                resp += f"🌡️ {data['temperature']}°C (feels like {data['feels_like']}°C)\n"
                resp += f"☁️ {data['condition']}  |  💧 {data['humidity']}% humidity  |  💨 {data['wind_speed']} km/h\n"
                if data.get('high') and data.get('low'):
                    resp += f"📈 High: {data['high']}°C  |  📉 Low: {data['low']}°C"
            conv.add_message("assistant", resp)
            self.mind.save_memory(conv.id, message, resp)
            self.mind.save_conversation(conv)
            return {"response": resp, "conversation_id": conv.id}

        if re.search(r'(?:news|headlines|latest)', msg):
            topic = query or "technology"
            news = self.search.get_news(topic)
            if news:
                resp = f"**Latest {topic.title()} News** 📰\n\n"
                for i, n in enumerate(news[:5], 1):
                    resp += f"{i}. **{n['title']}**\n   {n['snippet'][:100]}...\n   🔗 {n['url']}\n\n"
            else:
                resp = f"Couldn't pull news for '{topic}' right now. Try again in a bit! 📰"
            conv.add_message("assistant", resp)
            self.mind.save_memory(conv.id, message, resp)
            self.mind.save_conversation(conv)
            return {"response": resp, "conversation_id": conv.id}

        if re.search(r'(?:picture|photo|image|pic|show\s+me)', msg):
            results = self.search.image_search(query or message, 4)
            if results:
                images = [r["image"] for r in results if r.get("image")]
                resp = f"Here's what I found for **{query or message}** 🎨\n\n"
                resp += f"I found {len(images)} images. Take a look!"
                conv.add_message("assistant", resp)
                self.mind.save_memory(conv.id, message, resp)
                self.mind.save_conversation(conv)
                return {"response": resp, "conversation_id": conv.id, "images": images}
            else:
                resp = f"Couldn't find images for that. Want me to search the web instead? 🔍"
                conv.add_message("assistant", resp)
                self.mind.save_memory(conv.id, message, resp)
                self.mind.save_conversation(conv)
                return {"response": resp, "conversation_id": conv.id}

        # Web search for everything else
        results = self.search.web_search(query or message, 5)
        if results:
            resp = f"Here's what I found for **{query or message}** 🔍\n\n"
            for i, r in enumerate(results[:5], 1):
                resp += f"{i}. **{r['title']}**\n   {r['snippet'][:150]}...\n   🔗 {r['url']}\n\n"
            resp += "Want me to dig deeper into any of these?"

            images = self.search.image_search(query or message, 2)
            image_urls = [img["image"] for img in images if img.get("image")]
        else:
            resp = f"Hmm, couldn't find anything for '{query or message}'. Try being more specific! 🔍"
            image_urls = []

        conv.add_message("assistant", resp)
        self.mind.save_memory(conv.id, message, resp)
        self.mind.save_conversation(conv)

        result = {"response": resp, "conversation_id": conv.id}
        if image_urls:
            result["images"] = image_urls
        return result

    def _fallback_response(self, message: str, tone: str = "neutral", lang: str = "en", dept_id: str = None) -> str:
        msg = message.lower()

        if 'hello' in msg or 'hi' in msg or 'hey' in msg:
            base = "Hey there! 👋 I'm Lilly, your platform AI. I can help you with anything — departments, tickets, standups, code, or even search the web for you. What's on your mind?"
            return self._adapt_tone(base, tone)

        if 'department' in msg or 'dept' in msg:
            depts = self.store.list_departments()
            lines = [f"  {d.icon} **{d.name}** — {d.description}" for d in depts]
            base = "We've got **8 departments** all working in sync:\n" + "\n".join(lines) + "\n\nWant me to brief you on any one of them?"
            return self._adapt_tone(base, tone)

        if 'ticket' in msg or 'task' in msg or 'create' in msg:
            if dept_id:
                dept = self.store.get_department(dept_id)
                if dept:
                    s = self.store.get_summary(dept_id)
                    base = f"Ready to create in **{dept.name}**! Currently {s['active_tickets']} active tickets. Tell me what needs doing and I'll spin up a ticket. 🎫"
                    return self._adapt_tone(base, tone)
            base = "I can create tickets in any department. Just tell me what needs to be done and where. 🎫"
            return self._adapt_tone(base, tone)

        if 'agent' in msg or 'who' in msg or 'team' in msg:
            depts = self.store.list_departments()
            total = sum(len(self.store.list_agents(department_id=d.id)) for d in depts)
            base = f"There are **{total} AI agents** across {len(depts)} departments, all running on Ollama. Need a new one? I can spin up a custom agent with its own persona in seconds. 🤖"
            return self._adapt_tone(base, tone)

        if 'stats' in msg or 'how many' in msg or 'count' in msg:
            if dept_id:
                s = self.store.get_summary(dept_id)
                dept = self.store.get_department(dept_id)
                base = f"**{dept.name}** 📊\n- {s['total_tickets']} tickets ({s['active_tickets']} active)\n- {s['agents_total']} agents ({s['agents_busy']} busy)\n- {s['overdue_tickets']} overdue, {s['sla_breaches']} SLA breaches\n- {s['sprints_active']} active sprints"
            else:
                s = self.store.get_summary()
                base = f"**Corporate Overview** 📊\n- {s['total_tickets']} tickets ({s['active_tickets']} active)\n- {s['agents_total']} agents ({s['agents_busy']} busy)\n- {s['notices_count']} notices\n- {s['sprints_active']} active sprints"
            return self._adapt_tone(base, tone)

        if 'design' in msg or 'theme' in msg or 'editor' in msg or 'ui' in msg:
            base = "The Design Editor is in the sidebar! 🎨 You can customize colors, spacing, typography, and drag-drop components. Want me to tweak something specific?"
            return self._adapt_tone(base, tone)

        if 'memory' in msg or 'mind' in msg or 'palace' in msg:
            base = "My Mind Palace is fully operational! 🧠 I remember our past conversations and can recall relevant context. Ask me something we've discussed before!"
            return self._adapt_tone(base, tone)

        if 'code' in msg or 'write' in msg or 'program' in msg or 'function' in msg:
            base = "I can write code! 💻 Tell me what you need — a function, a component, a script, or even review existing code. I'm fluent in Python, JavaScript, and most modern stacks."
            return self._adapt_tone(base, tone)

        if 'translate' in msg or 'language' in msg:
            base = "I can translate between languages! 🌍 Just write something in any language and I'll detect it and respond in the same language. Want to try?"
            return self._adapt_tone(base, tone)

        if 'standup' in msg or 'brief' in msg or 'scrum' in msg:
            return self.get_standup_brief(dept_id)

        if self.search and re.search(r'(?:search|find|look\s+up|what\s+is|who\s+is)', msg):
            query = re.sub(r'(?:can you|please|could you)', '', message, flags=re.I).strip()
            results = self.search.web_search(query, 3)
            if results:
                base = "Here's what I found 🔍\n" + "\n".join(f"• **{r['title']}**: {r['snippet'][:100]}..." for r in results)
                return self._adapt_tone(base, tone)
            base = "I tried searching but couldn't find anything on that. Want to try a different query?"
            return self._adapt_tone(base, tone)

        base = "I'm all ears! 👂 Ask me about departments, tickets, standups, the weather, latest news, or anything you're curious about. Or tell me to create something, search the web, even spin up a new AI agent!"
        return self._adapt_tone(base, tone)

    def _adapt_tone(self, text: str, tone: str) -> str:
        if tone == "urgent":
            return f"⚡ On it! {text}"
        if tone == "formal":
            return text.replace("Hey there!", "Hello.").replace(" 👋", "").replace("What's on your mind?", "How may I assist you?")
        if tone == "casual":
            if "I'm all ears" in text:
                return "Yo! 👋 What's up? Ask me anything — depts, tickets, standups, web search, you name it!"
            return text
        return text

    def _get_whisper_model(self):
        if not hasattr(self, '_whisper_model') or self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                self._whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
            except Exception:
                self._whisper_model = None
        return self._whisper_model

    def transcribe_audio(self, audio_data: bytes, filename: str = "audio.webm") -> Optional[str]:
        # Use faster-whisper via temp file (handles webm, wav, ogg, etc. via ffmpeg)
        try:
            import tempfile, os

            model = self._get_whisper_model()
            if model is None:
                raise ImportError("faster-whisper not available")
            suffix = os.path.splitext(filename)[1] or ".webm"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_data)
                temp_path = f.name

            segments, _ = model.transcribe(temp_path, beam_size=5, language="en")
            text = " ".join(seg.text for seg in segments).strip()
            os.unlink(temp_path)
            if text:
                return text
        except Exception as e:
            print(f"[transcribe] faster-whisper failed: {e}", flush=True)

        # Fallback: try python-whisper
        try:
            import whisper, tempfile, os
            model = whisper.load_model("tiny")
            suffix = os.path.splitext(filename)[1] or ".webm"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_data)
                temp_path = f.name
            result = model.transcribe(temp_path)
            os.unlink(temp_path)
            return result["text"].strip()
        except Exception:
            pass

        # Remote fallback: Ollama whisper
        try:
            import base64
            b64 = base64.b64encode(audio_data).decode()
            resp = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": "whisper",
                    "prompt": "Transcribe this audio recording.",
                    "audio": b64,
                    "stream": False,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
        except Exception:
            pass

        return None

    def _call_ollama(self, system: str, messages: list) -> Optional[str]:
        try:
            prompt_parts = []
            for msg in messages[-6:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")[-1000:]
                prompt_parts.append(f"{role.upper()}: {content}")
            prompt = "\n".join(prompt_parts)

            resp = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": LLILLY_MODEL,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"num_predict": 1024},
                },
                timeout=120,
            )
            if resp.status_code == 200:
                return _strip_formalities(resp.json().get("response", ""))
            return None
        except Exception:
            return None

    def _parse_actions(self, response: str, default_dept: str = None) -> list:
        actions = []
        for match in re.finditer(r'\[CREATE_TICKET\]\s*([^|\n]+)\|([^|\n]+)\|([^|\n]*)\|?([^|\n]*)\|?([^|\n]*)\|?([^|\n]*)', response):
            dept = match.group(1).strip() or default_dept
            actions.append({
                "type": "create_ticket", "department_id": dept,
                "title": match.group(2).strip(), "description": match.group(3).strip() or "",
                "ticket_type": match.group(4).strip() or "task",
                "priority": match.group(5).strip() or "medium",
                "story_points": int(match.group(6).strip()) if match.group(6).strip() else 1,
            })
        for match in re.finditer(r'\[QUERY_DEPT\]\s*([^|\n]+)\|(.+)', response):
            actions.append({"type": "query_department", "department_id": match.group(1).strip(), "question": match.group(2).strip()})
        for match in re.finditer(r'\[CREATE_AGENT\]\s*([^|\n]+)\|([^|\n]+)\|([^|\n]+)\|([^|\n]*)\|?([^|\n]*)', response):
            actions.append({
                "type": "create_agent", "name": match.group(1).strip(),
                "role": match.group(2).strip(), "department_id": match.group(3).strip(),
                "specialty": match.group(4).strip() or "",
                "personality": match.group(5).strip() or "",
            })
        for match in re.finditer(r'\[UI_ACTION\]\s*(\w+)\|?(.*)', response):
            actions.append({"type": "ui_action", "ui_action": match.group(1).strip(), "ui_param": match.group(2).strip() or None})
        if re.search(r'\[DRIVE_SYNC\]', response):
            actions.append({"type": "drive_sync"})
        return actions

    def _execute_actions(self, actions: list) -> list:
        results = []
        for action in actions:
            if action["type"] == "create_ticket":
                dept = self.store.get_department(action["department_id"])
                if dept and dept.project_ids:
                    ticket = self.store.create_ticket(
                        dept.project_ids[0], action["title"], action["description"],
                        action["ticket_type"], action["priority"], action["story_points"],
                    )
                    if ticket:
                        results.append({"action": "create_ticket", "status": "done", "ticket_id": ticket.id, "title": ticket.title, "department": dept.name})
                        self.store.add_notice(
                            type="system_alert", title=f"Lilly created: {ticket.title}",
                            message=f"New {action['ticket_type']} in {dept.name}: {ticket.title}",
                            priority="normal", project_id=dept.project_ids[0], ticket_id=ticket.id,
                        )
                        continue
                results.append({"action": "create_ticket", "status": "failed", "error": f"Department {action['department_id']} not found"})

            elif action["type"] == "query_department":
                dept = self.store.get_department(action["department_id"])
                if dept:
                    results.append({"action": "query_department", "status": "done", "department": dept.name, "summary": self.store.get_summary(action["department_id"])})
                else:
                    results.append({"action": "query_department", "status": "failed", "error": "Department not found"})

            elif action["type"] == "create_agent":
                agent = self._create_agent_from_chat(
                    action["name"], action["role"], action["department_id"],
                    f"{action['specialty']} — {action['personality']}",
                )
                if agent:
                    results.append({"action": "create_agent", "status": "done", "agent_id": agent.id, "name": agent.name})
                else:
                    results.append({"action": "create_agent", "status": "failed"})

            elif action["type"] == "ui_action":
                results.append({
                    "action": "ui_action",
                    "ui_action": action.get("ui_action"),
                    "ui_param": action.get("ui_param"),
                    "status": "dispatched",
                })

            elif action["type"] == "drive_sync":
                try:
                    from services.drive import is_authenticated, ensure_department_folders, get_folder_links
                    if is_authenticated():
                        depts = self.store.list_departments()
                        dept_dicts = [{"id": d.id, "name": d.name} for d in depts]
                        folder_map = ensure_department_folders(dept_dicts)
                        self.store.drive_folder_map = folder_map
                        links = get_folder_links(folder_map, dept_dicts)
                        count = sum(1 for l in links if l["url"])
                        results.append({"action": "drive_sync", "status": "done", "folders": links, "count": count})
                    else:
                        results.append({"action": "drive_sync", "status": "needs_auth"})
                except Exception as e:
                    results.append({"action": "drive_sync", "status": "error", "error": str(e)})
        return results

    def get_history(self, conv_id: str, limit: int = 50) -> list:
        conv = self.conversations.get(conv_id)
        if not conv:
            saved = self.mind.load_conversation(conv_id)
            if saved:
                return saved.get_context(limit)
            return []
        return conv.get_context(limit)

    def get_self_healing_report(self) -> dict:
        summary = self.store.get_summary()
        tickets = self.store.list_tickets()
        overdue = [t for t in tickets if getattr(t, 'overdue', False)]
        high_priority = [t for t in tickets if getattr(t, 'priority', '') == 'critical']
        issues = []
        if overdue:
            issues.append(f"{len(overdue)} overdue tickets across all departments")
        if high_priority:
            issues.append(f"{len(high_priority)} critical-priority tickets need attention")
        if summary.get('sla_breaches', 0) > 0:
            issues.append(f"{summary['sla_breaches']} SLA breaches detected")
        health = "healthy"
        if issues:
            health = "degraded" if len(issues) <= 2 else "critical"
        return {
            "health": health, "issues": issues,
            "total_tickets": summary['total_tickets'], "active_tickets": summary['active_tickets'],
            "agents_total": summary['agents_total'], "agents_busy": summary['agents_busy'],
            "timestamp": time.time(),
        }
