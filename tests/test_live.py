"""The real path: a seed round against a CRM test account, a sync, and a check that every change landed in its bucket.

It writes to the test account, so it runs only when asked: set PIPELINE_DIFF_LIVE_TEST=hubspot or salesforce, with that
CRM's settings in the environment or .env, and a data folder that already holds a seed baseline.
"""

import os

import pytest

from pipeline_diff import config, seed
from pipeline_diff.cli import client_for, sync_client

LIVE = os.environ.get("PIPELINE_DIFF_LIVE_TEST", "")


@pytest.mark.live
@pytest.mark.skipif(LIVE not in ("hubspot", "salesforce"), reason="set PIPELINE_DIFF_LIVE_TEST=hubspot or salesforce to run")
def test_a_live_seed_round_lands_every_change_in_its_bucket():
    config.load_env()
    folder = config.data_dir() / LIVE
    client = client_for(LIVE)
    if not (folder / "seed.json").exists():
        seed.baseline(client, folder, count=100)
    sync_client(client, folder)
    seed.change_round(client, folder)
    sync_client(client, folder)
    assert seed.verify(folder) == []
