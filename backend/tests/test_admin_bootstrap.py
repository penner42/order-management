"""Desktop bootstrap credentials must not overwrite a changed password."""
import unittest
from unittest.mock import MagicMock, patch

from app import admin_bootstrap as bootstrap


class AdminBootstrapTests(unittest.TestCase):
    def test_creates_default_admin_when_password_sync_disabled(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch.object(bootstrap.settings, "admin_password_sync_enabled", False), \
             patch.object(bootstrap.settings, "admin_username", "admin"), \
             patch.object(bootstrap.settings, "admin_password", "admin"), \
             patch.object(bootstrap, "hash_password", return_value="hash") as hash_password:
            bootstrap.ensure_admin_user(db)
        user = db.add.call_args.args[0]
        self.assertEqual((user.username, user.role, user.hashed_password), ("admin", "admin", "hash"))
        hash_password.assert_called_once_with("admin")
        db.commit.assert_called_once()

    def test_existing_account_is_untouched_when_sync_disabled(self):
        db = MagicMock()
        user = db.query.return_value.filter.return_value.first.return_value
        user.hashed_password = "user-changed-password"
        with patch.object(bootstrap.settings, "admin_password_sync_enabled", False), \
             patch.object(bootstrap, "hash_password") as hash_password:
            bootstrap.ensure_admin_user(db)
        self.assertEqual(user.hashed_password, "user-changed-password")
        hash_password.assert_not_called()
        db.commit.assert_not_called()

    def test_existing_deployments_keep_password_sync(self):
        db = MagicMock()
        user = db.query.return_value.filter.return_value.first.return_value
        with patch.object(bootstrap.settings, "admin_password_sync_enabled", True), \
             patch.object(bootstrap, "hash_password", return_value="env-password"):
            bootstrap.ensure_admin_user(db)
        self.assertEqual((user.hashed_password, user.role), ("env-password", "admin"))
        db.commit.assert_called_once()
