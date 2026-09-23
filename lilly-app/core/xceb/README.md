# XCEB — Face + OSINT Evidence Broker

Autonomous reverse-face search that identifies unknown people seen by the
phone camera, renames their overlay box from **"Person"** to their discovered
name, and produces an honest, source-linked dossier.

Everything runs locally (Docker), uses **free tools only**, and never
fabricates findings: confidence is computed strictly from observed evidence —
engines that agree, distinct platforms that corroborate, FAISS similarity —
and every item is linked to the real URL it was seen on.

```
camera (vision) ── unknown face crop ──► XCEB case
      ▲                                      │
      │  overlay box label "Person"          ▼
      │  → changed to the name          reverse engines
      └── identity pushed back      (yandex·bing·tineye·lens)
            (auto-rename)                    │
                                             ▼
                                     name candidate(s)
                                             │
                          accounts (social/dating/fan) · records · LLM
                                             │
                                     confidence ≥ 55% ?
                                             ▼
                                rename case folder unknown_* → <name>_<date>
                                enroll face into FAISS ("faces file")
                                write profile.html / .txt / graph / Maltego CSV
                                push identity to vision  →  box shows the name
```

## Run

```bash
cd lilly-app
docker compose up -d --build lilly-xceb
open http://localhost:8200
```

The vision overlay server can feed XCEB automatically. Set on `lilly-vision`:

```yaml
environment:
  - OSINT_PUSH_URL=http://127.0.0.1:8200/xceb/vision/faces/osint_unknown
```

XCEB answers `{"handled": true}` so the vision server keeps sampling, and
pushes resolved identities back to `POST /api/vision/face/identify` so the
live bounding-box label swaps to the name (iou ≥ 0.3 match on `person_box`).

## API

| Endpoint | Purpose |
|---|---|
| `POST /xceb/cases` | start a case from a face crop (`crop_b64`); auto-runs |
| `POST /xceb/cases/{id}/run` | (re)run pipeline, optional `stages` list |
| `GET /xceb/cases` | case list from the catalog |
| `GET /xceb/cases/{id}` | full ledger (findings, steps, meta) |
| `GET /xceb/cases/{id}/profile.html` | dossier with % confidence + sources |
| `GET /xceb/cases/{id}/profile.txt` | plain-text dossier |
| `GET /xceb/cases/{id}/graph.json` | entity graph |
| `GET /xceb/cases/{id}/graph.svg` | rendered connection map |
| `GET /xceb/cases/{id}/maltego_export.csv` | Maltego CE import file |
| `GET /xceb/cases/{id}/face.jpg` | the face crop |
| `POST /xceb/faces/search` | FAISS search by photo, narrowed by geo/category/platform |
| `GET /xceb/faces` | the faces "file" (ledger + index stats) |
| `POST /xceb/faces/enroll` | enroll a named face directly |
| `POST /xceb/vision/faces/osint_unknown` | vision-server ingest (see above) |
| `GET/POST /xceb/scheduler` | sleep-window batch status / control |
| `GET /xceb/stream` | SSE live feed (cases, scheduler, vision ingest) |
| `GET /xceb/health`, `/xceb/config` | health + runtime config |
| `GET /` | web dashboard |

## Honest confidence model

- Reverse hit on ≥1 engine: **lead** (base 0.15 + 0.10 per agreeing engine,
  +0.02 per hit, capped 0.55 from reverse alone).
- ≥3 distinct platforms corroborating an account name: +0.05.
- FAISS named match at sim ≥ 0.60: sets identity at `0.72 × sim`.
- Optional Ollama rerank may +0.05 when it agrees with the top candidate.
- **Resolve threshold `XCEB_RESOLVE_CONF` (default 0.55)** triggers the
  folder rename to the person's name and FAISS enrollment.
- 30–55% → status `lead`; below → `unresolved`, auto-retried nightly
  (`XCEB_RETRY_HOURS`, within `XCEB_SLEEP_WINDOW`).
- Contact & residence data is always marked **"UNVERIFIED LEADS"** — verify
  independently before acting.

## Scheduler

Runs inside the server process: every `XCEB_SCHEDULER_TICK` (300s default)
it checks the sleep window (`XCEB_SLEEP_WINDOW` default `01:00-05:00`) and
processes due cases (max `XCEB_MAX_PER_TICK` 6). `--once` / `--status` CLI:

```bash
docker exec lilly-xceb python3 xceb_scheduler.py --status
docker exec lilly-xceb python3 xceb_scheduler.py --once
```

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `XCEB_DATA` | `/app/data` | cases, faces file, pub crops, logs |
| `XCEB_SHARED` | `/app/shared` | ro: shared models + known_faces seed |
| `XCEB_PUBLIC_BASE` | – | public tunnel base for URL-flow engines |
| `XCEB_VISION_URL` | `http://127.0.0.1:8198` | overlay server for auto-rename push |
| `XCEB_LLM_URL` | host Ollama | optional rerank/extract |
| `XCEB_RESOLVE_CONF` | `0.55` | identity confidence to resolve/rename |
| `XCEB_AUTO_ENROLL` | `1` | enroll resolved faces into the index |
| `XCEB_SLEEP_WINDOW` | `01:00-05:00` | night batch window |
| `XCEB_RETRY_HOURS` | `24` | unresolved case retry cadence |
| `XCEB_ENGINE_DELAY` | `2.5` | per-engine rate limit (s) |
| `XCEB_MAX_CASES` | `400` | catalog cap |
| `XCEB_PROXY` | – | socks5/http proxy for stealth |
| `XCEB_TOKEN` | – | optional bearer; UI: `/?token=…` |
| `SCRFD_CONF` | `0.15` | relaxed detector floor for small vision crops |
| `XCEB_SCHEDULER` | `1` | enable the scheduler loop |

## Faces file

The "file with faces" (FAISS + ledger) lives at `XCEB_DATA/faces/`
(`faces.json`, `embeddings.npy`, `faces.index`, `thumbs/`). `search_face()`
narrows by geography, category (social/dating/fan/news/…) and platform; the
vision server's `known_faces.json` can be imported via
`xceb_faces.seed_from_vision()`.

## Tooling

- Engines: Yandex, Bing, TinEye, Google Lens — real HTTP scrapes with a
  stealth fetcher tier (scrapling) behind an httpx fast path.
- Free-tool lineage: The-Osint-Toolbox / Image-Research-OSINT ideas, Maltego
  Community Edition import via `maltego_export.csv`.
- LLM (Ollama) is optional and only reranks real candidates — it never
  invents evidence.

## Notes

- `case_id` stays stable across the `unknown_*` → `<name>_<date>` folder rename.
- Profile/graph/Maltego CSVs are rewritten on every pipeline run, so the UI
  always reflects the freshest state.
- Reverse-engine scrapes are rate-limited (2.5 s + jitter per engine); flag
  Cloudflare-protected results gracefully degrade to fewer hits rather than
  fake matches.