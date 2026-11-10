# ALLIANCE // AUTOMATED GAME MASTER: MASTER ARCHITECTURE & ENGINE SPECIFICATION

> **Target Audience:** Systems Architects, Software Engineers, and AI Development Agents maintaining, extending, or refactoring the Alliance Platform.

---

## 1. Executive Summary & Core Game Concept

**Alliance** is an asynchronous elimination strategy game executed on a discrete 2D grid matrix. Players control a single unit on the board, spending Action Points (AP) in real time to move, attack opponents, transfer AP to allies, or upgrade their interaction range.

* **Objective:** Survive as the last living unit on the board, or coordinate with living survivors to pass an Armistice peace treaty.
* **Asynchronous AP Economy:** Units receive periodic AP distributions (default: +1 AP every 24 hours at 22:00 UTC). Unspent AP never expires and can be stockpiled indefinitely until elimination, at which point all stockpiled AP is permanently vaporized.
* **Fog of War:** Exact health balances (HP), AP stockpiles, and range tiers of opponents are strictly confidential. Combat produces private receipts for participants; only fatal eliminations and movements are broadcast publicly.
* **Untrusted Client:** The frontend client (web application) is treated as a visualizer. All validation, state transitions, range calculations, and burst cap limits are computed and enforced server-side.

---

## 2. System Architecture & Deployment Topology

```
[ Internet / Clients ]
         │ (HTTPS / WSS)
         ▼
┌──────────────────────────────────────────────┐
│ Pre-Existing Caddy Reverse Proxy (Host)      │
│  - SSL/TLS Termination                      │
│  - Passes X-Forwarded-For, -Proto, -Host     │
│  - WebSocket Upgrade Support (keepalives)    │
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
│  │    - REST APIs & WebSocket Server      │  │
│  │    - Static File Delivery (SPA)        │  │
│  │                                        │  │
│  │ 2. Background Task Runner (Scheduler)  │  │
│  │    - Daily AP Drop (22:00:00 UTC)      │  │
│  │    - Inactivity & Stagnation Clocks    │  │
│  │    - Web Push Notification Worker      │  │
│  │                                        │  │
│  │ 3. Database: Embedded SQLite in WAL    │  │
│  │    (PRAGMA journal_mode = WAL)         │  │
│  │    (PRAGMA synchronous = NORMAL)       │  │
│  │    (Single-writer serialized locks)    │  │
│  └────────────────────────────────────────┘  │
│  Persistent Mount: /data (DB, Uploads, Keys) │
└──────────────────────────────────────────────┘
```

### 2.1 Process Supervisor (`supervisord.conf`)
* Supervisord manages two co-located background processes inside the container:
  1. `uvicorn app.main:app`: ASGI server listening on `0.0.0.0:8000` with `--proxy-headers`, `--forwarded-allow-ips='*'`, and `--timeout-keep-alive 86400`.
  2. `python3 -m app.scheduler.cron_worker`: Embedded scheduler loop for daily AP distributions, stagnation clocks, and push notifications.

### 2.2 Database Storage Mount (`/data`)
* The application requires a single persistent volume mounted to `/data`:
  * `/data/alliance.db`: Embedded SQLite database.
  * `/data/uploads/`: Sanitized $128 \times 128$ user avatars.
  * `/data/keys/`: Auto-generated VAPID EC P-256 key pair (`vapid_private.pem`, `vapid_public.pem`).

---

## 3. Strict Concurrency & First-Come, First-Served Action Pipeline

### 3.1 The Concurrency Ingestion Problem
In a turnless, real-time asynchronous game, multiple players may submit conflicting actions nearly simultaneously (e.g., Player A shoots Player B while Player B attempts to move out of range or shoot Player A). Traditional concurrent HTTP threads risk race conditions.

