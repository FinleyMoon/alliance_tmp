<!-- ALLIANCE PLATFORM VERSION: V9 -->
# ALLIANCE // Automated Game Master Platform (V9)

> **V9 Release Verified**: All Version 9 updates are active:
> 1. **Recreate Match Lobby**: Dedicated button in staging, concluded match debriefing, and match settings audit. Allows changing all parameters (including matrix dimensions: grid width/height, burst cap, drop rules) while automatically re-enlisting all human and bot operatives.
> 2. **Late Join System**: Players can request late entry into active matches up to 3 hours prior to the first scheduled AP drop. Match host can approve or deny entry directly from settings. Approved operatives spawn at safe vacant coordinates with full starting vitals.
> 3. **Ranked Choice Voting (RCV) Map Placements**: Optional deployment mode generating 4 randomized candidate placement maps with an Instant Runoff Voting (RCV) election, interactive visual mini-grid previews, and configurable election timer.
> 4. **Enrolled Device List Fix**: Root-scoped `/sw.js` registration and non-blocking fallback ensure all user devices register cleanly with `/api/push/subscribe` and display in device settings.
> 5. **Version 9 Release Tree**: Complete release mirrored to `/workspace/v9/` with full test parity.

Automated Game Master (GM) asynchronous elimination strategy web platform, built for single-container Docker deployment behind an existing Caddy reverse proxy.

---

## 1. Architectural Highlights

```
[ Internet / Clients ]
         │ (HTTPS / WSS)
         ▼
┌──────────────────────────────────────────────┐
│ Pre-Existing Caddy Reverse Proxy (Host)      │
│  - SSL Termination                           │
│  - Passes X-Forwarded-* Headers              │
│  - WebSocket Upgrade Support                 │
└──────────────────────┬───────────────────────┘
                       │ (127.0.0.1:8000)
                       ▼
┌──────────────────────────────────────────────┐
│ Single Docker Container (`python:3.12-slim`) │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │ Process Supervisor (supervisord)       │  │
│  ├────────────────────────────────────────┤  │
│  │ 1. FastAPI / Uvicorn ASGI Server       │  │
│  │    - REST APIs & WebSockets            │  │
│  │    - Neo-Material Cyber SPA Frontend   │  │
│  │                                        │  │
│  │ 2. Background Task Runner (Scheduler)  │  │
│  │    - Daily AP Drop (22:00:00 UTC)      │  │
│  │    - Stagnation & Sudden Death Clock   │  │
│  │    - Web Push Notification Worker      │  │
│  │                                        │  │
│  │ 3. Database: Embedded SQLite in WAL    │  │
│  │    (Serialized atomic write lock)      │  │
│  └────────────────────────────────────────┘  │
│  Storage Mount: /data (DB, Uploads, Keys)    │
└──────────────────────────────────────────────┘
```

### 1.1 Strict Concurrency & FIFO Engine
* **Per-Game FIFO Ingress Queue (`asyncio.Queue`):** Every incoming action payload enters an in-memory queue.
* **Nanosecond Arrival Stamping:** `arrival_time = time.monotonic_ns()` guarantees deterministic execution order.
* **Single-Worker Execution:** Dedicated worker dequeues commands strictly one by one.
* **Atomic Transactions:** Executes `BEGIN IMMEDIATE TRANSACTION;` on SQLite in WAL mode (`PRAGMA synchronous = NORMAL;`). Player B can never resolve before Player A if Player A submitted first.
* **Action Burst Cap:** Action payloads cannot exceed the configured Burst Cap (default: **3 AP**). Exceeding requests are rejected with `ERR_BURST_CAP_EXCEEDED`.

