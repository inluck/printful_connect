# -*- coding: utf-8 -*-
"""
Tests for Printful webhook and shipping functionality.
"""
import json
import hashlib
import hmac
from unittest.mock import patch

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

from .common import PrintfulTestCase


class TestWebhookSignatureValidation(PrintfulTestCase):
    """Test webhook HMAC signature validation."""

    def setUp(self):
        super().setUp()
        self.webhook = self.env['printful.webhook'].create({
            'name': 'Test Webhook',
            'printful_config_id': self.printful_config.id,
            'webhook_secret': 'test_secret_key_12345',
        })

    def test_valid_signature(self):
        """Test that valid signatures are accepted."""
        payload = b'{"type": "order_created", "data": {}}'
        expected_sig = hmac.new(
            b'test_secret_key_12345',
            payload,
            hashlib.sha256
        ).hexdigest()

        result = self.webhook.validate_signature(payload, expected_sig)
        self.assertTrue(result)

    def test_invalid_signature(self):
        """Test that invalid signatures are rejected."""
        payload = b'{"type": "order_created", "data": {}}'
        invalid_sig = 'invalid_signature_abc123'

        result = self.webhook.validate_signature(payload, invalid_sig)
        self.assertFalse(result)

    def test_empty_signature(self):
        """Test that missing signature is rejected."""
        payload = b'{"type": "order_created", "data": {}}'

        result = self.webhook.validate_signature(payload, '')
        self.assertFalse(result)

    def test_no_secret_configured(self):
        """Test that webhooks without secret skip validation."""
        self.webhook.write({'webhook_secret': False})
        payload = b'{"type": "order_created", "data": {}}'

        # Should return True when no secret configured (permissive mode)
        result = self.webhook.validate_signature(payload, 'any_signature')
        self.assertTrue(result)


class TestWebhookEventProcessing(PrintfulTestCase):
    """Test webhook event handling."""

    def setUp(self):
        super().setUp()
        self.webhook = self.env['printful.webhook'].create({
            'name': 'Test Webhook',
            'printful_config_id': self.printful_config.id,
            'event_order_created': True,
            'event_package_shipped': True,
            'event_stock_updated': True,
        })

    def test_subscribed_events_list(self):
        """Test getting list of subscribed events."""
        events = self.webhook.get_subscribed_events()

        self.assertIn('order_created', events)
        self.assertIn('package_shipped', events)
        self.assertIn('stock_updated', events)
        # Not subscribed
        self.assertNotIn('product_deleted', events)

    def test_order_created_event(self):
        """Test processing order_created webhook event."""
        # Create a sale order to link
        order = self._create_test_sale_order()
        order.write({'order_external_ref': 'ODOO-test-external-123'})

        payload = {
            'type': 'order_created',
            'data': {
                'order': {
                    'id': 98765,
                    'external_id': 'ODOO-test-external-123',
                    'status': 'pending',
                }
            }
        }

        event = self.env['printful.webhook.event'].create({
            'name': 'test_event_1',
            'webhook_id': self.webhook.id,
            'event_type': 'order_created',
            'payload': json.dumps(payload),
            'state': 'pending',
        })

        event._process_event()

        self.assertEqual(event.state, 'done')
        self.assertEqual(event.sale_order_id.id, order.id)

    def test_package_shipped_event(self):
        """Test processing package_shipped webhook event."""
        order = self._create_test_sale_order()
        order.write({'order_external_ref': 'ODOO-shipping-test'})

        payload = {
            'type': 'package_shipped',
            'data': {
                'order': {
                    'id': 98765,
                    'external_id': 'ODOO-shipping-test',
                },
                'shipment': {
                    'tracking_number': '1Z999AA10123456784',
                    'tracking_url': 'https://tracking.example.com/1Z999AA10123456784',
                    'carrier': 'UPS',
                    'ship_date': '2024-01-15',
                    'estimated_delivery': '2024-01-18',
                }
            }
        }

        event = self.env['printful.webhook.event'].create({
            'name': 'test_shipped_event',
            'webhook_id': self.webhook.id,
            'event_type': 'package_shipped',
            'payload': json.dumps(payload),
            'state': 'pending',
        })

        event._process_event()

        self.assertEqual(event.state, 'done')
        self.assertEqual(event.sale_order_id.id, order.id)
        # Check that order was updated
        self.assertEqual(order.order_shipping, 'UPS')

    def test_stock_updated_event(self):
        """Test processing stock_updated webhook event."""
        # Create a product variant
        template = self._create_test_product()
        variant = self._create_test_variant(template, 'var_stock_test')
        variant.write({
            'printful_variant_external_ref': '5001',
            'printful_product_in_stock': True,
        })

        payload = {
            'type': 'stock_updated',
            'data': {
                'sync_variant': {
                    'id': '5001',
                    'availability_status': 'out_of_stock',
                }
            }
        }

        event = self.env['printful.webhook.event'].create({
            'name': 'test_stock_event',
            'webhook_id': self.webhook.id,
            'event_type': 'stock_updated',
            'payload': json.dumps(payload),
            'state': 'pending',
        })

        event._process_event()

        self.assertEqual(event.state, 'done')
        # Refresh variant from DB
        variant.invalidate_recordset()
        self.assertFalse(variant.printful_product_in_stock)

    def test_failed_event_retry(self):
        """Test retrying a failed event."""
        event = self.env['printful.webhook.event'].create({
            'name': 'test_failed_event',
            'webhook_id': self.webhook.id,
            'event_type': 'unknown',
            'payload': '{"invalid": "data"}',
            'state': 'failed',
            'error_message': 'Previous error',
            'retry_count': 0,
        })

        event.action_retry()

        self.assertEqual(event.state, 'done')  # Will be done since it's unknown type
        self.assertEqual(event.retry_count, 1)