### 3.2 Ingress Serialization Specification
1. **Per-Game In-Memory Queue (`asyncio.Queue`):** Every incoming action payload hitting the API (`POST /api/games/{id}/actions`) is immediately wrapped in an `ActionRequest` and enqueued into a dedicated FIFO queue for that specific game instance.
2. **Monotonic Nanosecond Timestamping:**
   ```python
   arrival_time_ns = time.monotonic_ns()
   ```
   Every request is tagged with the server's monotonic clock at the exact instant it enters the ingress pipeline.
3. **Single-Worker Execution:** A single background worker coroutine (`_game_worker_loop`) pulls requests from the game queue strictly one by one.
4. **Immediate Database Transaction Lock:**
   ```sql
   BEGIN IMMEDIATE TRANSACTION;
   ```
   The transaction acquires a reserved write lock on SQLite before any state checks or mutations occur, guaranteeing that Player B's request can never resolve before Player A's request if Player A arrived first.
5. **Burst Cap Limit Enforcement:**
   * **Rule:** A single action payload can consume no more than the lobby's configured `burst_cap_ap` (Default: 3 AP).
   * **Validation:** If the combined cost of the queued actions in a payload exceeds `burst_cap_ap`, the entire payload is rejected with error: `ERR_BURST_CAP_EXCEEDED`.
   * **Pacing:** The user's client must receive the execution receipt and the resulting board state before transmitting a subsequent batch.

---

## 4. Range Geometry & Combat Engine

### 4.1 Range Geometry Mathematical Formula
Interaction range expands symmetrically outward from the player's coordinate $(x_0, y_0)$ based on Range Level $L$ ($L \ge 1$, starting level $L=1$):

* **Core Half-Width (Radius $R$):**
  $$R = L + 1$$
* **Core Box:** A square of dimension $(2R + 1) \times (2R + 1)$ centered at $(x_0, y_0)$:
  $$\text{Core Tile Condition: } \max(|x - x_0|, |y - y_0|) \le R$$
* **Cardinal Spikes:** 4 extensions reaching distance $R + 1$ in orthogonal directions:
  $$\text{Spike Tiles: } \{(x_0, y_0 \pm (R+1)), (x_0 \pm (R+1), y_0)\}$$
* **Formal In-Range Definition:**
  $$\text{In-Range}(x, y) \iff \Big(\max(|x - x_0|, |y - y_0|) \le R \;\lor\; \big(|x - x_0| + |y - y_0| = R + 1 \;\land\; (x = x_0 \lor y = y_0)\big)\Big) \;\land\; (x, y) \ne (x_0, y_0)$$

#### Range Progression Table

| Level $L$ | Core Radius $R$ | Core Dimensions | Core Reachable Tiles | Spikes Count | Total Reachable Tiles |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **Level 1 (Base)** | 2 | $5 \times 5$ | 24 (excluding center) | 4 (at distance 3) | **28 tiles** |
| **Level 2 (+1 Upgrade)** | 3 | $7 \times 7$ | 48 (excluding center) | 4 (at distance 4) | **52 tiles** |
| **Level 3 (+2 Upgrades)** | 4 | $9 \times 9$ | 80 (excluding center) | 4 (at distance 5) | **84 tiles** |
| **Level $L$** | $L+1$ | $(2L+3) \times (2L+3)$ | $(2L+3)^2 - 1$ | 4 | $(2L+3)^2 + 3$ |

### 4.2 Actions & Validation Rules
1. **MOVE (Cost: `move_cost_ap`, Default 1 AP):**
   * Target tile must be orthogonally adjacent: $|x - x_0| + |y - y_0| = 1$.
   * Diagonal movement is strictly forbidden.
   * Target tile must be within active grid boundaries ($1 + \text{shrink} \le x \le W - \text{shrink}, 1 + \text{shrink} \le y \le H - \text{shrink}$).
   * Target tile must be completely unoccupied by any living unit.
2. **ATTACK (Cost: `attack_cost_ap`, Default 1 AP):**
   * Target must be an active, living player (`is_alive == 1`, `hp > 0`).
   * Target must occupy a tile satisfying the Range Geometry formula.
   * Line of sight is unobstructed (attacks bypass intervening players).
   * Inflicts 1 damage to target. If target HP drops to 0, initiates elimination lifecycle.