### 1.2 Fog of War (Information Visibility)
* **Confidential Stats:** Opponents' remaining HP, AP balances, and Range levels are omitted from public responses (serialized as `null`).
* **Non-Lethal Hits:** Attacker receives: `"Target damaged. Target remaining HP: UNKNOWN"`. Victim receives: `"Hit by [Player]! 1 damage taken. Current HP: X"`. Public feed receives *no event*.
* **Lethal Eliminaton:** Public receives: `"[Player] has been eliminated."` (Killer is omitted unless `reveal_killer_on_death` is configured to `true`). Victim receives: `"You were eliminated by [Killer]."`.

### 1.3 Cryptographic Audit Trail
* **SHA-256 Hash Chaining:** Every game event is hashed sequentially (`event_seq`, `prev_hash`, `timestamp_ns`, `payload`).
* **Public Parameter Changelog:** Any administrative setting modification is permanently logged in `game_parameter_audit_logs`.

---

## 2. Directory Structure

```
├── .forgejo/workflows/
│   └── docker-build.yml       # Forgejo CI/CD workflow triggered on tag Soddy
├── app/
│   ├── api/                   # REST endpoints and WebSocket handlers
│   ├── engine/                # Range geometry, action pipeline, queue, win conditions, ledger
│   ├── i18n/                  # Bilingual dictionaries (en.json, ru.json) and translator
│   ├── models/                # Pydantic schemas and lobby config specification
│   ├── scheduler/             # Periodic AP drop and stagnation runner
│   ├── services/              # Lobby management, audit logs, push notifications
│   ├── static/                # Neo-Material Cyber Single Page Application
│   ├── avatar.py              # Magic byte inspection, EXIF stripping, 128x128 resize
│   ├── config.py              # Environment configuration and paths
│   ├── database.py            # SQLite WAL connection and serialized transactions
│   ├── flags.py               # ISO 3166-1 alpha-2 flags registry
│   ├── main.py                # FastAPI application entry point
│   └── security.py            # Argon2id hashing, rate limiting, secure cookies
├── tests/                     # Comprehensive test suite (engine, win conditions, concurrency, security)
├── Caddyfile                  # Sample Caddy reverse proxy configuration
├── Dockerfile                 # Single-container production Dockerfile
├── docker-compose.yml         # Container deployment configuration
├── requirements.txt           # Production dependencies
├── supervisord.conf           # Process supervisor configuration
└── README.md
```

---

## 3. Build & Deployment Instructions

### Prerequisites
* Docker Engine 24+ and Docker Compose v2+
* Pre-existing Caddy reverse proxy running on the host machine

### Option A: Running with Docker Compose (Recommended)
1. Clone this repository onto your server:
   ```bash
   git clone <repo-url> alliance && cd alliance
   ```

2. Start the container:
   ```bash
   docker compose up -d --build
   ```

3. Check container status:
   ```bash
   docker compose ps
   docker compose logs -f
   ```

### Option B: Building and Running with Docker Directly
```bash
# Build Docker image
docker build -t alliance:latest .

# Run with persistent volume mounted to /data
docker run -d \
  --name alliance_gm \
  -p 127.0.0.1:8000:8000 \
  -v alliance_data:/data \
  --restart unless-stopped \
  alliance:latest
```

---

## 4. Reverse Proxy Integration (Caddy)

Add the following block to your host's `/etc/caddy/Caddyfile`:

```caddy
alliance.example.com {
    encode zstd gzip

    reverse_proxy 127.0.0.1:8000 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}

        transport http {
            keepalive 86400s
            keepalive_idle_conns 100
        }
    }
}
```

Reload Caddy:
```bash
systemctl reload caddy
```

---

## 5. Forgejo CI/CD Action Workflow

A pre-configured Forgejo workflow is included in `.forgejo/workflows/docker-build.yml`.

### Triggering the Build
Pushing a tag named `Soddy` automatically triggers the action runner to build and tag the Docker image:

```bash
git tag Soddy
git push origin Soddy
```

The workflow:
1. Checks out the repository.
2. Sets up Docker Buildx.
3. Builds the Docker container using `Dockerfile`.
4. Tags the output image as `alliance:Soddy` and `${{ github.repository }}:Soddy`.

