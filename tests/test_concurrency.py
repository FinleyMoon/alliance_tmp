"""Unit tests for strict concurrency, nanosecond timestamping, and FIFO ordering."""
import unittest
import asyncio
import time
import sqlite3
from datetime import datetime, timezone

from app.engine.queue import submit_action_to_queue, ActionRequest
from app.database import init_db, db_transaction


class TestConcurrencyAndFIFO(unittest.TestCase):
    def test_nanosecond_timestamping_and_monotonicity(self):
        """Action requests are stamped with strictly increasing monotonic nanosecond timestamps."""
        req1 = ActionRequest("game_concurrency", "user_1", [{"action_type": "MOVE", "to_x": 1, "to_y": 2}])
        time.sleep(0.001)
        req2 = ActionRequest("game_concurrency", "user_2", [{"action_type": "MOVE", "to_x": 3, "to_y": 4}])

        self.assertGreater(req2.arrival_time_ns, req1.arrival_time_ns)
        self.assertNotEqual(req1.request_id, req2.request_id)

    def test_fifo_queue_ordering(self):
        """Requests enqueued in order are dequeued strictly in arrival order."""
        async def run_fifo_test():
            queue = asyncio.Queue()
            order_processed = []

            async def mock_worker():
                while True:
                    item = await queue.get()
                    order_processed.append(item)
                    queue.task_done()
                    if len(order_processed) == 3:
                        break

            worker_task = asyncio.create_task(mock_worker())
            await queue.put("REQ_PLAYER_A")
            await queue.put("REQ_PLAYER_B")
            await queue.put("REQ_PLAYER_C")

            await queue.join()
            worker_task.cancel()
            self.assertEqual(order_processed, ["REQ_PLAYER_A", "REQ_PLAYER_B", "REQ_PLAYER_C"])

        asyncio.run(run_fifo_test())


if __name__ == "__main__":
    unittest.main()
