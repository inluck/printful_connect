# -*- coding: utf-8 -*-
"""
Tests for product synchronization functionality.
"""
from unittest.mock import patch, Mock
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from .common import PrintfulTestCase


@tagged('post_install', '-at_install', 'printful')
class TestProductTemplateSync(PrintfulTestCase):
    """Test cases for product template synchronization."""

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_upsert_product_template_create(self, mock_get):
        """Test creating a new product template."""
        # Mock image download
        mock_get.return_value = self._create_mock_response({})

        sync_product = {
            'id': 99999,
            'name': 'New Product',
            'external_id': 'ext_99999',
            'thumbnail_url': 'https://example.com/thumb.jpg',
        }

        template = self.printful_config._upsert_product_template(sync_product, {})

        self.assertEqual(template.name, 'New Product')
        self.assertEqual(template.printful_ref, '99999')
        self.assertEqual(template.printful_external_ref, 'ext_99999')

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_upsert_product_template_update(self, mock_get):
        """Test updating an existing product template."""
        mock_get.return_value = self._create_mock_response({})

        # Create existing product
        existing = self.env['product.template'].create({
            'name': 'Old Name',
            'printful_ref': '88888',
        })

        sync_product = {
            'id': 88888,
            'name': 'Updated Name',
            'external_id': 'ext_88888',
        }

        template = self.printful_config._upsert_product_template(sync_product, {})

        self.assertEqual(template.id, existing.id)
        self.assertEqual(template.name, 'Updated Name')

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_upsert_product_template_image_failure(self, mock_get):
        """Test that image download failure doesn't block product creation."""
        mock_get.side_effect = Exception('Image download failed')

        sync_product = {
            'id': 77777,
            'name': 'Product Without Image',
            'external_id': 'ext_77777',
            'thumbnail_url': 'https://example.com/bad.jpg',
        }

        # Should not raise, just log warning
        template = self.printful_config._upsert_product_template(sync_product, {})

        self.assertEqual(template.name, 'Product Without Image')
        self.assertFalse(template.image_1920)


@tagged('post_install', '-at_install', 'printful')
class TestSingleProductSync(PrintfulTestCase):
    """Test cases for single product synchronization."""

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_sync_all_variants_fail_raises_error(self, mock_get):
        """Test that sync raises error when all variants fail."""
        # Mock product fetch success but variant fetch failure
        def get_side_effect(url, **kwargs):
            if 'store/products/12345' in url:
                return self._create_mock_response(self.MOCK_PRODUCT_DETAIL)
            elif 'products/variant' in url:
                # All variants fail
                mock_response = Mock()
                mock_response.status_code = 500
                return mock_response
            else:
                return self._create_mock_response({'code': 200, 'result': {}})

        mock_get.side_effect = get_side_effect

        with self.assertRaises(UserError) as context:
            self.printful_config._sync_single_product('12345')
        self.assertIn('Failed to sync any variants', str(context.exception))

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_sync_single_product_success(self, mock_post, mock_get):
        """Test successful single product sync."""
        # Setup mock responses
        def get_side_effect(url, **kwargs):
            if 'store/products/12345' in url:
                return self._create_mock_response(self.MOCK_PRODUCT_DETAIL)
            elif 'products/variant' in url:
                return self._create_mock_response(self.MOCK_VARIANT_DETAIL)
            else:
                return self._create_mock_response({'code': 200, 'result': {}})

        mock_get.side_effect = get_side_effect
        mock_post.return_value = self._create_mock_response(self.MOCK_SHIPPING_RATES)

        self.printful_config.default_shipping_country_id = self.env.ref('base.us')

        template = self.printful_config._sync_single_product('12345')

        self.assertIsNotNone(template)
        self.assertEqual(template.name, 'Test T-Shirt')
        self.assertEqual(template.printful_ref, '12345')

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_sync_single_product_api_error(self, mock_get):
        """Test sync handles API errors gracefully."""
        mock_get.return_value = self._create_mock_response({
            'code': 404,
            'result': 'Product not found'
        })

        with self.assertRaises(UserError) as context:
            self.printful_config._sync_single_product('99999')
        self.assertIn('Failed to fetch', str(context.exception))

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_sync_single_product_no_variants(self, mock_get):
        """Test sync returns None when product has no variants."""
        mock_get.return_value = self._create_mock_response({
            'code': 200,
            'result': {
                'sync_product': {'id': 12345, 'name': 'Empty Product'},
                'sync_variants': [],
            }
        })

        result = self.printful_config._sync_single_product('12345')

        self.assertIsNone(result)

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_sync_single_product_with_progress_callback(self, mock_post, mock_get):
        """Test progress callback is called during sync."""
        def get_side_effect(url, **kwargs):
            if 'store/products/12345' in url:
                return self._create_mock_response(self.MOCK_PRODUCT_DETAIL)
            elif 'products/variant' in url:
                return self._create_mock_response(self.MOCK_VARIANT_DETAIL)
            else:
                return self._create_mock_response({'code': 200, 'result': {}})

        mock_get.side_effect = get_side_effect
        mock_post.return_value = self._create_mock_response(self.MOCK_SHIPPING_RATES)

        self.printful_config.default_shipping_country_id = self.env.ref('base.us')

        callback_calls = []
        def progress_callback(synced, total, colors, sizes):
            callback_calls.append((synced, total, colors, sizes))

        self.printful_config._sync_single_product('12345', progress_callback=progress_callback)

        # Should have been called for each variant
        self.assertGreater(len(callback_calls), 0)