---

## 6. Running the Automated Test Suite

Run the unit tests directly inside the repository:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

Test coverage includes:
* **`test_engine.py`**: Range geometry calculation (Level 1: $5\times 5+4=28$ tiles, Level 2: $7\times 7+4=52$ tiles), Move, Attack, Fatal hit, Non-lethal Fog of War, Burst Cap rejection.
* **`test_win_conditions.py`**: Last Man Standing, Unanimous Armistice, Rogue Traitor rule (`GRANT_AP`, `INSTANT_WIN`, `REVEAL_IDENTITY`), Stagnation (`DRAW`, `SUDDEN_DEATH`).
* **`test_concurrency.py`**: Ingress nanosecond timestamping monotonicity and strict FIFO queue ordering.
* **`test_security.py`**: Argon2id password hashing, sliding-window rate limiting (5 attempts/15 mins), and avatar magic byte inspection.
* **`test_ledger_audit.py`**: SHA-256 hash chaining and tamper detection.

---

## 7. Lobby Configuration Parameters Reference

| Setting Key | Type | Default | Range | Description |
| :--- | :--- | :--- | :--- | :--- |
| `board_width` | Integer | `15` | 5–50 | Board horizontal columns |
| `board_height` | Integer | `15` | 5–50 | Board vertical rows |
| `starting_hp` | Integer | `3` | 1–10 | Hearts per unit on spawn |
| `starting_ap` | Integer | `0` | 0–10 | Initial AP at match start |
| `starting_range_level`| Integer | `1` | 1–5 | Starting range tier ($1 = 5\times5+4$) |
| `ap_drop_cron` | String | `0 22 * * *` | Cron | Daily distribution schedule |
| `ap_drop_timezone` | String | `UTC` | IANA TZ | Schedule timezone |
| `ap_drop_amount` | Integer | `1` | 1–10 | AP awarded per drop |
| `burst_cap_ap` | Integer | `3` | 1–10 | Max AP cost allowed per batch payload |
| `move_cost_ap` | Integer | `1` | 1–5 | AP cost per orthogonal tile shift |
| `attack_cost_ap` | Integer | `1` | 1–5 | AP cost per strike in range |
| `give_ap_cost_ap` | Integer | `1` | 1–5 | AP cost per transfer to ally |
| `range_upgrade_cost_base` | Integer | `2` | 1–10 | Base AP cost for range upgrade |
| `range_upgrade_cost_scale`| Float | `1.0` | 1.0–3.0 | Multiplier per successive upgrade |
| `win_cond_last_man` | Boolean | `true` | bool | Last surviving combatant wins |
| `win_cond_armistice` | Boolean | `true` | bool | Unanimous peace vote collective win |
| `armistice_threshold` | Float | `1.0` | 0.5–1.0| Living ratio required for armistice |
| `win_cond_rogue` | Boolean | `true` | bool | Trigger special rule if $N-1$ vote peace |
| `rogue_resolution_mode`| Enum | `GRANT_AP` | Enum | `GRANT_AP`, `INSTANT_WIN`, `REVEAL_IDENTITY` |
| `rogue_bonus_ap` | Integer | `3` | 1–10 | AP awarded to rogue in `GRANT_AP` mode |
| `win_cond_stagnation` | Boolean | `false` | bool | Timeout if no damage occurs |
| `stagnation_days_limit`| Integer | `5` | 1–30 | Days without damage before stagnation |
| `stagnation_mode` | Enum | `DRAW` | Enum | `DRAW` or `SUDDEN_DEATH` (border collapse) |
| `reveal_killer_on_death`| Boolean | `false` | bool | Display killer in public announcements |
| `allow_dead_chat` | Boolean | `true` | bool | Permit eliminated players to chat |
| `fog_of_war_stats` | Boolean | `true` | bool | Strictly hide opponent HP, AP, Range |
| `audit_log_public` | Boolean | `true` | bool | Publish all setting edits to changelog |