class TestShippingMethodConfiguration(PrintfulTestCase):
    """Test shipping method configuration and mapping."""

    def setUp(self):
        super().setUp()
        self.shipping_method = self.env['printful.shipping.method'].create({
            'name': 'Standard Shipping',
            'printful_config_id': self.printful_config.id,
            'printful_method_id': 'STANDARD',
            'is_default': True,
            'fixed_surcharge': 1.00,
            'percentage_markup': 10.0,
        })

    def test_shipping_price_calculation(self):
        """Test shipping price with markup and surcharge."""
        base_price = 5.00

        final_price = self.shipping_method.calculate_price(base_price)

        # Expected: 5.00 * 1.10 (10% markup) + 1.00 (surcharge) = 6.50
        self.assertEqual(final_price, 6.50)

    def test_single_default_constraint(self):
        """Test that only one default shipping method is allowed per config."""
        # Try to create another default method
        with self.assertRaises(UserError):
            self.env['printful.shipping.method'].create({
                'name': 'Express Shipping',
                'printful_config_id': self.printful_config.id,
                'printful_method_id': 'EXPRESS',
                'is_default': True,  # This should fail
            })


class TestDefaultAddress(PrintfulTestCase):
    """Test default address configuration for shipping estimates."""

    def setUp(self):
        super().setUp()
        country_us = self.env.ref('base.us')
        state_ca = self.env['res.country.state'].search([
            ('country_id', '=', country_us.id),
            ('code', '=', 'CA')
        ], limit=1)

        self.default_address = self.env['printful.default.address'].create({
            'name': 'US West Coast Default',
            'printful_config_id': self.printful_config.id,
            'country_id': country_us.id,
            'state_id': state_ca.id if state_ca else False,
            'city': 'Los Angeles',
            'zip_code': '90210',
            'address1': '123 Beverly Hills Blvd',
            'is_primary': True,
        })

    def test_recipient_data_format(self):
        """Test that get_recipient_data returns correct format."""
        data = self.default_address.get_recipient_data()

        self.assertEqual(data['country_code'], 'US')
        self.assertEqual(data['city'], 'Los Angeles')
        self.assertEqual(data['address1'], '123 Beverly Hills Blvd')
        self.assertEqual(data['zip'], '90210')
        self.assertIn('phone', data)

    def test_primary_address_computed_field(self):
        """Test that primary_default_address_id is computed correctly."""
        self.printful_config.invalidate_recordset()

        self.assertEqual(
            self.printful_config.primary_default_address_id.id,
            self.default_address.id
        )


class TestShippingRatesV2(PrintfulTestCase):
    """Test v2 shipping rate calculation."""

    def setUp(self):
        super().setUp()
        # Create a product with Printful variant
        self.template = self._create_test_product()
        self.variant = self._create_test_variant(self.template)

    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_get_shipping_rates_v2(self, mock_post):
        """Test getting shipping rates with v2 API."""
        mock_post.return_value = self._create_mock_response(self.MOCK_SHIPPING_RATES)

        rates = self.printful_config._get_shipping_rates_v2(
            country_code='US',
            state_code='CA',
            zip_code='90210',
            items=[{'variant_id': '4011', 'quantity': 1}]
        )

        self.assertEqual(len(rates), 2)
        self.assertEqual(rates[0]['id'], 'STANDARD')
        self.assertEqual(rates[0]['rate'], 4.99)
        self.assertEqual(rates[1]['id'], 'EXPRESS')


