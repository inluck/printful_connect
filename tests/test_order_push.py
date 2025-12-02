# -*- coding: utf-8 -*-
"""
Tests for order push functionality.
"""
from unittest.mock import patch
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from .common import PrintfulTestCase


@tagged('post_install', '-at_install', 'printful')
class TestOrderPush(PrintfulTestCase):
    """Test cases for pushing orders to Printful."""

    def test_push_idempotency_check(self):
        """Test that orders cannot be pushed twice."""
        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        # Simulate already pushed order
        order.order_external_ref = 'ODOO-existing-uuid'

        with self.assertRaises(UserError) as context:
            order._push_to_printful()
        self.assertIn('already been pushed', str(context.exception))
        self.assertIn('ODOO-existing-uuid', str(context.exception))

    def test_build_printful_order_data_structure(self):
        """Test order data is built with correct structure."""
        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        order_data = order._build_printful_order_data()

        # Check structure
        self.assertIn('external_id', order_data)
        self.assertIn('recipient', order_data)
        self.assertIn('items', order_data)
        self.assertIn('retail_costs', order_data)

        # Check external_id is UUID-based
        self.assertTrue(order_data['external_id'].startswith('ODOO'))

        # Check recipient data
        recipient = order_data['recipient']
        self.assertEqual(recipient['name'], 'Test Customer')
        self.assertEqual(recipient['city'], 'New York')

    def test_build_printful_order_data_items(self):
        """Test order items are built correctly."""
        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        order_data = order._build_printful_order_data()

        # Should have items (excluding delivery lines)
        self.assertGreater(len(order_data['items']), 0)

        item = order_data['items'][0]
        self.assertIn('variant_id', item)
        self.assertIn('external_variant_id', item)
        self.assertIn('quantity', item)

    def test_build_printful_order_data_unique_external_id(self):
        """Test each order gets a unique external_id."""
        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)

        order1 = self._create_test_sale_order(partner, variant)
        order2 = self._create_test_sale_order(partner, variant)

        data1 = order1._build_printful_order_data()
        data2 = order2._build_printful_order_data()

        self.assertNotEqual(data1['external_id'], data2['external_id'])

    @patch('odoo.addons.printful_connect.models.sale.requests.post')
    def test_push_to_printful_success(self, mock_post):
        """Test successful order push to Printful."""
        # Setup mock responses for create and confirm
        mock_post.side_effect = [
            self._create_mock_response(self.MOCK_ORDER_CREATE),
            self._create_mock_response(self.MOCK_ORDER_CONFIRM),
        ]

        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        order._push_to_printful()

        # Verify order was updated with Printful data
        self.assertEqual(order.order_ref, '98765')
        self.assertEqual(order.order_shipping, 'STANDARD')
        self.assertGreater(order.order_total, 0)

    @patch('odoo.addons.printful_connect.models.sale.requests.post')
    def test_push_to_printful_no_config(self, mock_post):
        """Test push fails without Printful config."""
        # Remove all configs
        self.env['printful.printful'].search([]).unlink()

        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        with self.assertRaises(UserError) as context:
            order._push_to_printful()
        self.assertIn('API token', str(context.exception))

    @patch('odoo.addons.printful_connect.models.sale.requests.post')
    def test_push_to_printful_create_failure(self, mock_post):
        """Test push handles order creation failure."""
        mock_post.return_value = self._create_mock_response({
            'code': 400,
            'result': 'Invalid order data',
            'error': {'message': 'Missing required fields'}
        })

        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        with self.assertRaises(UserError) as context:
            order._push_to_printful()
        self.assertIn('Failed to create', str(context.exception))

    @patch('odoo.addons.printful_connect.models.sale.requests.post')
    def test_push_to_printful_confirm_failure(self, mock_post):
        """Test push handles order confirmation failure."""
        mock_post.side_effect = [
            self._create_mock_response(self.MOCK_ORDER_CREATE),
            self._create_mock_response({
                'code': 400,
                'result': 'Cannot confirm order',
            }),
        ]

        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        with self.assertRaises(UserError) as context:
            order._push_to_printful()
        self.assertIn('confirmation failed', str(context.exception))

    @patch('odoo.addons.printful_connect.models.sale.requests.post')
    def test_push_to_printful_timeout(self, mock_post):
        """Test push handles timeout gracefully."""
        import requests
        mock_post.side_effect = requests.exceptions.Timeout('Connection timed out')

        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        with self.assertRaises(UserError) as context:
            order._push_to_printful()
        self.assertIn('timed out', str(context.exception))

    @patch('odoo.addons.printful_connect.models.sale.requests.post')
    def test_push_to_printful_network_error(self, mock_post):
        """Test push handles network errors gracefully."""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError('Network unreachable')

        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        with self.assertRaises(UserError) as context:
            order._push_to_printful()
        self.assertIn('Network error', str(context.exception))