3. **GIVE AP (Cost: `give_ap_cost_ap`, Default 1 AP):**
   * Target must be a living player within initiator's Range Geometry.
   * Deducts 1 AP from initiator balance; credits 1 AP to target balance.
4. **UPGRADE RANGE (Cost: Scalable AP):**
   * Cost formula: $\lceil \text{base\_cost} \times (\text{scale\_factor})^{L-1} \rceil$.
   * Increments player range level ($L \to L+1$).

### 4.3 Elimination Lifecycle
When a unit's HP reaches 0:
1. `is_alive` is set to `0`, `hp` is set to `0`, and `eliminated_at` is stamped.
2. `eliminated_by` is recorded as the killer's username.
3. **Stockpiled AP Destruction:** Target's AP balance is set to `0` (points cannot be looted or transferred posthumously).
4. **Public Broadcast:** Event `ELIMINATION` is appended to the public ledger. The killer's name is excluded unless `reveal_killer_on_death` is `true`.
5. **Private Notice:** The victim receives a private notification: `"You were eliminated by [Killer]."`.

---

## 5. Fog of War & Information Visibility Data Contract

Data serialization enforces strict separation between public payloads and authenticated session payloads:

```
                           MASTER GAME STATE (Internal DB)
                                        │
           ┌────────────────────────────┴────────────────────────────┐
           ▼                                                         ▼
[ PUBLIC SERIALIZER ]                                     [ PRIVATE SERIALIZER ]
 - Board dimensions & shrink level                         - Own remaining HP
 - All player positions (X, Y)                             - Own current AP
 - Player alive / dead statuses                            - Own Range level
 - Public move announcements                               - Targeted non-lethal hits received
 - Public death notices (Victim only)                      - AP gifts received from allies
 - Opponents' HP: NULL                                     - Attacker receipt:
 - Opponents' AP: NULL                                       "Target damaged.
 - Opponents' Range: NULL                                     Target remaining HP: UNKNOWN"
```

---

## 6. Multi-Win Condition Engine

Evaluated inside atomic transactions immediately following any action execution or scheduled clock tick:

| Win Condition | Activation Trigger | Resolution & Side-Effects |
| :--- | :--- | :--- |
| **Last Man Standing** (`win_cond_last_man`) | `living_players == 1` | That player is declared sole victor (`LAST_MAN`). Game status transitions to `ENDED`. |
| **Unanimous Armistice** (`win_cond_armistice`) | Living peace votes / Total living $\ge \text{armistice\_threshold}$ (Default: 1.0 = 100%) | Game ends immediately. All living players share collective victory (`ARMISTICE`). |
| **Rogue / Traitor Rule** (`win_cond_rogue`) | Living $\ge 3$ and exactly $N-1$ living players vote for peace, but 1 player refuses | **Configurable Resolution Mode (`rogue_resolution_mode`):**<br>• `GRANT_AP`: Rogue immediately receives $+\text{rogue\_bonus\_ap}$ AP.<br>• `INSTANT_WIN`: Rogue wins immediately by betrayal.<br>• `REVEAL_IDENTITY`: Public announcement identifies the holdout. |
| **Stagnation / Timeout** (`win_cond_stagnation`) | No damage or death has occurred within `stagnation_days_limit` days | **Configurable Resolution Mode (`stagnation_mode`):**<br>• `DRAW`: Game terminates in an Automatic Draw.<br>• `SUDDEN_DEATH`: Daily AP drops double; board outer boundary collapses by 1 tile layer, eliminating units caught in the collapse. |

---

## 7. Comprehensive Lobby Configuration Registry (28 Keys)

Every lobby stores its parameter set in `game_parameters`:

