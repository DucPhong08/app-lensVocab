from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request

from app.models.setting import SystemSetting
from app.services import ai_service, degradation_service
from app.services.guest_quota_service import client_ip


class MVPRecognitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_context_does_not_invoke_bedrock(self):
        with (
            patch.object(
                degradation_service,
                "fetch_system_settings",
                AsyncMock(return_value=SystemSetting.model_construct()),
            ),
            patch.object(
                degradation_service, "fallback_keyword_from_context", AsyncMock(return_value=None)
            ) as fallback,
        ):
            result = await degradation_service.evaluate_vision_result(None, 0, "")
        fallback.assert_not_awaited()
        self.assertEqual(result.status, degradation_service.DegradationResult.UNRECOGNIZABLE)

    async def test_one_display_object_does_not_reduce_candidate_budget(self):
        client = MagicMock()
        client.detect_labels.return_value = {"Labels": [{"Name": "Chair", "Confidence": 90.0}]}
        with patch.object(ai_service, "_get_rekognition_client", return_value=client):
            result = ai_service._sync_detect_labels(b"offline", max_labels=1)
        self.assertGreaterEqual(client.detect_labels.call_args.kwargs["MaxLabels"], 20)
        self.assertEqual(len(result.labels), 1)

    async def test_concrete_noun_precedes_higher_confidence_context(self):
        client = MagicMock()
        client.detect_labels.return_value = {
            "Labels": [
                {"Name": "Indoors", "Confidence": 99.0},
                {"Name": "Chair", "Confidence": 90.0},
            ]
        }
        with patch.object(ai_service, "_get_rekognition_client", return_value=client):
            result = ai_service._sync_detect_labels(b"offline", max_labels=1)
        self.assertEqual(result.top_label, "Chair")
        self.assertIn("Indoors", result.raw_description)

    async def test_context_only_labels_do_not_become_a_confident_object(self):
        client = MagicMock()
        client.detect_labels.return_value = {"Labels": [{"Name": "Indoors", "Confidence": 99.0}]}
        with patch.object(ai_service, "_get_rekognition_client", return_value=client):
            result = ai_service._sync_detect_labels(b"offline")
        self.assertIsNone(result.top_label)
        self.assertEqual(result.raw_description, "Indoors")


class MVPProxyTests(unittest.TestCase):
    def test_render_flag_does_not_trust_headers_from_an_unknown_peer(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "client": ("192.0.2.10", 123),
                "headers": [
                    (b"cf-connecting-ip", b"198.51.100.2"),
                    (b"x-forwarded-for", b"203.0.113.4"),
                ],
            }
        )
        with patch.dict(os.environ, {"RENDER": "true"}):
            self.assertEqual(client_ip(request), "192.0.2.10")
