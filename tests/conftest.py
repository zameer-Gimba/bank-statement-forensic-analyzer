"""
Shared fixtures: one small generated case, built once per test session.
"""

import json

import pytest

from src.case import analyse
from src.generator import generate_case
from src.ingest import ingest_paths


@pytest.fixture(scope="session")
def case_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("case")
    generate_case(folder, seed=7)
    return folder


@pytest.fixture(scope="session")
def truth(case_dir):
    return json.loads((case_dir / "ground_truth.json").read_text())


@pytest.fixture(scope="session")
def statements(case_dir):
    return ingest_paths(sorted(case_dir.glob("statement_*")))


@pytest.fixture(scope="session")
def analysis(statements):
    return analyse(statements)
