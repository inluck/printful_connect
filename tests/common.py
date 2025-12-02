# -*- coding: utf-8 -*-
"""
Common test utilities and mock data for Printful Connect tests.
"""
from unittest.mock import Mock
from odoo.tests.common import TransactionCase


class PrintfulTestCase(TransactionCase):
    """
    Base test case for Printful Connect tests.
    Provides common fixtures and mock API responses.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_printful_config()
        cls._setup_attributes()
        cls._setup_mock_responses()

    @classmethod
    def _setup_printful_config(cls):
        """Create a test Printful configuration."""
        cls.printful_config = cls.env['printful.printful'].create({
            'name': 'Test Printful Store',
            'token': 'test_api_token_12345',
        })

    @classmethod
    def _setup_attributes(cls):
        """Create size and color attributes for testing."""
        cls.size_attribute = cls.env['product.attribute'].create({
            'name': 'Size',
            'display_type': 'radio',
            'create_variant': 'always',
        })
        cls.color_attribute = cls.env['product.attribute'].create({
            'name': 'Color',
            'display_type': 'color',
            'create_variant': 'always',
        })
        # Link attributes to config
        cls.printful_config.write({
            'size_attribute_id': cls.size_attribute.id,
            'color_attribute_id': cls.color_attribute.id,
        })

    @classmethod
    def _setup_mock_responses(cls):
        """Define mock API response data."""
        cls.MOCK_PRODUCTS_LIST = {
            'code': 200,
            'result': [
                {
                    'id': 12345,
                    'external_id': 'ext_12345',
                    'name': 'Test T-Shirt',
                    'variants': 6,
                    'synced': 6,
                    'thumbnail_url': 'https://example.com/thumb.jpg',
                    'is_ignored': False,
                },
                {
                    'id': 12346,
                    'external_id': 'ext_12346',
                    'name': 'Test Hoodie',
                    'variants': 4,
                    'synced': 4,
                    'thumbnail_url': 'https://example.com/thumb2.jpg',
                    'is_ignored': False,
                },
            ]
        }

        cls.MOCK_PRODUCT_DETAIL = {
            'code': 200,
            'result': {
                'sync_product': {
                    'id': 12345,
                    'external_id': 'ext_12345',
                    'name': 'Test T-Shirt',
                    'variants': 2,
                    'synced': 2,
                    'thumbnail_url': 'https://example.com/thumb.jpg',
                },
                'sync_variants': [
                    {
                        'id': 1001,
                        'external_id': 'var_1001',
                        'sync_product_id': 12345,
                        'name': 'Test T-Shirt - S / Black',
                        'synced': True,
                        'variant_id': 4011,
                        'retail_price': '25.00',
                        'currency': 'USD',
                        'sku': 'TSHIRT-S-BLK',
                        'product': {
                            'variant_id': 4011,
                            'product_id': 71,
                            'name': 'Unisex Staple T-Shirt',
                        },
                    },
                    {
                        'id': 1002,
                        'external_id': 'var_1002',
                        'sync_product_id': 12345,
                        'name': 'Test T-Shirt - M / Black',
                        'synced': True,
                        'variant_id': 4012,
                        'retail_price': '26.00',
                        'currency': 'USD',
                        'sku': 'TSHIRT-M-BLK',
                        'product': {
                            'variant_id': 4012,
                            'product_id': 71,
                            'name': 'Unisex Staple T-Shirt',
                        },
                    },
                ],
            }
        }

        cls.MOCK_VARIANT_DETAIL = {
            'code': 200,
            'result': {
                'variant': {
                    'id': 4011,
                    'product_id': 71,
                    'name': 'Bella + Canvas 3001 - S',
                    'size': 'S',
                    'color': 'Black',
                    'color_code': '#000000',
                    'in_stock': True,
                    'price': '12.95',
                },
                'product': {
                    'id': 71,
                    'type': 'T-SHIRT',
                    'brand': 'Bella + Canvas',
                    'model': '3001 Unisex Short Sleeve Jersey Tee',
                    'description': 'A comfortable t-shirt for everyday wear.',
                },
            }
        }

        cls.MOCK_ORDER_CREATE = {
            'code': 200,
            'result': {
                'id': 98765,
                'external_id': 'ODOO-test-uuid',
                'store': 12345,
                'status': 'draft',
                'shipping': 'STANDARD',
                'shipping_service_name': 'Flat Rate (3-4 business days)',
                'costs': {
                    'currency': 'USD',
                    'subtotal': '25.00',
                    'discount': '0.00',
                    'shipping': '4.99',
                    'digitization': '0.00',
                    'additional_fee': '0.00',
                    'fulfillment_fee': '0.00',
                    'retail_delivery_fee': '0.00',
                    'tax': '0.00',
                    'vat': '0.00',
                    'total': '29.99',
                },
                'pricing_breakdown': [{
                    'customer_pays': '29.99',
                    'printful_price': '17.94',
                    'profit': '12.05',
                    'currency_symbol': '$',
                }],
                'dashboard_url': 'https://www.printful.com/dashboard/order/98765',
            }
        }

        cls.MOCK_ORDER_CONFIRM = {
            'code': 200,
            'result': {
                'id': 98765,
                'external_id': 'ODOO-test-uuid',
                'status': 'pending',
                'costs': {
                    'currency': 'USD',
                    'subtotal': '25.00',
                    'discount': '0.00',
                    'shipping': '4.99',
                    'tax': '0.00',
                    'total': '29.99',
                },
                'pricing_breakdown': [{
                    'customer_pays': '29.99',
                    'printful_price': '17.94',
                    'profit': '12.05',
                    'currency_symbol': '$',
                }],
            }
        }

        cls.MOCK_SHIPPING_RATES = {
            'code': 200,
            'result': [
                {
                    'id': 'STANDARD',
                    'name': 'Flat Rate',
                    'rate': '4.99',
                    'currency': 'USD',
                    'minDeliveryDays': 3,
                    'maxDeliveryDays': 4,
                },
                {
                    'id': 'EXPRESS',
                    'name': 'Express',
                    'rate': '9.99',
                    'currency': 'USD',
                    'minDeliveryDays': 1,
                    'maxDeliveryDays': 2,
                },
            ]
        }

        cls.MOCK_ERROR_RESPONSE = {
            'code': 401,
            'result': 'Unauthorized',
            'error': {
                'reason': 'Unauthorized',
                'message': 'Invalid API key',
            }
        }

    def _create_mock_response(self, json_data, status_code=200):
        """Create a mock requests.Response object."""
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.json.return_value = json_data
        mock_response.content = b'mock_image_data'
        mock_response.raise_for_status = Mock()
        if status_code >= 400:
            mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return mock_response

    def _create_test_partner(self, name='Test Customer'):
        """Create a test partner/customer."""
        country_us = self.env.ref('base.us')
        state_ny = self.env['res.country.state'].search([
            ('country_id', '=', country_us.id),
            ('code', '=', 'NY')
        ], limit=1)

        return self.env['res.partner'].create({
            'name': name,
            'street': '123 Test Street',
            'street2': 'Apt 4B',
            'city': 'New York',
            'state_id': state_ny.id if state_ny else False,
            'country_id': country_us.id,
            'zip': '10001',
            'email': 'test@example.com',
            'phone': '+1-555-123-4567',
        })

    def _create_test_product(self, name='Test Product', printful_ref='12345'):
        """Create a test product template with Printful fields."""
        return self.env['product.template'].create({
            'name': name,
            'printful_ref': printful_ref,
            'printful_external_ref': f'ext_{printful_ref}',
            'list_price': 25.00,
            'standard_price': 12.95,
        })

    def _create_test_variant(self, template, printful_variant_ref='var_1001'):
        """Create a test product variant with Printful fields."""
        variant = template.product_variant_ids[0] if template.product_variant_ids else None
        if variant:
            variant.write({
                'printful_variant_ref': printful_variant_ref,
                'printful_variant_id': '4011',
                'printful_sku': 'TEST-SKU-001',
                'printful_product_in_stock': True,
            })
        return variant

    def _create_test_sale_order(self, partner=None, product=None):
        """Create a test sale order."""
        if not partner:
            partner = self._create_test_partner()
        if not product:
            template = self._create_test_product()
            product = self._create_test_variant(template)

        order = self.env['sale.order'].create({
            'partner_id': partner.id,
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': 1,
            'price_unit': 25.00,
        })
        return order

    def _create_default_shipping_address(self, is_primary=True):
        """Create a default shipping address for the Printful config."""
        country_us = self.env.ref('base.us')
        state_ny = self.env['res.country.state'].search([
            ('country_id', '=', country_us.id),
            ('code', '=', 'NY')
        ], limit=1)

        return self.env['printful.default.address'].create({
            'name': 'US Default Address',
            'printful_config_id': self.printful_config.id,
            'country_id': country_us.id,
            'state_id': state_ny.id if state_ny else False,
            'city': 'New York',
            'zip_code': '10001',
            'address1': '123 Test Street',
            'is_primary': is_primary,
        })