@tagged('post_install', '-at_install', 'printful')
class TestUpdateFromPrintfulResponse(PrintfulTestCase):
    """Test cases for updating order from Printful API response."""

    def test_update_from_printful_response(self):
        """Test order fields are updated from API response."""
        partner = self._create_test_partner()
        template = self._create_test_product()
        variant = self._create_test_variant(template)
        order = self._create_test_sale_order(partner, variant)

        order._update_from_printful_response(self.MOCK_ORDER_CREATE)

        self.assertEqual(order.order_ref, '98765')
        self.assertEqual(order.order_external_ref, 'ODOO-test-uuid')
        self.assertEqual(order.order_currency, 'USD')
        self.assertEqual(order.order_subtotal, 25.00)
        self.assertEqual(order.shipping, 4.99)
        self.assertEqual(order.order_total, 29.99)
        self.assertEqual(order.customer_pays, 29.99)
        self.assertEqual(order.printful_price, 17.94)
        self.assertEqual(order.profit, 12.05)

    def test_update_from_printful_response_empty_costs(self):
        """Test update handles missing cost data."""
        partner = self._create_test_partner()
        order = self.env['sale.order'].create({'partner_id': partner.id})

        order._update_from_printful_response({
            'result': {
                'id': 12345,
            }
        })

        # Should not raise, defaults to 0
        self.assertEqual(order.order_ref, '12345')
        self.assertEqual(order.order_total, 0)


@tagged('post_install', '-at_install', 'printful')
class TestOrderFetch(PrintfulTestCase):
    """Test cases for fetching orders from Printful."""

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_process_printful_order_creates_customer(self, mock_get):
        """Test order processing creates customer from recipient."""
        mock_get.return_value = self._create_mock_response({'code': 200})

        order_data = {
            'id': 55555,
            'external_id': 'ext_55555',
            'recipient': {
                'name': 'John Doe',
                'address1': '456 Main St',
                'city': 'Los Angeles',
                'country_code': 'US',
                'zip': '90001',
                'email': 'john@example.com',
                'phone': '+1-555-000-0000',
            },
            'items': [],
            'costs': {
                'currency': 'USD',
                'total': '50.00',
            },
            'pricing_breakdown': [{}],
        }

        self.printful_config._process_printful_order(order_data)

        # Verify customer was created
        customer = self.env['res.partner'].search([
            ('email', '=', 'john@example.com')
        ], limit=1)
        self.assertTrue(customer)
        self.assertEqual(customer.name, 'John Doe')
        self.assertEqual(customer.city, 'Los Angeles')

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_process_printful_order_skips_existing(self, mock_get):
        """Test order processing skips already imported orders."""
        # Create existing order with same ref
        partner = self._create_test_partner()
        existing_order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_ref': '#PF55555',
        })

        order_data = {
            'id': 55555,
            'recipient': {'name': 'Test'},
            'items': [],
            'costs': {},
            'pricing_breakdown': [{}],
        }

        result = self.printful_config._process_printful_order(order_data)

        # Should return existing order, not create new one
        self.assertEqual(result.id, existing_order.id)

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_process_printful_order_links_products(self, mock_get):
        """Test order processing links to existing product variants."""
        mock_get.return_value = self._create_mock_response({'code': 200})

        # Create product with matching variant ref
        template = self._create_test_product()
        variant = self._create_test_variant(template, 'matched_variant_ref')

        order_data = {
            'id': 66666,
            'recipient': {
                'name': 'Test Customer',
            },
            'items': [{
                'external_variant_id': 'matched_variant_ref',
                'price': '25.00',
                'quantity': 2,
            }],
            'costs': {'total': '50.00'},
            'pricing_breakdown': [{}],
        }

        result = self.printful_config._process_printful_order(order_data)

        # Verify order line was created with linked product
        self.assertEqual(len(result.order_line), 1)
        self.assertEqual(result.order_line[0].product_id.id, variant.id)
        self.assertEqual(result.order_line[0].product_uom_qty, 2)


@tagged('post_install', '-at_install', 'printful')
class TestSaleOrderLine(PrintfulTestCase):
    """Test cases for SaleOrderLine extensions."""

    def test_order_line_domain_filters_in_stock(self):
        """Test that order line product domain filters by in_stock."""
        # Check the domain on the field
        line_field = self.env['sale.order.line']._fields['product_id']
        domain = line_field.domain

        self.assertIn("'printful_product_in_stock', '=', True", str(domain))


@tagged('post_install', '-at_install', 'printful')
class TestResPartner(PrintfulTestCase):
    """Test cases for ResPartner extensions."""

    def test_partner_has_tax_number_field(self):
        """Test partner model has tax_number field."""
        partner = self._create_test_partner()
        partner.tax_number = 'US123456789'

        self.assertEqual(partner.tax_number, 'US123456789')

    def test_partner_has_company_field(self):
        """Test partner model has company field."""
        partner = self._create_test_partner()
        partner.company = 'Test Company Inc.'

        self.assertEqual(partner.company, 'Test Company Inc.')
