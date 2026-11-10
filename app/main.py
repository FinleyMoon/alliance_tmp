"""FastAPI / ASGI Web Application Entry Point with WebSocket and Static Delivery."""
import os
import json
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from app.config import (
    BASE_DIR,
    DATA_DIR,
    UPLOADS_DIR,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    get_server_now,
    get_server_timezone,
    get_server_timezone_name,
)
from app.database import init_db, query_db, query_one, db_transaction
from app.security import (
    get_user_from_session_cookie,
    destroy_session,
    hash_password,
    verify_password,
)
from app.api.routes_auth import register_user, authenticate_user, delete_user_account
from app.avatar import sanitize_and_save_avatar
from app.flags import ISO_FLAGS, code_to_emoji
from app.models.game_types import LobbyConfig, ActionBatchPayload
from app.models.schemas import (
    CreateGameRequest,
    UpdateGameConfigRequest,
    UserPasswordChangeRequest,
    UserProfileUpdateRequest,
    ConferenceVoteRequest,
    PlaytestAwardApRequest,
    PlaytestControlBotRequest,
    RecreateGameRequest,
    MapVoteRequest,
)
from app.services.game_service import (
    create_game_lobby,
    join_game_lobby,
    start_game,
    remove_player_from_lobby,
    add_dummy_player,
    get_game_state,
    toggle_armistice_vote,
    cast_conference_vote,
    get_board_diff_since_last_visit,
    reconstruct_board_at_sequence,
    delete_game_lobby,
    terminate_game_prematurely,
    award_playtest_ap,
    control_playtest_bot,
    recreate_game_lobby,
    get_first_ap_drop_cutoff,
    create_join_request,
    get_game_join_requests,
    approve_join_request,
    deny_join_request,
    get_map_voting_state,
    cast_map_vote,
    resolve_map_rcv_vote,
)
from app.services.backup_service import export_database_backup, restore_database_backup
from app.engine.queue import submit_action_to_queue
from app.engine.rules import get_official_rulebook, generate_active_rules_summary
from app.engine.ledger import verify_ledger_chain
from app.services.audit_service import get_game_audit_logs, update_game_parameters_with_audit
from app.services.notification_service import (
    get_user_notifications,
    mark_notifications_read,
    ws_manager,
    create_in_app_notification,
    get_user_alert_preferences,
    update_user_alert_preferences
)
from app.services.push_service import (
    get_or_create_vapid_keys,
    save_push_subscription,
    get_user_push_subscriptions,
    delete_push_subscription,
    revoke_all_push_subscriptions,
    send_web_push_notification,
)
from app.scheduler.cron_worker import scheduler_loop

STATIC_DIR = BASE_DIR / "app" / "static"

