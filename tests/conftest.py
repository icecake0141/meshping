# Copyright 2026 Meshping Contributors
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# NOTE: This file may include code that was generated or suggested by a large language model (LLM).
# This file was created or modified with the assistance of an AI (Large Language Model).
# Review required for correctness, security, and licensing.

"""Shared pytest fixtures for the Meshping test suite."""

# pylint: disable=import-error
import pytest

import server


@pytest.fixture()
def client(tmp_path):
    """Flask test client with isolated in-memory database."""
    server.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'alert_test.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    with server.app.app_context():
        server.db.create_all()
        server.current_targets = []
        server.recent_cache.clear()
        yield server.app.test_client()
        server.db.session.remove()
        server.db.drop_all()
