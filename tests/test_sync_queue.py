# -*- coding: utf-8 -*-
"""
Tests for Printful sync queue functionality.
"""
from unittest.mock import patch, Mock
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from .common import PrintfulTestCase


@tagged('post_install', '-at_install', 'printful')
class TestSyncQueue(PrintfulTestCase):
    """Test cases for PrintfulSyncQueue model."""

    def test_queue_creation_with_sequence(self):
        """Test that queue gets a sequence name on creation."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        # Name should be set by sequence or default
        self.assertTrue(queue.name)
        self.assertEqual(queue.state, 'draft')

    def test_queue_statistics_computation(self):
        """Test that queue statistics are computed correctly."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })

        # Create items with different states
        self.env['printful.sync.queue.item'].create([
            {'queue_id': queue.id, 'name': 'Product 1', 'printful_product_id': '1', 'state': 'pending'},
            {'queue_id': queue.id, 'name': 'Product 2', 'printful_product_id': '2', 'state': 'pending'},
            {'queue_id': queue.id, 'name': 'Product 3', 'printful_product_id': '3', 'state': 'done'},
            {'queue_id': queue.id, 'name': 'Product 4', 'printful_product_id': '4', 'state': 'error'},
        ])

        # Force recompute
        queue.invalidate_recordset()

        self.assertEqual(queue.total_items, 4)
        self.assertEqual(queue.pending_items, 2)
        self.assertEqual(queue.completed_items, 1)
        self.assertEqual(queue.error_items, 1)
        self.assertEqual(queue.progress, 25.0)  # 1/4 = 25%

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_populate_queue_success(self, mock_get):
        """Test successful queue population from API."""
        mock_get.return_value = self._create_mock_response(self.MOCK_PRODUCTS_LIST)

        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })

        queue.action_populate_queue()

        self.assertEqual(queue.state, 'pending')
        self.assertEqual(len(queue.item_ids), 2)

        # Verify items were created correctly
        item_names = queue.item_ids.mapped('name')
        self.assertIn('Test T-Shirt', item_names)
        self.assertIn('Test Hoodie', item_names)

    @patch('odoo.addons.printful_connect.models.printful.requests.get')
    def test_populate_queue_api_error(self, mock_get):
        """Test queue population handles API errors."""
        mock_get.return_value = self._create_mock_response(self.MOCK_ERROR_RESPONSE, 401)

        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })

        with self.assertRaises(UserError):
            queue.action_populate_queue()

        self.assertEqual(queue.state, 'error')
        self.assertTrue(queue.error_message)

    def test_populate_queue_wrong_state(self):
        """Test that only draft queues can be populated."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'running',
        })

        with self.assertRaises(UserError) as context:
            queue.action_populate_queue()
        self.assertIn('draft', str(context.exception))

    def test_cancel_queue(self):
        """Test queue cancellation."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'pending',
        })

        queue.action_cancel()

        self.assertEqual(queue.state, 'cancelled')

    def test_cancel_completed_queue_fails(self):
        """Test that completed queues cannot be cancelled."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'done',
        })

        with self.assertRaises(UserError):
            queue.action_cancel()

    def test_reset_to_draft(self):
        """Test resetting queue to draft state."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'error',
        })
        self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Product 1',
            'printful_product_id': '1',
        })

        queue.action_reset_to_draft()

        self.assertEqual(queue.state, 'draft')
        self.assertEqual(len(queue.item_ids), 0)

    def test_retry_errors(self):
        """Test retrying failed items."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'error',
        })
        error_items = self.env['printful.sync.queue.item'].create([
            {'queue_id': queue.id, 'name': 'Product 1', 'printful_product_id': '1', 'state': 'error', 'error_message': 'Failed'},
            {'queue_id': queue.id, 'name': 'Product 2', 'printful_product_id': '2', 'state': 'error', 'error_message': 'Failed'},
            {'queue_id': queue.id, 'name': 'Product 3', 'printful_product_id': '3', 'state': 'done'},
        ])

        queue.action_retry_errors()

        # Error items should be reset to pending
        self.assertEqual(error_items[0].state, 'pending')
        self.assertEqual(error_items[1].state, 'pending')
        self.assertFalse(error_items[0].error_message)
        # Done item should remain done
        self.assertEqual(error_items[2].state, 'done')
        # Queue should be pending
        self.assertEqual(queue.state, 'pending')


@tagged('post_install', '-at_install', 'printful')
class TestSyncQueueItem(PrintfulTestCase):
    """Test cases for PrintfulSyncQueueItem model."""

    def test_item_progress_computation(self):
        """Test item progress percentage computation."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
            'variant_count': 10,
            'variants_synced': 3,
        })

        self.assertEqual(item.progress, 30.0)

    def test_item_progress_zero_variants(self):
        """Test progress is 0 when variant_count is 0."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
            'variant_count': 0,
        })

        self.assertEqual(item.progress, 0)

    def test_item_variant_summary(self):
        """Test variant summary string generation."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
            'variant_count': 12,
            'color_count': 3,
            'size_count': 4,
        })

        self.assertIn('3 colors', item.variant_summary)
        self.assertIn('4 sizes', item.variant_summary)
        self.assertIn('12 variants', item.variant_summary)

    def test_item_color_by_state(self):
        """Test Kanban color assignment based on state."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })

        states_colors = [
            ('pending', 0),
            ('in_progress', 4),
            ('done', 10),
            ('error', 1),
        ]

        for state, expected_color in states_colors:
            item = self.env['printful.sync.queue.item'].create({
                'queue_id': queue.id,
                'name': f'Test {state}',
                'printful_product_id': state,
                'state': state,
            })
            self.assertEqual(item.color, expected_color, f"Wrong color for state {state}")

    def test_update_progress_callback(self):
        """Test progress callback updates item correctly."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
        })

        item._update_progress(5, 10, colors=2, sizes=5)

        self.assertEqual(item.variants_synced, 5)
        self.assertEqual(item.variant_count, 10)
        self.assertEqual(item.color_count, 2)
        self.assertEqual(item.size_count, 5)

    def test_view_product_without_link(self):
        """Test viewing product raises error when not synced."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
        })

        with self.assertRaises(UserError) as context:
            item.action_view_product()
        self.assertIn('No product linked', str(context.exception))

    def test_view_product_with_link(self):
        """Test viewing product returns correct action."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        template = self._create_test_product()
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
            'product_template_id': template.id,
        })

        result = item.action_view_product()

        self.assertEqual(result['res_model'], 'product.template')
        self.assertEqual(result['res_id'], template.id)

    def test_retry_non_error_item_fails(self):
        """Test that only error items can be retried."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
            'state': 'done',
        })

        with self.assertRaises(UserError) as context:
            item.action_retry()
        self.assertIn('failed items', str(context.exception))


@tagged('post_install', '-at_install', 'printful')
class TestQueueBatchProcessing(PrintfulTestCase):
    """Test cases for batch processing functionality."""

    def test_process_next_batch_not_running(self):
        """Test that batch processing requires running state."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'pending',
        })

        result = queue.action_process_next_batch()

        self.assertFalse(result)

    @patch.object(type(PrintfulTestCase.env['printful.sync.queue.item']), 'action_sync_product')
    def test_process_next_batch_completes_queue(self, mock_sync):
        """Test that queue completes when all items processed."""
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.printful_config.id,
            'state': 'running',
        })
        item = self.env['printful.sync.queue.item'].create({
            'queue_id': queue.id,
            'name': 'Test Product',
            'printful_product_id': '12345',
            'state': 'pending',
        })

        # Mock sync to mark item as done
        def set_done(self):
            self.state = 'done'
        mock_sync.side_effect = lambda: item.write({'state': 'done'})

        result = queue.action_process_next_batch(batch_size=5)

        # Queue should be done since no more pending items
        self.assertFalse(result)
