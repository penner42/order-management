"""Tracking authentication must fail closed before carrier API calls run."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import require_user


class TrackingAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials(self):
        with self.assertRaises(HTTPException) as caught:
            await require_user(None)
        self.assertEqual(caught.exception.status_code, 401)

    async def test_validates_token_with_backend(self):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="synthetic")
        for status, expected in [(200, None), (401, 401), (403, 401), (500, 503), (302, 503)]:
            with self.subTest(status=status), patch('app.auth.httpx.AsyncClient') as client:
                session = AsyncMock()
                client.return_value.__aenter__.return_value = session
                session.get.return_value = httpx.Response(status, json={"id": 1, "username": "test"})
                if expected:
                    with self.assertRaises(HTTPException) as caught:
                        await require_user(credentials)
                    self.assertEqual(caught.exception.status_code, expected)
                else:
                    self.assertEqual((await require_user(credentials))["id"], 1)
                self.assertEqual(session.get.call_args.kwargs['headers'], {"Authorization": "Bearer synthetic"})

    async def test_backend_unavailable(self):
        with patch('app.auth.httpx.AsyncClient') as client:
            session = AsyncMock()
            client.return_value.__aenter__.return_value = session
            session.get.side_effect = httpx.ConnectError("unavailable")
            with self.assertRaises(HTTPException) as caught:
                await require_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="synthetic"))
            self.assertEqual(caught.exception.status_code, 503)
