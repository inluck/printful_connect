# -*- coding: utf-8 -*-
"""
Tests for Printful API integration methods.
"""
from unittest.mock import patch, Mock
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from .common import PrintfulTestCase


@tagged('post_install', '-at_install', 'printful')
class TestPrintfulAPI(PrintfulTestCase):
    """Test cases for Printful API helper methods."""

    def test_get_auth_headers_with_token(self):
        """Test that auth headers are generated correctly with valid token."""
        headers = self.printful_config._get_auth_headers()
        self.assertEqual(headers['Authorization'], 'Bearer test_api_token_12345')

    def test_get_auth_headers_without_token(self):
        """Test that missing token raises UserError."""
        config = self.env['printful.printful'].create({
            'name': 'Empty Config',
            'token': False,
        })
        with self.assertRaises(UserError) as context:
            config._get_auth_headers()
        self.assertIn('API token', str(context.exception))

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_make_api_request_success(self, mock_get):
        """Test successful API request."""
        mock_get.return_value = self._create_mock_response(self.MOCK_PRODUCTS_LIST)

        response = self.printful_config._make_api_request(
            'https://api.printful.com/store/products',
            {'Authorization': 'Bearer test'}
        )

        self.assertEqual(response.json()['code'], 200)
        mock_get.assert_called_once()

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_make_api_request_rate_limit(self, mock_get):
        """Test that rate limit errors are handled."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = Exception('Rate limit exceeded')
        mock_get.return_value = mock_response

        with self.assertRaises(Exception) as context:
            self.printful_config._make_api_request(
                'https://api.printful.com/store/products',
                {'Authorization': 'Bearer test'}
            )
        self.assertIn('Rate limit', str(context.exception))

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_make_api_request_with_timeout(self, mock_get):
        """Test that API requests include timeout."""
        mock_get.return_value = self._create_mock_response({'code': 200})

        self.printful_config._make_api_request(
            'https://api.printful.com/test',
            {'Authorization': 'Bearer test'}
        )

        # Verify timeout was passed
        call_kwargs = mock_get.call_args[1]
        self.assertEqual(call_kwargs.get('timeout'), 30)


@tagged('post_install', '-at_install', 'printful')
class TestPrintfulConfig(PrintfulTestCase):
    """Test cases for Printful configuration model."""

    def test_create_config_with_defaults(self):
        """Test creating a Printful config with default values."""
        config = self.env['printful.printful'].create({
            'name': 'New Store',
            'token': 'new_token',
        })
        # Currency should default to company currency
        self.assertEqual(config.currency_id, self.env.company.currency_id)

    def test_create_sync_queue_action(self):
        """Test that create_sync_queue action creates a queue."""
        result = self.printful_config.action_create_sync_queue()

        self.assertEqual(result['res_model'], 'printful.sync.queue')
        queue = self.env['printful.sync.queue'].browse(result['res_id'])
        self.assertEqual(queue.printful_config_id, self.printful_config)
        self.assertEqual(queue.state, 'draft')


@tagged('post_install', '-at_install', 'printful')
class TestShippingInfo(PrintfulTestCase):
    """Test cases for shipping information retrieval."""

    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_get_shipping_info_success(self, mock_post):
        """Test successful shipping info retrieval."""
        mock_post.return_value = self._create_mock_response(self.MOCK_SHIPPING_RATES)

        # Set up default shipping address
        self._create_default_shipping_address()

        sync_variant = {
            'variant_id': 4011,
            'external_id': 'var_1001',
            'retail_price': '25.00',
        }
        headers = {'Authorization': 'Bearer test'}

        result = self.printful_config._get_shipping_info(sync_variant, headers)

        self.assertEqual(result, '3-4 Business Days')

    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_get_shipping_info_no_address(self, mock_post):
        """Test shipping info returns None when no default address configured."""
        # Ensure no default address is configured
        self.printful_config.default_address_ids.unlink()

        sync_variant = {'variant_id': 4011}
        result = self.printful_config._get_shipping_info(sync_variant, {})

        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_get_shipping_info_api_error(self, mock_post):
        """Test shipping info returns None on API error."""
        mock_post.side_effect = Exception('API Error')
        self._create_default_shipping_address()

        sync_variant = {'variant_id': 4011, 'retail_price': '25.00'}
        result = self.printful_config._get_shipping_info(sync_variant, {})

        self.assertIsNone(result)


@tagged('post_install', '-at_install', 'printful')
class TestCategoryHelpers(PrintfulTestCase):
    """Test cases for category management helpers."""

    def test_get_or_create_category_new(self):
        """Test creating a new category."""
        cat_id = self.printful_config._get_or_create_category('New Category', None)

        self.assertIsNotNone(cat_id)
        category = self.env['product.public.category'].browse(cat_id)
        self.assertEqual(category.name, 'New Category')

    def test_get_or_create_category_existing(self):
        """Test getting an existing category."""
        existing = self.env['product.public.category'].create({
            'name': 'Existing Category',
        })

        cat_id = self.printful_config._get_or_create_category('Existing Category', None)

        self.assertEqual(cat_id, existing.id)

    def test_get_or_create_category_empty_name(self):
        """Test that empty category name returns None."""
        result = self.printful_config._get_or_create_category('', None)
        self.assertIsNone(result)

        result = self.printful_config._get_or_create_category(None, None)
        self.assertIsNone(result)


@tagged('post_install', '-at_install', 'printful')
class TestAttributeHelpers(PrintfulTestCase):
    """Test cases for attribute management helpers."""

    def test_get_or_create_attribute_value_new(self):
        """Test creating a new attribute value."""
        value_id = self.printful_config._get_or_create_attribute_value(
            self.size_attribute,
            'XL'
        )

        self.assertIsNotNone(value_id)
        value = self.env['product.attribute.value'].browse(value_id)
        self.assertEqual(value.name, 'XL')
        self.assertEqual(value.attribute_id, self.size_attribute)

    def test_get_or_create_attribute_value_existing(self):
        """Test getting an existing attribute value."""
        existing = self.env['product.attribute.value'].create({
            'name': 'Medium',
            'attribute_id': self.size_attribute.id,
        })

        value_id = self.printful_config._get_or_create_attribute_value(
            self.size_attribute,
            'Medium'
        )

        self.assertEqual(value_id, existing.id)

    def test_get_or_create_attribute_value_empty(self):
        """Test that empty value returns None."""
        result = self.printful_config._get_or_create_attribute_value(
            self.size_attribute,
            ''
        )
        self.assertIsNone(result)

    def test_get_or_create_attribute_line(self):
        """Test creating an attribute line on a product."""
        template = self._create_test_product()
        value_id = self.env['product.attribute.value'].create({
            'name': 'Small',
            'attribute_id': self.size_attribute.id,
        }).id

        line = self.printful_config._get_or_create_attribute_line(
            self.size_attribute,
            template,
            value_id
        )

        self.assertIsNotNone(line)
        self.assertEqual(line.attribute_id, self.size_attribute)
        self.assertEqual(line.product_tmpl_id, template)