| Column Key | SQL Type | Default | Validation Range | Description |
| :--- | :--- | :--- | :--- | :--- |
| `board_width` | INTEGER | `15` | $5 - 50$ | Number of horizontal grid columns |
| `board_height` | INTEGER | `15` | $5 - 50$ | Number of vertical grid rows |
| `starting_hp` | INTEGER | `3` | $1 - 10$ | Hearts assigned to players on spawn |
| `starting_ap` | INTEGER | `0` | $0 - 10$ | Action points available at match start |
| `starting_range_level` | INTEGER | `1` | $1 - 5$ | Initial range tier ($1 = 5\times5+4$) |
| `ap_drop_cron` | TEXT | `0 22 * * *` | Valid Cron | Recurrence schedule for AP distribution |
| `ap_drop_timezone` | TEXT | `UTC` | IANA TZ | Timezone for drop schedule |
| `ap_drop_amount` | INTEGER | `1` | $1 - 10$ | Number of AP awarded per drop tick |
| `burst_cap_ap` | INTEGER | `3` | $1 - 10$ | Maximum AP allowed per command batch |
| `move_cost_ap` | INTEGER | `1` | $1 - 5$ | AP cost to move 1 orthogonal tile |
| `attack_cost_ap` | INTEGER | `1` | $1 - 5$ | AP cost to attack a target in range |
| `give_ap_cost_ap` | INTEGER | `1` | $1 - 5$ | AP cost to transfer 1 AP to an ally |
| `range_upgrade_cost_base` | INTEGER | `2` | $1 - 10$ | Base AP cost for first range upgrade |
| `range_upgrade_cost_scale`| REAL | `1.0` | $1.0 - 3.0$ | Exponential multiplier per successive tier |
| `win_cond_last_man` | INTEGER | `1` | 0 or 1 | Enables Last Man Standing victory |
| `win_cond_armistice` | INTEGER | `1` | 0 or 1 | Enables Unanimous Armistice victory |
| `armistice_threshold` | REAL | `1.0` | $0.5 - 1.0$ | Living voter ratio required for peace |
| `win_cond_rogue` | INTEGER | `1` | 0 or 1 | Enables Rogue Traitor rule |
| `rogue_resolution_mode` | TEXT | `GRANT_AP` | Enum | `GRANT_AP`, `INSTANT_WIN`, `REVEAL_IDENTITY` |
| `rogue_bonus_ap` | INTEGER | `3` | $1 - 10$ | Bonus AP awarded in `GRANT_AP` mode |
| `win_cond_stagnation` | INTEGER | `0` | 0 or 1 | Enables inactivity stagnation clock |
| `stagnation_days_limit` | INTEGER | `5` | $1 - 30$ | Days without combat before stagnation |
| `stagnation_mode` | TEXT | `DRAW` | Enum | `DRAW` or `SUDDEN_DEATH` |
| `reveal_killer_on_death` | INTEGER | `0` | 0 or 1 | Display killer username in public notices |
| `allow_dead_chat` | INTEGER | `1` | 0 or 1 | Permits eliminated players to chat |
| `fog_of_war_stats` | INTEGER | `1` | 0 or 1 | Enforces stats omission on opponents |
| `require_email_verify` | INTEGER | `0` | 0 or 1 | Requires email verification for signup |
| `audit_log_public` | INTEGER | `1` | 0 or 1 | Publishes setting edits to changelog |

---

## 8. Cryptographic Audit Trail & Event Ledger

### 8.1 SHA-256 Hash Chaining
Every validated event is assigned a monotonically increasing `event_seq` and appended to the `events` table:
* Genesis Event ($seq=1$): `prev_hash = "0" * 64`.
* Subsequent Events ($seq=k$):
  $$\text{hash}_k = \text{SHA-256}\big(seq_k \;\|\; \text{prev\_hash}_k \;\|\; \text{timestamp\_ns}_k \;\|\; \text{game\_id} \;\|\; \text{event\_type} \;\|\; \text{payload\_json}\big)$$
* Cryptographic proof (`verify_ledger_chain`) verifies the entire history. Any retroactive tampering with past events invalidates subsequent hashes.

### 8.2 Parameter Audit Logs (`game_parameter_audit_logs`)
Any administrative parameter alteration made after game creation is logged with:
* UTC timestamp
* Parameter key
* Old value $\to$ New value
* Admin username

