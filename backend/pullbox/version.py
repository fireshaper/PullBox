"""PullBox's version — ``[project].version`` in ``backend/pyproject.toml``.

That field is the single place the version is set. Everything else reads it from
here: the FastAPI app, the webhook User-Agent, and ``/api/health``, which is
where the frontend's sidebar gets it.

It is read from the file rather than ``importlib.metadata`` because the project
is never installed as a distribution (uv treats it as a virtual project, and the
Docker image syncs with ``--no-install-project``), so there is no package
metadata to query. ``pyproject.toml`` sits next to the package in every layout
PullBox runs from: the dev checkout, the deploy zip and the Docker image.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _read_version() -> str:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


__version__ = _read_version()
