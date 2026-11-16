"""Claude Code hook handlers for jCodemunch enforcement.

One module per hook family; this facade re-exports only the ``run_*`` entry
points ``server.py`` dispatches. Tests import helpers from their defining
modules, the same way they monkeypatch them.
"""

from .briefing import run_subagentstart  # noqa: F401
from .cursor import run_cursor_hook  # noqa: F401
from .reindex import run_copilot_posttooluse, run_posttooluse  # noqa: F401
from .snapshot import run_precompact, run_sessionstart  # noqa: F401
from .steering import run_pretooluse  # noqa: F401
from .taskcomplete import run_taskcomplete  # noqa: F401
