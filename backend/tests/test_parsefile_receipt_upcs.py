"""Parsefile receiving analytics supplies UPC links omitted from its CSV export."""
import unittest
from unittest.mock import patch
from urllib.request import Request

from app.utils.buying_group_apis import parsefile as api
from app.utils.receipt_upc import receipt_upc
from app.utils.receiving_cache import encode, decode

UPC = '000012345678'


def analytics_row(item_id='1', name='Headphones', warehouse='Main', upc=UPC):
    return {'f_id': item_id, 'f_item': name, 'f_warehouse': warehouse,
            'f_link': f'https://www.upcitemdb.com/upc/{upc}' if upc else None}


def page(rows, count):
    return {'loadtable': [{'table': 'it@receive-analysis', 'data': {'data': rows, 'count': count}}]}


def export():
    return api.ParsefileReceivedItemsSearchResult(api.parse_received_items_csv(
        'ID,ITEM,WAREHOUSE,RECEIPT ID,QTY,TRACKING NUMBERS\n1,Headphones,Main,R1,1,TRACK\n'), '', '')


class ParsefileRedirectTests(unittest.TestCase):
    def test_canonical_www_redirect_preserves_post_data_and_headers(self):
        request = Request('https://group.test/p/endpoint', data=b'{"user":1}',
                          headers={'Authorization': 'Bearer test-token', 'Content-Type': 'application/json'})
        for code in (301, 302, 307, 308):
            with self.subTest(code=code):
                redirected = api._SafeRedirect().redirect_request(
                    request, None, code, 'Redirect', {}, 'https://www.group.test/p/endpoint')
                self.assertEqual(redirected.get_method(), 'POST')
                self.assertEqual(redirected.data, request.data)
                self.assertEqual(redirected.get_header('Authorization'), 'Bearer test-token')
                self.assertEqual(redirected.get_header('Content-type'), 'application/json')

    def test_untrusted_redirect_is_rejected_before_forwarding_credentials(self):
        request = Request('https://group.test/p/endpoint', data=b'{}', headers={'Authorization': 'Bearer test-token'})
        for target in ('https://other.test/p/endpoint', 'https://user@group.test/p/endpoint',
                       'https://group.test:9999/p/endpoint'):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, 'untrusted host'):
                api._SafeRedirect().redirect_request(request, None, 301, 'Redirect', {}, target)

    def test_login_path_redirect_keeps_standard_get_behavior(self):
        request = Request('https://group.test/p/endpoint', data=b'{}')
        redirected = api._SafeRedirect().redirect_request(
            request, None, 302, 'Redirect', {}, 'https://www.group.test/f/login')
        self.assertEqual(redirected.get_method(), 'GET')
        self.assertIsNone(redirected.data)


