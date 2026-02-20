# Copyright 2026 Meshping Contributors
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
# NOTE: This file was created with the assistance of an AI (Large Language Model).
# Review required for correctness, security, and licensing.

"""
Unit tests for agent passphrase / shared-secret validation.

Tests the ``is_valid_agent_passphrase`` helper which encapsulates the
AGENT_SECRET check used during WebSocket handshake authentication.
"""

# pylint: disable=import-error
import server


def test_any_passphrase_accepted_when_agent_secret_not_configured(monkeypatch):
    """When AGENT_SECRET is empty any passphrase must be accepted (backward compat)."""
    monkeypatch.setattr(server, "AGENT_SECRET", "")
    assert server.is_valid_agent_passphrase("any-passphrase") is True
    assert server.is_valid_agent_passphrase("") is True


def test_correct_passphrase_accepted_when_agent_secret_configured(monkeypatch):
    """When AGENT_SECRET is set the matching passphrase must be accepted."""
    monkeypatch.setattr(server, "AGENT_SECRET", "my-secret")
    assert server.is_valid_agent_passphrase("my-secret") is True


def test_wrong_passphrase_rejected_when_agent_secret_configured(monkeypatch):
    """When AGENT_SECRET is set a wrong passphrase must be rejected."""
    monkeypatch.setattr(server, "AGENT_SECRET", "my-secret")
    assert server.is_valid_agent_passphrase("wrong-secret") is False


def test_empty_passphrase_rejected_when_agent_secret_configured(monkeypatch):
    """When AGENT_SECRET is set an empty passphrase must be rejected."""
    monkeypatch.setattr(server, "AGENT_SECRET", "my-secret")
    assert server.is_valid_agent_passphrase("") is False
