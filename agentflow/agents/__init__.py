from .aggregator import run as run_aggregator
from .content import run as run_legacy_content
from .exec_code import run as run_code
from .exec_content import run as run_content
from .exec_jobs import run as run_jobs
from .exec_research import run as run_research
from .research import run as run_legacy_research
from .reviewer import run as run_reviewer
from .splitter import run as run_splitter

EXECUTORS = {
    "exec_code": run_code,
    "exec_content": run_content,
    "exec_research": run_research,
    "exec_jobs": run_jobs,
}

__all__ = [
    "EXECUTORS",
    "run_aggregator",
    "run_code",
    "run_content",
    "run_jobs",
    "run_legacy_content",
    "run_legacy_research",
    "run_research",
    "run_reviewer",
    "run_splitter",
]