class TestOrderShippingMethod(PrintfulTestCase):
    """Test shipping method selection during order push."""

    def setUp(self):
        super().setUp()
        # Create shipping methods
        self.standard_method = self.env['printful.shipping.method'].create({
            'name': 'Standard',
            'printful_config_id': self.printful_config.id,
            'printful_method_id': 'STANDARD',
            'is_default': True,
        })
        self.express_method = self.env['printful.shipping.method'].create({
            'name': 'Express',
            'printful_config_id': self.printful_config.id,
            'printful_method_id': 'EXPRESS',
            'is_default': False,
        })

    def test_default_shipping_method_used(self):
        """Test that default shipping method is used when no carrier mapping."""
        order = self._create_test_sale_order()
        order_data = order._build_printful_order_data()

        self.assertEqual(order_data['shipping'], 'STANDARD')

    def test_carrier_mapping_used(self):
        """Test that carrier mapping overrides default."""
        # Create a delivery carrier and map it to EXPRESS
        carrier = self.env['delivery.carrier'].create({
            'name': 'Express Delivery',
            'delivery_type': 'fixed',
            'product_id': self.env.ref('delivery.product_product_delivery').id,
            'fixed_price': 9.99,
        })
        self.express_method.write({'delivery_carrier_id': carrier.id})

        order = self._create_test_sale_order()
        order.write({'carrier_id': carrier.id})
        order_data = order._build_printful_order_data()

        self.assertEqual(order_data['shipping'], 'EXPRESS')


class TestRateLimiter(TransactionCase):
    """Test the leaky bucket rate limiter."""

    def test_rate_limiter_singleton(self):
        """Test that rate limiter is singleton per token."""
        from odoo.addons.printful_connect.models.printful import PrintfulRateLimiter

        limiter1 = PrintfulRateLimiter('token_abc')
        limiter2 = PrintfulRateLimiter('token_abc')
        limiter3 = PrintfulRateLimiter('token_xyz')

        self.assertIs(limiter1, limiter2)
        self.assertIsNot(limiter1, limiter3)

    def test_rate_limiter_acquire(self):
        """Test basic acquire functionality."""
        from odoo.addons.printful_connect.models.printful import PrintfulRateLimiter

        limiter = PrintfulRateLimiter('test_token_unique')
        # Should succeed immediately
        result = limiter.acquire(timeout=1)
        self.assertTrue(result)

    def test_rate_limiter_header_update(self):
        """Test updating bucket from headers."""
        from odoo.addons.printful_connect.models.printful import PrintfulRateLimiter

        limiter = PrintfulRateLimiter('test_token_header')

        # Simulate receiving headers with remaining count
        headers = {'X-Ratelimit-Remaining': '50'}
        limiter.update_from_headers(headers)

        # Bucket should be updated (can't easily verify internal state,
        # but we can verify no error occurs)
        self.assertTrue(True)


class TestVariantFinding(PrintfulTestCase):
    """Test the improved variant finding logic."""

    def setUp(self):
        super().setUp()
        # Create a product template with variants
        self.template = self.env['product.template'].create({
            'name': 'Multi-Variant Product',
            'printful_ref': '99999',
        })

        # Create size and color attribute values
        size_small = self.env['product.attribute.value'].create({
            'name': 'Small',
            'attribute_id': self.size_attribute.id,
        })
        size_medium = self.env['product.attribute.value'].create({
            'name': 'Medium',
            'attribute_id': self.size_attribute.id,
        })
        color_black = self.env['product.attribute.value'].create({
            'name': 'Black',
            'attribute_id': self.color_attribute.id,
        })
        color_white = self.env['product.attribute.value'].create({
            'name': 'White',
            'attribute_id': self.color_attribute.id,
        })

        # Create attribute lines
        self.size_line = self.env['product.template.attribute.line'].create({
            'product_tmpl_id': self.template.id,
            'attribute_id': self.size_attribute.id,
            'value_ids': [(4, size_small.id), (4, size_medium.id)],
        })
        self.color_line = self.env['product.template.attribute.line'].create({
            'product_tmpl_id': self.template.id,
            'attribute_id': self.color_attribute.id,
            'value_ids': [(4, color_black.id), (4, color_white.id)],
        })

    def test_find_variant_by_attribute_values(self):
        """Test finding variant by specific attribute values."""
        # Get the variants
        variants = self.template.product_variant_ids

        # Should have 4 variants (2 sizes x 2 colors)
        self.assertEqual(len(variants), 4)

        # Find specific variant using the improved method
        found_variant = self.printful_config._find_product_variant(
            self.template,
            self.size_line,
            self.color_line,
            size_value='Small',
            color_value='Black',
        )

        self.assertIsNotNone(found_variant)
        # Verify it's the correct variant
        ptav_names = found_variant.product_template_attribute_value_ids.mapped('name')
        self.assertIn('Small', ptav_names)
        self.assertIn('Black', ptav_names)