---

## 9. Security & Identity Specifications

1. **Password Hashing:**
   * Argon2id ($m = 65536 \text{ KiB} = 64 \text{ MiB}, t = 3 \text{ iterations}, p = 4 \text{ lanes}$).
   * Constant-time comparison via `hmac.compare_digest`.
2. **Session Cookies:**
   * Signed token: `<uuid>.<hmac-sha256>`.
   * Flags: `HttpOnly`, `SameSite=Lax`, encrypted/signed.
3. **Rate Limiting:**
   * Sliding-window token bucket tracking IP + username. Max 5 failed attempts per 15 minutes.
4. **Avatar Validation & Sanitization:**
   * Allowed extensions: `.png`, `.jpg`, `.webp`. Max size: 2 MB.
   * Magic-byte header verification (rejecting spoofed MIME types).
   * Pillow sanitization: Strips all EXIF metadata, resizes with Lanczos filter to standard $128 \times 128$ px, re-encodes to clean PNG.

---

## 10. API Specification Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/auth/register` | Create account with Argon2id hash |
| `POST` | `/api/auth/login` | Authenticate with rate limiting & set session cookie |
| `POST` | `/api/auth/logout` | Invalidate session in DB and clear cookie |
| `GET` | `/api/auth/me` | Fetch active user profile |
| `POST` | `/api/auth/avatar` | Upload & sanitize custom avatar ($128\times128$) |
| `GET` | `/api/flags` | List ISO 3166-1 alpha-2 flags registry |
| `GET` | `/api/games` | List all open and active game lobbies |
| `POST` | `/api/games` | Create new lobby with custom parameters |
| `GET` | `/api/games/{id}` | Fetch board state with Fog of War filtering |
| `POST` | `/api/games/{id}/join` | Join lobby and receive random unoccupied coordinate |
| `POST` | `/api/games/{id}/start` | Transition lobby to ACTIVE |
| `POST` | `/api/games/{id}/actions` | Submit batch commands (FIFO queue & burst limit) |
| `POST` | `/api/games/{id}/armistice` | Toggle peace vote (evaluates armistice & rogue rule) |
| `GET` | `/api/games/{id}/diff` | "Since Last Visit" offline ghost trails & events |
| `GET` | `/api/games/{id}/replay` | Historical board reconstruction & cryptographic proof |
| `GET` | `/api/games/{id}/audit` | Settings changelog audit history |
| `PATCH` | `/api/games/{id}/config` | Admin mid-game parameter updates (audited) |
| `GET` | `/api/rules` | Authoritative bilingual rulebook text |
| `GET` | `/api/rules/active/{id}` | Dynamic active rules summary for specific lobby |
| `GET` | `/api/notifications` | In-app notification center feed & unread count |
| `POST` | `/api/notifications/read` | Mark notifications as read |
| `GET` | `/api/push/key` | Retrieve VAPID public key |
| `POST` | `/api/push/subscribe` | Register browser push subscription |
| `WS` | `/ws/game/{id}` | Real-time WebSocket connection (keepalive ping/pong) |

---

## 11. Extension Points for Developers & Agents

1. **Adding a New Action Type:**
   * Register enum in `app/models/game_types.py` (`ActionType`).
   * Add cost calculation rule in `app/engine/actions.py` (`calculate_action_cost`).
   * Implement validation and state transition block in `app/engine/actions.py` (`execute_action_batch`).
   * Add radial button in `app/static/js/radial_dock.js`.
2. **Adding a New Win Condition:**
   * Add toggle setting in `game_parameters` schema (`app/database.py` and `LobbyConfig`).
   * Implement evaluation function in `app/engine/win_conditions.py`.
   * Call evaluator inside `execute_action_batch` and `app/scheduler/cron_worker.py`.
3. **Database Schema Migrations:**
   * The schema is defined in `init_db()` within `app/database.py`. Use standard `ALTER TABLE` statements for forward migrations.