class ParsefileAnalyticsUpcTests(unittest.TestCase):
    def load(self, responses):
        with (patch.object(api, '_ensure_portal_session', return_value='session'),
              patch.object(api, '_portal_post', side_effect=responses) as post,
              patch.dict(api._session_cache, {}, clear=True)):
            result = api.load_receiving_analytics_upcs(
                base_url='https://group.test', email='user@example.test', password='test-password')
        return result, post

    def test_reads_complete_analytics_pages_and_extracts_links(self):
        mapping, post = self.load([
            ('session', page([analytics_row()], 2)),
            ('rotated', page([analytics_row('2', 'Pencil', upc=None)], 2)),
        ])
        self.assertEqual(mapping, {('Headphones', 'Main'): UPC})
        self.assertEqual([call.kwargs['data']['page'] for call in post.call_args_list], [1, 2])
        for call in post.call_args_list:
            self.assertEqual(call.kwargs['page'], 'it@analysis')
            self.assertEqual(call.kwargs['cmd']['_table'], 'it@receive-analysis')
            self.assertEqual(call.kwargs['cmd']['cmd'], 'load')
            self.assertEqual(call.kwargs['data']['filters'], {})

    def test_partial_repeated_or_changed_history_is_rejected(self):
        for responses in (
            [('session', page([analytics_row()], 2)), ('session', page([], 2))],
            [('session', page([analytics_row()], 2)), ('session', page([analytics_row()], 2))],
            [('session', page([analytics_row()], 2)), ('session', page([analytics_row('2')], 3))],
            [('session', {'loadtable': [{'table': 'other', 'data': {'data': [], 'count': 0}}]})],
        ):
            with self.subTest(responses=responses), self.assertRaises(RuntimeError):
                self.load(responses)

    def test_grouped_receipts_with_consistent_upcs_share_the_lookup(self):
        mapping, _ = self.load([('session', page([analytics_row('1'), analytics_row('28')], 2))])
        self.assertEqual(mapping, {('Headphones', 'Main'): UPC})
        receipt_export = export()
        repeated = api.parse_received_items_csv(
            'ID,ITEM,WAREHOUSE,RECEIPT ID,QTY,TRACKING NUMBERS\n28,Headphones,Main,R2,1,TRACK2\n')[0]
        receipt_export.rows.append(repeated)
        with (patch.object(api, 'search_received_items', return_value=receipt_export),
              patch.object(api, 'load_receiving_analytics_upcs', return_value=mapping)):
            result = api.list_all_received_items(base_url='https://group.test', bearer_token='test-token',
                user_id=1, email='user@example.test', portal_password='test-password')
        self.assertEqual([receipt_upc(row) for row in result.rows], [UPC, UPC])

    def test_same_name_with_conflicting_or_missing_upcs_is_not_assigned(self):
        for other_upc in (None, '999999999999'):
            with self.subTest(other_upc=other_upc):
                mapping, _ = self.load([('session', page([
                    analytics_row('1'), analytics_row('28', upc=other_upc),
                ], 2))])
                self.assertEqual(mapping, {})

    def test_expired_portal_session_reauthenticates_once(self):
        with patch.object(api, '_rellogin_portal_session', return_value='fresh') as login:
            mapping, post = self.load([api._PortalSessionExpired('Expired'), ('fresh', page([analytics_row()], 1))])
        self.assertEqual(mapping[('Headphones', 'Main')], UPC)
        login.assert_called_once()
        self.assertEqual(post.call_args.kwargs['session_id'], 'fresh')

    def test_csv_join_requires_exact_name_and_warehouse(self):
        for key, expected in (
            (('Headphones', 'Main'), UPC),
            (('Other product', 'Main'), None),
            (('Headphones', 'Other warehouse'), None),
        ):
            with self.subTest(key=key), patch.object(api, 'search_received_items', return_value=export()), \
                    patch.object(api, 'load_receiving_analytics_upcs', return_value={key: UPC}):
                result = api.list_all_received_items(base_url='https://group.test', bearer_token='test-token',
                    user_id=1, email='user@example.test', portal_password='test-password')
                self.assertIsNone(result.upc_warning)
                self.assertEqual(receipt_upc(result.rows[0]), expected)
                replay = decode(encode(result))
                self.assertEqual(receipt_upc(replay.rows[0]), expected)
                self.assertNotIn('test-password', str(encode(result)))

    def test_upc_lookup_failure_leaves_receipt_data_available_with_warning(self):
        with (patch.object(api, 'search_received_items', return_value=export()),
              patch.object(api, 'load_receiving_analytics_upcs', side_effect=RuntimeError('Login failed'))):
            result = api.list_all_received_items(base_url='https://group.test', bearer_token='test-token',
                user_id=1, email='user@example.test', portal_password='test-password')
        self.assertEqual(result.rows[0].receipt_id, 'R1')
        self.assertIsNone(receipt_upc(result.rows[0]))
        self.assertIn('portal login', result.upc_warning)

    def test_missing_portal_password_is_actionable_without_network_lookup(self):
        with (patch.object(api, 'search_received_items', return_value=export()),
              patch.object(api, 'load_receiving_analytics_upcs') as load):
            result = api.list_all_received_items(base_url='https://group.test', bearer_token='test-token',
                user_id=1, email='user@example.test')
        load.assert_not_called()
        self.assertIn('portal password', result.upc_warning)

    def test_existing_csv_upc_skips_optional_analytics_lookup(self):
        receipt_export = export()
        receipt_export.rows[0].raw['UPC'] = UPC
        with (patch.object(api, 'search_received_items', return_value=receipt_export),
              patch.object(api, 'load_receiving_analytics_upcs') as load):
            result = api.list_all_received_items(base_url='https://group.test', bearer_token='test-token',
                user_id=1, email='user@example.test', portal_password='test-password')
        load.assert_not_called()
        self.assertEqual(receipt_upc(result.rows[0]), UPC)

    def test_old_cache_without_warning_field_remains_compatible(self):
        saved = encode(export())
        saved['fields'].pop('upc_warning')
        self.assertIsNone(decode(saved).upc_warning)
