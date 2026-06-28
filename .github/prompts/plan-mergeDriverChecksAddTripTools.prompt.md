## Plan: Merge driver checks & add trip modify/delete tools

TL;DR: Merge `check_driver_info` and `check_driver_trips` into a single `check_driver_dashboard` tool that returns driver profile, registered vehicles, and upcoming trips. Add `update_driver_trip` and `delete_driver_trip` tools (prefer soft-cancel) with schemas, handlers, DB methods, indexing calls, and tests.

**Steps**
1. Update schemas (`app/ai/tool_schemas.py`):
   - Add `_CHECK_DRIVER_DASHBOARD` for `check_driver_dashboard` (no parameters).
   - Add `_UPDATE_DRIVER_TRIP` and `_DELETE_DRIVER_TRIP` for `update_driver_trip` and `delete_driver_trip`.
   - Replace `check_driver_info` and `check_driver_trips` entries in `_TOOLS_BY_MODE["driver"]` with `check_driver_dashboard`, and add the two new tool names to the driver mode list.

2. Implement merged handler (`app/tools/handlers.py`):
   - Add `check_driver_dashboard(self, arguments)` that:
     - Resolves driver by phone (existing helper), fetches registered vehicles and upcoming trips (reuse `list_driver_trips`), and returns a structured JSON payload combining profile, vehicles, and trips summaries.
   - Place the method next to existing `check_driver_info`/`check_driver_trips` logic and remove or keep old helpers as internal functions if useful.

3. Implement update/delete handlers (`app/tools/handlers.py`):
   - `update_driver_trip(self, arguments)`:
     - Validate `trip_id` and driver ownership (`get_driver_by_phone` + `get_trip_by_id`).
     - Accept only allowed fields: `departure`, `destination`, `departure_date`, `departure_time`, `vehicle_type`, `available_seats`, `total_seats`, `price`.
     - Call repository `update_driver_trip(trip_id, updates)` and reindex the trip.
     - Return the updated trip record.
   - `delete_driver_trip(self, arguments)`:
     - Validate ownership.
     - Prefer soft-cancel: call repository `cancel_driver_trip(trip_id)` that sets `status = "cancelled"`.
     - Remove/unindex from vector index and return cancelled trip.

4. Add DB methods (`app/database/supabase.py`):
   - `async def update_driver_trip(self, trip_id: str, updates: dict) -> dict` — update allowed columns and return the updated row.
   - `async def cancel_driver_trip(self, trip_id: str) -> dict` — set `status` to `cancelled` (soft delete) and return updated row.
   - Follow existing style/patterns used by `create_driver_trip` and `get_trip_by_id`.

5. Indexing updates (`app/services/trip_indexing.py`):
   - Add `async def unindex_trip(trip_id: str)` or `delete_from_index(trip_id)` to remove vectors.
   - Ensure `update_driver_trip` calls `index_trip` after DB update and `cancel_driver_trip` calls `unindex_trip`.

6. Registration / wiring:
   - No registry code changes required: `conversation_service._tool_registry()` registers tools from `_TOOLS_BY_MODE` automatically once names are updated.

7. Tests:
   - Update `tests/conftest.py` FakeRepository to implement `update_driver_trip` and `cancel_driver_trip` behaviors.
   - Replace or update tests that call `check_driver_info` / `check_driver_trips` to assert the merged `check_driver_dashboard` behavior in `tests/test_tools.py`.
   - Add tests for `update_driver_trip` and `delete_driver_trip` mirroring existing handler test patterns.

8. Docs & prompts:
   - Update `prompts/system_driver.md` to document the merged tool and the new modify/delete tools so the model calls them appropriately.

**Verification**
1. Run unit tests: `pytest -q` and ensure all tests pass (focus on `tests/test_tools.py`).
2. Run targeted tests: `pytest -q tests/test_tools.py::test_check_driver_dashboard` and new tests for update/delete.
3. Manual local sanity: call the tool handlers via the existing orchestrator test harness in `tests/test_ai_orchestrator.py`.
4. Verify indexing: after update, trip appears in index with new values; after cancel, it's unindexed.

**Decisions & assumptions**
- Use soft-cancel (`status = "cancelled"`) instead of hard delete to preserve audit/history and avoid breaking references.
- Require driver ownership verification before allowing update/delete.
- Limit updatable fields to the set listed above; price/seats allowed, but payments/refunds are out of scope.

**Further considerations**
1. Notifications: consider notifying booked passengers when a trip is modified/cancelled (out of scope for this change).
2. Concurrency: consider optimistic locking/versioning if concurrent edits are possible.
3. Permissions & audit logs: ensure handlers record who made the change (phone/driver id) for traceability.
