"""Tests for notification_webhook — fire_webhook helper."""

from unittest.mock import MagicMock, patch

import pytest


class TestFireWebhook:
    def test_no_url_returns_false(self):
        """When MEDEXPERT_WEBHOOK_URL is empty, fire_webhook returns False."""
        with patch("lifesci_tools.notification_webhook.WEBHOOK_URL", ""):
            from lifesci_tools.notification_webhook import fire_webhook

            assert fire_webhook("test_event", {"key": "value"}) is False

    def test_success_returns_true(self):
        """When webhook URL is set and POST succeeds, returns True."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with (
            patch("lifesci_tools.notification_webhook.WEBHOOK_URL", "https://hooks.example.com/test"),
            patch("httpx.Client", return_value=mock_client),
        ):
            from lifesci_tools.notification_webhook import fire_webhook

            result = fire_webhook("prompt_candidate_created", {
                "agent_name": "DrugSpecialist",
                "version": 3,
            })
            assert result is True
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert body["event_type"] == "prompt_candidate_created"
            assert body["agent_name"] == "DrugSpecialist"

    def test_failure_returns_false(self):
        """When POST raises, returns False without propagating."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("Connection refused")

        with (
            patch("lifesci_tools.notification_webhook.WEBHOOK_URL", "https://hooks.example.com/test"),
            patch("httpx.Client", return_value=mock_client),
        ):
            from lifesci_tools.notification_webhook import fire_webhook

            result = fire_webhook("test_event", {"key": "value"})
            assert result is False

    def test_includes_event_type_in_body(self):
        """The JSON body includes event_type alongside the payload."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with (
            patch("lifesci_tools.notification_webhook.WEBHOOK_URL", "https://hooks.example.com/test"),
            patch("httpx.Client", return_value=mock_client),
        ):
            from lifesci_tools.notification_webhook import fire_webhook

            fire_webhook("calibration_pending", {
                "agent_name": "LiteratureSpecialist",
                "delta": 0.2,
            })
            call_kwargs = mock_client.post.call_args
            body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert body["event_type"] == "calibration_pending"
            assert body["agent_name"] == "LiteratureSpecialist"
            assert body["delta"] == 0.2
