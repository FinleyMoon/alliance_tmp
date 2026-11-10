"""Strict Concurrency & FIFO Action Engine: centralized per-game in-memory queue."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.database import db_transaction
from app.engine.actions import execute_action_batch

# Global dictionary of per-game asyncio.Queue and worker tasks
_game_queues: Dict[str, asyncio.Queue[ActionRequest]] = {}
_game_workers: Dict[str, asyncio.Task[None]] = {}
_request_counter: int = 0


class ActionRequest:
    """Envelope for incoming action requests."""
    def __init__(
        self,
        game_id: str,
        initiator_user_id: str,
        actions: List[Dict[str, Any]],
        expected_seq: Optional[int] = None,
        as_player_id: Optional[str] = None
    ):
        global _request_counter
        _request_counter += 1
        self.request_id = f"req_{_request_counter}_{uuid.uuid4().hex[:8]}"
        self.game_id = game_id
        self.initiator_user_id = initiator_user_id
        self.actions = actions
        self.expected_seq = expected_seq
        self.as_player_id = as_player_id
        # Precise nanosecond timestamping upon API ingress
        self.arrival_time_ns = time.monotonic_ns()
        self.created_at_iso = datetime.now(timezone.utc).isoformat()
        # Future for caller awaiting serialized execution result
        self.future: Optional[asyncio.Future[Dict[str, Any]]] = None
        try:
            self.future = asyncio.get_running_loop().create_future()
        except RuntimeError:
            self.future = None


async def _game_worker_loop(game_id: str, queue: asyncio.Queue[ActionRequest]) -> None:
    """Dedicated single worker consumer processing commands strictly one by one."""
    while True:
        try:
            req: ActionRequest = await queue.get()
            try:
                # Execute inside immediate serialized SQLite transaction
                with db_transaction() as conn:
                    success, msg_or_code, receipts, total_ap = execute_action_batch(
                        conn=conn,
                        game_id=req.game_id,
                        initiator_user_id=req.initiator_user_id,
                        actions_raw=req.actions,
                        timestamp_ns=req.arrival_time_ns,
                        now_iso=req.created_at_iso,
                        expected_seq=req.expected_seq,
                        as_player_id=req.as_player_id
                    )

                result: Dict[str, Any] = {
                    "request_id": req.request_id,
                    "success": success,
                    "message": msg_or_code,
                    "receipts": receipts,
                    "total_ap_spent": total_ap,
                    "arrival_time_ns": req.arrival_time_ns
                }
                fut = req.future
                if fut is not None and not fut.done():
                    fut.set_result(result)

            except Exception as ex:
                fut = req.future
                if fut is not None and not fut.done():
                    fut.set_exception(ex)
            finally:
                queue.task_done()

        except asyncio.CancelledError:
            break
        except Exception:
            # Continue running worker loop despite individual unhandled failures
            await asyncio.sleep(0.01)


def get_game_queue(game_id: str) -> asyncio.Queue[ActionRequest]:
    """Get or create per-game centralized FIFO queue with running single-worker task."""
    loop = asyncio.get_running_loop()
    if game_id not in _game_queues or game_id not in _game_workers or _game_workers[game_id].done():
        queue: asyncio.Queue[ActionRequest] = asyncio.Queue()
        _game_queues[game_id] = queue
        worker_task = loop.create_task(_game_worker_loop(game_id, queue))
        _game_workers[game_id] = worker_task
    return _game_queues[game_id]


async def submit_action_to_queue(
    game_id: str,
    initiator_user_id: str,
    actions: List[Dict[str, Any]],
    timeout_seconds: float = 30.0,
    expected_seq: Optional[int] = None,
    as_player_id: Optional[str] = None
) -> Dict[str, Any]:
    """Enqueue action request into strict FIFO pipeline and await serialized execution receipt."""
    queue = get_game_queue(game_id)
    req = ActionRequest(game_id, initiator_user_id, actions, expected_seq=expected_seq, as_player_id=as_player_id)
    fut = req.future
    if fut is None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req.future = fut
    await queue.put(req)

    # Await serialized resolution
    result = await asyncio.wait_for(fut, timeout=timeout_seconds)
    return result
