"""
MinionQA Package.

Autonomous web QA and revenue-flow validation agent powered by OpenMinion 0.1
for Cortensor Hackathon #5.
"""

from typing import TYPE_CHECKING, Any
from minion_qa.config import MinionQAConfig, default_config
from minion_qa.tools.browser import (
    navigate_and_snapshot,
    execute_action,
    get_browser_session,
    reset_browser_session,
)
from minion_qa.tools.assertions import (
    verify_assertion,
    evaluate_assertion,
    get_assertion_history,
    record_assertion,
)
from minion_qa.tools.reporter import (
    generate_qa_audit_report,
    compile_markdown_report,
    print_rich_summary,
)

if TYPE_CHECKING:
    from minion_qa.agent import MinionQAAgent

__version__ = "0.1.0"
__all__ = [
    "MinionQAAgent",
    "MinionQAConfig",
    "default_config",
    "navigate_and_snapshot",
    "execute_action",
    "verify_assertion",
    "generate_qa_audit_report",
    "compile_markdown_report",
    "print_rich_summary",
    "get_browser_session",
    "reset_browser_session",
    "evaluate_assertion",
    "get_assertion_history",
    "record_assertion",
]


def __getattr__(name: str) -> Any:
    if name == "MinionQAAgent":
        from minion_qa.agent import MinionQAAgent
        return MinionQAAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
