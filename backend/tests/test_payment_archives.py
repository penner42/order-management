"""Sanitized Parsefile archive navigation and stable annual identities."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from app.payment_providers import ParsefilePaymentProvider
from app.payment_evidence import PaymentEvidence, ReceiptEvidence, PaymentFetch


def history(year, value='100'):
    d = datetime(year, 1, 2, tzinfo=timezone.utc)
    return PaymentFetch([PaymentEvidence(external_id='1', amount=value, created_at=d,
        status='PAID', receipt_ids=['1'])], [ReceiptEvidence(external_id='1',amount=value,created_at=d,quantity=1)])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.provider = ParsefilePaymentProvider()
        self.group = SimpleNamespace(base_url='https://www.example.test',api_email='test@example.test',api_password='test')

    def test_advertised_same_site_archive_links_only(self):
        payload={'scope':{'menus':{'data':[{'title':'Archive 2025','href':'https://www.2025.example.test'}]}}}
        with patch('app.payment_providers.parsefile.portal_login',return_value='session'), patch(
            'app.payment_providers.parsefile._portal_post',return_value=('session',payload)):
            self.assertEqual(self.provider.archives(self.group,self.group.base_url),[(2025,'https://www.2025.example.test')])
            payload['scope']['menus']['data'][0]['href']='https://2025.attacker.test'
            with self.assertRaises(ValueError):self.provider.archives(self.group,self.group.base_url)

    def fetch(self, current, archived, archives=None):
        archives=archives if archives is not None else [(2025,'https://www.2025.example.test')]
        with patch('app.payment_providers.canonical_base',side_effect=lambda url:url), patch.object(
            self.provider,'archives',return_value=archives), patch.object(
            self.provider,'_fetch_partition',side_effect=[current,archived]):
            return self.provider.fetch(self.group)

    def test_reused_ids_get_separate_annual_identities_and_receipt_links(self):
        result=self.fetch(history(2026),history(2025))
        self.assertTrue(result.history_complete)
        self.assertEqual({p.identity_key for p in result.payments},{'2026:1','2025:1'})
        self.assertEqual({tuple(p.receipt_ids) for p in result.payments},{('2026:1',),('2025:1',)})

    def test_archival_move_preserves_identity_and_deduplicates_overlap(self):
        before=self.fetch(history(2025),history(2025))
        after=self.fetch(PaymentFetch([],[]),history(2025))
        self.assertEqual(len(before.payments),1)
        self.assertEqual(before.payments[0].identity_key,after.payments[0].identity_key)
        self.assertEqual(before.receipts[0].identity_key,after.receipts[0].identity_key)

    def test_unavailable_archive_returns_incomplete_history_with_current_records(self):
        result=self.fetch(history(2026),RuntimeError('private remote failure'))
        self.assertFalse(result.history_complete)
        self.assertEqual(len(result.payments),1)
        self.assertIn('2025',result.warnings[0])
        self.assertNotIn('private',str(result.warnings))

    def test_pre_2025_archives_are_never_contacted_or_reported_missing(self):
        with patch('app.payment_providers.canonical_base', side_effect=lambda url: url) as canonical, patch.object(
            self.provider, 'archives', return_value=[(2025, 'https://www.2025.example.test'),
                (2024, 'https://www.2024.example.test')]), patch.object(
            self.provider, '_fetch_partition', side_effect=[history(2026), history(2025)]) as fetch:
            result = self.provider.fetch(self.group)
        self.assertTrue(result.history_complete)
        self.assertEqual(result.warnings, [])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([call.args[0] for call in canonical.call_args_list],
            [self.group.base_url, 'https://www.2025.example.test'])

    def test_archive_login_redirect_to_another_site_is_rejected(self):
        with patch('app.payment_providers.canonical_base',side_effect=['https://www.example.test','https://other.test']), patch.object(
            self.provider,'archives',return_value=[(2025,'https://www.2025.example.test')]), patch.object(
            self.provider,'_fetch_partition',return_value=history(2026)) as read:
            result=self.provider.fetch(self.group)
            self.assertFalse(result.history_complete)
            self.assertEqual(read.call_count,1)