try:
    from fastapi import (
        FastAPI,
        Request,
        Response,
        HTTPException,
        Depends,
        WebSocket,
        WebSocketDisconnect,
        UploadFile,
        File,
        status,
    )
    from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        get_or_create_vapid_keys()
        sched_task = None
        # Only run embedded task if supervisor is not running standalone cron_worker
        if os.getenv("DISABLE_EMBEDDED_SCHEDULER", "0") != "1":
            sched_task = asyncio.create_task(scheduler_loop())
        yield
        if sched_task is not None:
            sched_task.cancel()
            try:
                await sched_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Alliance // Automated Game Master",
        version="1.0.0",
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if UPLOADS_DIR.exists():
        app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

    def get_client_ip(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "127.0.0.1"

    def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
        cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
        return get_user_from_session_cookie(cookie_val)

    def get_current_user(request: Request) -> Dict[str, Any]:
        user = get_current_user_optional(request)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user

    @app.get("/")
    async def root():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"status": "ALLIANCE_ONLINE", "version": "1.0.0"}

    @app.get("/sw.js")
    async def get_service_worker():
        sw_file = STATIC_DIR / "sw.js"
        if not sw_file.exists():
            raise HTTPException(status_code=404, detail="Service worker script not found")
        return FileResponse(
            sw_file,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"}
        )

    @app.get("/api/health")
    async def health():
        return {"status": "HEALTHY", "db_wal": True}

    @app.get("/api/system/info")
    async def api_system_info():
        setting = query_one("SELECT value FROM system_settings WHERE key = 'registrations_enabled'")
        reg_enabled = (setting is None or setting["value"] == "1")
        return {"registrations_enabled": reg_enabled}

    @app.get("/api/time")
    async def api_server_time():
        now_server = get_server_now()
        now_utc = datetime.now(timezone.utc)
        tz_offset_sec = now_server.utcoffset().total_seconds() if now_server.utcoffset() else 0
        return {
            "iso": now_server.isoformat(),
            "timestamp_ms": int(now_server.timestamp() * 1000),
            "time_str": now_server.strftime("%H:%M:%S"),
            "time_exact": f"{now_server.strftime('%H:%M:%S')}.{int(now_server.microsecond / 100000)}",
            "timezone": get_server_timezone_name(),
            "tz_offset_ms": int(tz_offset_sec * 1000),
            "utc_iso": now_utc.isoformat()
        }

    # ---------------- Auth Endpoints ----------------
    @app.post("/api/auth/register")
    async def api_register(request: Request, payload: Dict[str, Any], response: Response):
        client_ip = get_client_ip(request)
        try:
            user = register_user(
                username=payload.get("username", ""),
                password=payload.get("password", ""),
                national_flag=payload.get("national_flag", "UN"),
                preferred_lang=payload.get("preferred_lang", "en"),
                invite_code=payload.get("invite_code")
            )
            auth_result = authenticate_user(
                username=payload.get("username", ""),
                password=payload.get("password", ""),
                ip_address=client_ip
            )
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=auth_result["session_cookie"],
                max_age=SESSION_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=False
            )
            return {"user": auth_result["user"]}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/login")
    async def api_login(request: Request, payload: Dict[str, Any], response: Response):
        client_ip = get_client_ip(request)
        try:
            auth_result = authenticate_user(
                username=payload.get("username", ""),
                password=payload.get("password", ""),
                ip_address=client_ip
            )
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=auth_result["session_cookie"],
                max_age=SESSION_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=False
            )
            return {"user": auth_result["user"]}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/logout")
    async def api_logout(request: Request, response: Response):
        cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
        destroy_session(cookie_val)
        response.delete_cookie(key=SESSION_COOKIE_NAME)
        return {"message": "Logged out successfully"}

    @app.get("/api/auth/me")
    async def api_me(user: Dict[str, Any] = Depends(get_current_user)):
        return {"user": user}

    @app.post("/api/auth/avatar")
    async def api_upload_avatar(request: Request, file: UploadFile = File(...), user: Dict[str, Any] = Depends(get_current_user)):
        content = await file.read()
        success, msg, rel_path = sanitize_and_save_avatar(content, file.filename or "avatar.png")
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        with db_transaction() as conn:
            conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (rel_path, user["id"]))
        return {"avatar_path": rel_path}

    @app.delete("/api/auth/avatar")
    @app.post("/api/auth/avatar/reset")
    async def api_reset_avatar(user: Dict[str, Any] = Depends(get_current_user)):
        with db_transaction() as conn:
            conn.execute("UPDATE users SET avatar_path = NULL WHERE id = ?", (user["id"],))
        return {"avatar_path": None, "status": "RESET"}

    @app.post("/api/auth/password")
    async def api_change_password(payload: UserPasswordChangeRequest, user: Dict[str, Any] = Depends(get_current_user)):
        user_row = query_one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
        if not user_row or not verify_password(payload.old_password, user_row["password_hash"]):
            raise HTTPException(status_code=400, detail="Invalid current password")
        if len(payload.new_password) < 6:
            raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
        new_hash = hash_password(payload.new_password)
        with db_transaction() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
        return {"status": "SUCCESS", "message": "Password changed successfully"}

    @app.patch("/api/auth/profile")
    async def api_update_profile(payload: UserProfileUpdateRequest, user: Dict[str, Any] = Depends(get_current_user)):
        updates = []
        params = []
        new_username = None
        if payload.username is not None:
            clean_name = payload.username.strip()
            if len(clean_name) < 3 or len(clean_name) > 30:
                raise HTTPException(status_code=400, detail="Username must be between 3 and 30 characters")
            existing = query_one("SELECT id FROM users WHERE username = ? AND id != ?", (clean_name, user["id"]))
            if existing:
                raise HTTPException(status_code=400, detail="Username already claimed by another player")
            updates.append("username = ?")
            params.append(clean_name)
            new_username = clean_name
        if payload.national_flag:
            updates.append("national_flag = ?")
            params.append(code_to_emoji(payload.national_flag))
        if payload.preferred_lang:
            updates.append("preferred_lang = ?")
            params.append(payload.preferred_lang)
        if updates:
            params.append(user["id"])
            with db_transaction() as conn:
                conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(params))
                if new_username:
                    conn.execute("UPDATE players SET username = ? WHERE user_id = ?", (new_username, user["id"]))
        updated_user = query_one("SELECT id, username, national_flag, avatar_path, preferred_lang, is_admin FROM users WHERE id = ?", (user["id"],))
        return {"user": dict(updated_user)}

    @app.delete("/api/auth/account")
    @app.post("/api/auth/account/delete")
    async def api_delete_account(user: Dict[str, Any] = Depends(get_current_user)):
        delete_user_account(user["id"])
        return {"status": "DELETED", "message": "Account successfully deleted"}

    @app.get("/api/flags")
    async def api_flags():
        return {"flags": ISO_FLAGS}

    # ---------------- Game Lobby Endpoints ----------------
    @app.get("/api/games")
    async def api_list_games():
        games = query_db(
            """
            SELECT g.id, g.name, g.status, g.created_at, g.created_by, g.is_playtest, u.username as creator_name,
                   (SELECT COUNT(*) FROM players WHERE game_id = g.id) as player_count,
                   COALESCE(gp.allow_late_join, 0) as allow_late_join,
                   COALESCE(gp.map_voting_enabled, 0) as map_voting_enabled
            FROM games g
            JOIN users u ON g.created_by = u.id
            LEFT JOIN game_parameters gp ON g.id = gp.game_id
            ORDER BY g.created_at DESC
            """
        )
        return {"games": [dict(g) for g in games]}

    @app.post("/api/games")
    async def api_create_game(payload: CreateGameRequest, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required to create game lobbies")
        game_id = create_game_lobby(name=payload.name, creator_user_id=user["id"], config=payload.config, is_playtest=payload.is_playtest)
        return {"game_id": game_id}

    @app.delete("/api/games/{game_id}")
    async def api_delete_game(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required to delete game lobbies")
        try:
            delete_game_lobby(game_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {"type": "LOBBY_DELETED", "game_id": game_id})
            return {"status": "DELETED", "game_id": game_id}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/terminate")
    async def api_terminate_game(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required to terminate games early")
        try:
            terminate_game_prematurely(game_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {"type": "GAME_ENDED", "condition": "ADMIN_TERMINATED"})
            return {"status": "ENDED", "game_id": game_id}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/playtest/award-ap")
    async def api_playtest_award_ap(game_id: str, payload: PlaytestAwardApRequest, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required for playtest awards")
        try:
            award_playtest_ap(game_id, user["id"], payload.amount)
            await ws_manager.broadcast_to_game(game_id, {"type": "BOARD_UPDATE"})
            return {"status": "SUCCESS", "awarded_ap": payload.amount}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/playtest/control-bot")
    async def api_playtest_control_bot(game_id: str, payload: PlaytestControlBotRequest, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required to control bots")
        try:
            raw_actions = None
            if payload.actions is not None:
                raw_actions = [a if isinstance(a, dict) else a.model_dump() for a in payload.actions]
            elif payload.action_batch is not None:
                batch = payload.action_batch
                if isinstance(batch, dict) and "actions" in batch:
                    raw_actions = batch["actions"]
                elif isinstance(batch, list):
                    raw_actions = batch
                else:
                    raw_actions = []
            else:
                raw_actions = []

            res = await control_playtest_bot(
                game_id=game_id,
                admin_user_id=user["id"],
                bot_player_id=payload.bot_player_id,
                actions=raw_actions,
                action_batch=payload.action_batch,
                expected_seq=payload.expected_seq
            )
            if not res["success"]:
                raise HTTPException(status_code=400, detail=res["message"])
            new_state = get_game_state(game_id, current_user_id=user["id"])
            await ws_manager.broadcast_to_game(game_id, {"type": "BOARD_UPDATE", "event_seq": new_state["last_event_seq"]})
            return {"receipt": res, "updated_state": new_state}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/games/{game_id}")
    async def api_get_game(game_id: str, request: Request):
        user = get_current_user_optional(request)
        user_id = user["id"] if user else None
        try:
            state = get_game_state(game_id, current_user_id=user_id)
            return state
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/games/{game_id}/join")
    async def api_join_game(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            player_info = join_game_lobby(game_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {"type": "PLAYER_JOINED", "username": user["username"]})
            return player_info
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/remove-player/{player_id}")
    async def api_remove_player(game_id: str, player_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = remove_player_from_lobby(game_id, user["id"], player_id)
            await ws_manager.broadcast_to_game(game_id, {"type": "PLAYER_REMOVED", "player_id": player_id})
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/start")
    async def api_start_game(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = start_game(game_id, user["id"])
            status = res.get("status", "ACTIVE") if isinstance(res, dict) else "ACTIVE"
            event_type = "MAP_VOTING_STARTED" if status == "MAP_VOTING" else "GAME_STARTED"
            await ws_manager.broadcast_to_game(game_id, {"type": event_type, "status": status})
            return res if isinstance(res, dict) else {"status": status}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/recreate")
    async def api_recreate_game(game_id: str, payload: RecreateGameRequest, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = recreate_game_lobby(
                source_game_id=game_id,
                user_id=user["id"],
                name=payload.name,
                config=payload.config,
                is_playtest=payload.is_playtest
            )
            await ws_manager.broadcast_to_game(game_id, {
                "type": "LOBBY_RECREATED",
                "new_game_id": res["new_game_id"],
                "name": res["name"]
            })
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/games/{game_id}/late-join-status")
    async def api_get_late_join_status(game_id: str):
        allowed, reason, cutoff_at = get_first_ap_drop_cutoff(game_id)
        return {"allowed": allowed, "reason": reason, "cutoff_at": cutoff_at}

    @app.post("/api/games/{game_id}/join-request")
    async def api_create_join_request(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = create_join_request(game_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {
                "type": "JOIN_REQUEST_CREATED",
                "username": user["username"]
            })
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/games/{game_id}/join-requests")
    async def api_get_join_requests(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        return {"requests": get_game_join_requests(game_id)}

    @app.post("/api/games/{game_id}/join-requests/{request_id}/approve")
    async def api_approve_join_request(game_id: str, request_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = approve_join_request(game_id, request_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {
                "type": "PLAYER_JOINED",
                "username": res["username"],
                "late_join": True
            })
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/join-requests/{request_id}/deny")
    async def api_deny_join_request(game_id: str, request_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = deny_join_request(game_id, request_id, user["id"])
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/games/{game_id}/map-voting")
    async def api_get_map_voting(game_id: str, request: Request):
        user = get_current_user_optional(request)
        try:
            return get_map_voting_state(game_id, user["id"] if user else None)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/games/{game_id}/map-vote")
    async def api_cast_map_vote(game_id: str, payload: MapVoteRequest, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = cast_map_vote(game_id, user["id"], payload.ranks)
            await ws_manager.broadcast_to_game(game_id, {
                "type": "MAP_VOTE_CAST",
                "user_id": user["id"],
                "voted_count": res.get("voted_count", 0),
                "total_players": res.get("total_players", 0)
            })
            if res.get("status") == "ACTIVE":
                await ws_manager.broadcast_to_game(game_id, {
                    "type": "MAP_VOTING_RESOLVED",
                    "status": "ACTIVE"
                })
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/map-voting/resolve")
    async def api_resolve_map_voting(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = resolve_map_rcv_vote(game_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {
                "type": "MAP_VOTING_RESOLVED",
                "status": "ACTIVE",
                "winner_option_idx": res.get("winner_option_idx")
            })
            return res
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/actions")
    async def api_submit_actions(game_id: str, payload: ActionBatchPayload, user: Dict[str, Any] = Depends(get_current_user)):
        raw_actions = [a.model_dump() for a in payload.actions]
        result = await submit_action_to_queue(game_id, user["id"], raw_actions, expected_seq=payload.expected_seq)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        new_state = get_game_state(game_id, current_user_id=user["id"])
        await ws_manager.broadcast_to_game(game_id, {"type": "BOARD_UPDATE", "event_seq": new_state["last_event_seq"]})

        return {
            "receipt": result,
            "updated_state": new_state
        }

    @app.post("/api/games/{game_id}/armistice")
    async def api_armistice_vote(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = toggle_armistice_vote(game_id, user["id"])
            await ws_manager.broadcast_to_game(game_id, {"type": "ARMISTICE_UPDATE", "user_id": user["id"], "result": res})
            return res
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/games/{game_id}/conference/vote")
    async def api_conference_vote(game_id: str, payload: ConferenceVoteRequest, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            res = cast_conference_vote(game_id, user["id"], payload.vote)
            await ws_manager.broadcast_to_game(game_id, {
                "type": "CONFERENCE_VOTE",
                "user_id": user["id"],
                "username": user["username"],
                "all_voted": res["all_voted"],
                "outcome": res["outcome"]
            })
            return res
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/games/{game_id}/diff")
    async def api_game_diff(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        diff = get_board_diff_since_last_visit(game_id, user["id"])
        return diff

    @app.get("/api/games/{game_id}/replay")
    async def api_game_replay(game_id: str, request: Request, seq: Optional[int] = None):
        if seq is not None:
            return reconstruct_board_at_sequence(game_id, seq)
        user = get_current_user_optional(request)
        u_id = user["id"] if user else None
        if u_id:
            events = query_db(
                "SELECT * FROM events WHERE game_id = ? AND (visibility = 'PUBLIC' OR initiator_user_id = ? OR target_user_id = ?) ORDER BY event_seq ASC",
                (game_id, u_id, u_id)
            )
        else:
            events = query_db(
                "SELECT * FROM events WHERE game_id = ? AND visibility = 'PUBLIC' ORDER BY event_seq ASC",
                (game_id,)
            )
        ev_list = [dict(e) for e in events]
        for e in ev_list:
            e["payload"] = json.loads(e["payload_json"])
            if e["event_type"] == "COMBAT_HIT":
                if u_id != e["initiator_user_id"]:
                    e["initiator_user_id"] = None
                    if "attacker_user_id" in e["payload"]:
                        e["payload"]["attacker_user_id"] = None
        is_valid, proof_msg = verify_ledger_chain(ev_list)
        return {
            "events": ev_list,
            "cryptographic_proof": {
                "valid": is_valid,
                "message": proof_msg
            }
        }

    @app.get("/api/games/{game_id}/audit")
    async def api_game_audit(game_id: str):
        logs = get_game_audit_logs(game_id)
        return {"audit_logs": logs}

    @app.patch("/api/games/{game_id}/config")
    async def api_update_config(game_id: str, payload: UpdateGameConfigRequest, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required to alter lobby parameters")
        logs = update_game_parameters_with_audit(
            game_id=game_id,
            admin_user_id=user["id"],
            admin_username=user["username"],
            updates=payload.updates
        )
        return {"updated_logs": logs}

    # ---------------- Comprehensive Admin Management Endpoints ----------------
    @app.get("/api/admin/settings")
    async def api_admin_get_settings(user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        setting = query_one("SELECT value FROM system_settings WHERE key = 'registrations_enabled'")
        reg_enabled = (setting is None or setting["value"] == "1")
        u_cnt = query_one("SELECT COUNT(*) as c FROM users")["c"]
        g_cnt = query_one("SELECT COUNT(*) as c FROM games")["c"]
        return {
            "registrations_enabled": reg_enabled,
            "total_users": u_cnt,
            "total_games": g_cnt
        }

    @app.post("/api/admin/settings")
    async def api_admin_update_settings(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        if "registrations_enabled" in payload:
            val = "1" if payload["registrations_enabled"] else "0"
            with db_transaction() as conn:
                conn.execute(
                    "INSERT INTO system_settings (key, value) VALUES ('registrations_enabled', ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                    (val, val)
                )
        return {"status": "SUCCESS"}

    @app.get("/api/admin/invites")
    async def api_admin_get_invites(user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        rows = query_db("SELECT * FROM invite_tokens ORDER BY created_at DESC")
        return {"invites": [dict(r) for r in rows]}

    @app.post("/api/admin/invites")
    async def api_admin_create_invite(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        token = payload.get("token") or f"INV-{uuid.uuid4().hex[:8].upper()}"
        uses_left = int(payload.get("uses", 1))
        days = int(payload.get("days", 7))
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat() if days > 0 else None
        now_iso = datetime.now(timezone.utc).isoformat()
        with db_transaction() as conn:
            conn.execute(
                """
                INSERT INTO invite_tokens (token, created_by, created_at, expires_at, uses_left, used_count)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (token, user["username"], now_iso, expires_at, uses_left)
            )
        return {"token": token, "expires_at": expires_at, "uses_left": uses_left}

    @app.delete("/api/admin/invites/{token}")
    async def api_admin_delete_invite(token: str, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        with db_transaction() as conn:
            conn.execute("DELETE FROM invite_tokens WHERE token = ?", (token,))
        return {"status": "DELETED"}

    @app.get("/api/admin/users")
    async def api_admin_get_users(user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        users = query_db(
            """
            SELECT u.id, u.username, u.national_flag, u.is_admin, u.preferred_lang, u.created_at,
                   (SELECT COUNT(*) FROM players WHERE user_id = u.id) as games_count
            FROM users u
            WHERE (u.is_bot = 0 OR u.is_bot IS NULL) AND u.password_hash != 'dummy_hash'
            ORDER BY u.created_at DESC
            """
        )
        return {"users": [dict(u) for u in users]}

    @app.patch("/api/admin/users/{target_id}")
    async def api_admin_update_user(target_id: str, payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        if "is_admin" in payload:
            is_adm = 1 if payload["is_admin"] else 0
            with db_transaction() as conn:
                conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (is_adm, target_id))
        return {"status": "UPDATED"}

    @app.delete("/api/admin/users/{target_id}")
    async def api_admin_delete_user(target_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        if target_id == user["id"]:
            raise HTTPException(status_code=400, detail="Cannot delete your own admin account")
        with db_transaction() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (target_id,))
        return {"status": "DELETED"}

    @app.get("/api/admin/backup")
    async def api_admin_backup(user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        return export_database_backup()

    @app.post("/api/admin/restore")
    async def api_admin_restore(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin permissions required")
        success, msg = restore_database_backup(payload)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        return {"status": "SUCCESS", "message": msg}

    @app.post("/api/games/{game_id}/dummy")
    async def api_add_dummy(game_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        try:
            bot = add_dummy_player(game_id)
            await ws_manager.broadcast_to_game(game_id, {"type": "PLAYER_JOINED", "username": bot["username"]})
            return bot
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/i18n/{lang}")
    async def api_get_i18n(lang: str):
        safe_lang = "ru" if lang.startswith("ru") else "en"
        i18n_path = BASE_DIR / "app" / "i18n" / f"{safe_lang}.json"
        if i18n_path.exists():
            with open(i18n_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # ---------------- Rules & Notifications ----------------
    @app.get("/api/rules")
    async def api_rules(lang: str = "en"):
        return get_official_rulebook(lang)

    @app.get("/api/rules/active/{game_id}")
    async def api_active_rules(game_id: str, lang: str = "en"):
        cfg_row = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
        if not cfg_row:
            raise HTTPException(status_code=404, detail="Game parameters not found")
        return generate_active_rules_summary(dict(cfg_row), lang)

    @app.get("/api/notifications")
    async def api_notifications(lang: str = "en", user: Dict[str, Any] = Depends(get_current_user)):
        return get_user_notifications(user["id"], lang=lang)

    @app.post("/api/notifications/read")
    async def api_mark_notifications_read(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        ids = payload.get("notification_ids")
        mark_notifications_read(user["id"], ids)
        return {"status": "SUCCESS"}

    @app.get("/api/user/alert-preferences")
    async def api_get_alert_preferences(user: Dict[str, Any] = Depends(get_current_user)):
        return get_user_alert_preferences(user["id"])

    @app.post("/api/user/alert-preferences")
    async def api_update_alert_preferences(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        return update_user_alert_preferences(user["id"], payload)

    @app.get("/api/push/key")
    async def api_push_key():
        pub_key, _ = get_or_create_vapid_keys()
        return {"vapid_public_key": pub_key}

    @app.post("/api/push/subscribe")
    async def api_push_subscribe(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
        endpoint = payload.get("endpoint")
        keys = payload.get("keys", {})
        if not endpoint or "p256dh" not in keys or "auth" not in keys:
            raise HTTPException(status_code=400, detail="Invalid push subscription data")
        save_push_subscription(user["id"], endpoint, keys["p256dh"], keys["auth"])
        return {"status": "SUBSCRIBED"}

    @app.get("/api/push/subscriptions")
    async def api_get_push_subscriptions(user: Dict[str, Any] = Depends(get_current_user)):
        return {"subscriptions": get_user_push_subscriptions(user["id"])}

    @app.delete("/api/push/subscriptions/{sub_id}")
    async def api_delete_push_subscription(sub_id: str, user: Dict[str, Any] = Depends(get_current_user)):
        success = delete_push_subscription(user["id"], sub_id)
        return {"status": "DELETED" if success else "NOT_FOUND"}

    @app.post("/api/push/subscriptions/revoke-all")
    async def api_revoke_all_push_subscriptions(user: Dict[str, Any] = Depends(get_current_user)):
        count = revoke_all_push_subscriptions(user["id"])
        return {"status": "REVOKED", "count": count}

    @app.post("/api/push/test")
    async def api_push_test(user: Dict[str, Any] = Depends(get_current_user)):
        dispatched = send_web_push_notification(
            user["id"],
            title="Tactical Test Notification",
            body="Encrypted push notifications are verified and active on this device!",
            data={"type": "TEST_ALERT"}
        )
        return {"status": "SENT", "dispatched": dispatched, "devices_notified": dispatched}

    # ---------------- WebSocket ----------------
    @app.websocket("/ws/game/{game_id}")
    async def websocket_game_endpoint(websocket: WebSocket, game_id: str):
        await websocket.accept()
        cookie_val = websocket.cookies.get(SESSION_COOKIE_NAME)
        user = get_user_from_session_cookie(cookie_val)
        user_id = user["id"] if user else f"anon_{uuid.uuid4().hex[:6]}"

        ws_manager.connect(game_id, user_id, websocket)
        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    data = json.loads(data_text)
                    if data.get("type") == "PING":
                        await websocket.send_text(json.dumps({"type": "PONG"}))
                    elif data.get("type") == "CHAT" and user:
                        cfg = query_one("SELECT allow_dead_chat FROM game_parameters WHERE game_id = ?", (game_id,))
                        player = query_one("SELECT is_alive FROM players WHERE game_id = ? AND user_id = ?", (game_id, user["id"]))
                        is_dead = (player and player["is_alive"] == 0)

                        if is_dead and cfg and cfg["allow_dead_chat"] == 0:
                            await websocket.send_text(json.dumps({
                                "type": "ERROR",
                                "message": "Dead chat is disabled in this lobby."
                            }))
                        else:
                            msg_payload = {
                                "type": "CHAT",
                                "username": user["username"],
                                "is_dead": bool(is_dead),
                                "message": data.get("message", "").strip()[:500],
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            }
                            await ws_manager.broadcast_to_game(game_id, msg_payload)
                except Exception:
                    pass
        except WebSocketDisconnect:
            ws_manager.disconnect(game_id, user_id, websocket)

except ImportError:
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    init_db()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            response_body = json.dumps({"status": "ALLIANCE_FALLBACK_OK"}).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")]
            })
            await send({
                "type": "http.response.body",
                "body": response_body
            })
