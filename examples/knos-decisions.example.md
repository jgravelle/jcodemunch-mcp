# Decisions and current work

<!-- Example record. `knos export` writes this file in an adopting repo; it is
     plain markdown, so a fresh clone reads it with nothing installed. -->

## Decisions

- `index_folder` is **synchronous** — dispatched via `asyncio.to_thread()` in server.py to avoid blocking the event loop. _(source: CLAUDE.md)_
- `index_repo` is **async** (uses httpx for GitHub API) _(source: CLAUDE.md)_

## Being worked on right now

_Nothing claimed._

---
<sub>One record every agent working in this repo reads. Claims lapse after 30
minutes or on `knos done`.</sub>