@tagged('post_install', '-at_install', 'printful')
class TestVariantProcessing(PrintfulTestCase):
    """Test cases for variant processing."""

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    @patch('odoo.addons.printful_connect.models.printful.requests.post')
    def test_process_sync_variant_extracts_size_color(self, mock_post, mock_get):
        """Test that variant processing extracts size and color."""
        mock_get.return_value = self._create_mock_response(self.MOCK_VARIANT_DETAIL)
        mock_post.return_value = self._create_mock_response(self.MOCK_SHIPPING_RATES)

        template = self._create_test_product()
        sync_variant = {
            'id': 1001,
            'external_id': 'var_1001',
            'variant_id': 4011,
            'retail_price': '25.00',
            'sku': 'TEST-SKU',
            'currency': 'USD',
            'product': {'product_id': 71},
        }

        result = self.printful_config._process_sync_variant(
            sync_variant,
            template,
            self.printful_config,
            {},
            lowest_price=25.0
        )

        self.assertEqual(result['size'], 'S')
        self.assertEqual(result['color'], 'Black')

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_process_sync_variant_api_failure(self, mock_get):
        """Test variant processing handles API failure."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        template = self._create_test_product()
        sync_variant = {'variant_id': 9999}

        result = self.printful_config._process_sync_variant(
            sync_variant,
            template,
            self.printful_config,
            {},
            lowest_price=25.0
        )

        # Should return empty result dict, not raise
        self.assertIsNone(result['size'])
        self.assertIsNone(result['color'])


@tagged('post_install', '-at_install', 'printful')
class TestSizeGuide(PrintfulTestCase):
    """Test cases for size guide generation."""

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_get_size_guide_generates_html(self, mock_get):
        """Test size guide generates valid HTML."""
        mock_get.return_value = self._create_mock_response({
            'code': 200,
            'result': {
                'available_sizes': ['S', 'M', 'L'],
                'size_tables': [{
                    'type': 'measure_yourself',
                    'unit': 'inches',
                    'description': 'Measure yourself',
                    'measurements': [{
                        'type_label': 'Length',
                        'values': [
                            {'size': 'S', 'value': '27'},
                            {'size': 'M', 'value': '28'},
                            {'size': 'L', 'value': '29'},
                        ]
                    }]
                }]
            }
        })

        result = self.printful_config._get_size_guide({}, '71')

        self.assertIn('<h2>', result)
        self.assertIn('Measure Yourself', result)
        self.assertIn('<table', result)

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_get_size_guide_safe_returns_empty_on_error(self, mock_get):
        """Test safe size guide returns empty string on error."""
        mock_get.side_effect = Exception('API Error')

        result = self.printful_config._get_size_guide_safe({}, '71')

        self.assertEqual(result, '')

    def test_get_size_guide_safe_no_product_id(self):
        """Test safe size guide returns empty for None product_id."""
        result = self.printful_config._get_size_guide_safe({}, None)
        self.assertEqual(result, '')


@tagged('post_install', '-at_install', 'printful')
class TestProductImage(PrintfulTestCase):
    """Test cases for product image handling."""

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_upsert_product_image_create(self, mock_get):
        """Test creating a new product image."""
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        template = self._create_test_product()

        self.printful_config._upsert_product_image(
            'Front View',
            'https://example.com/front.jpg',
            template
        )

        images = self.env['product.image'].search([
            ('product_tmpl_id', '=', template.id),
            ('name', '=', 'Front View'),
        ])
        self.assertEqual(len(images), 1)

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_upsert_product_image_update(self, mock_get):
        """Test updating an existing product image."""
        mock_response = Mock()
        mock_response.content = b'new_image_data'
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        template = self._create_test_product()

        # Create existing image
        self.env['product.image'].create({
            'name': 'Existing Image',
            'product_tmpl_id': template.id,
        })

        self.printful_config._upsert_product_image(
            'Existing Image',
            'https://example.com/new.jpg',
            template
        )

        images = self.env['product.image'].search([
            ('product_tmpl_id', '=', template.id),
            ('name', '=', 'Existing Image'),
        ])
        self.assertEqual(len(images), 1)

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_upsert_product_image_download_failure(self, mock_get):
        """Test image creation handles download failure."""
        mock_get.side_effect = Exception('Download failed')

        template = self._create_test_product()

        # Should not raise
        self.printful_config._upsert_product_image(
            'Failed Image',
            'https://example.com/bad.jpg',
            template
        )

        # No image should be created
        images = self.env['product.image'].search([
            ('product_tmpl_id', '=', template.id),
            ('name', '=', 'Failed Image'),
        ])
        self.assertEqual(len(images), 0)
