# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class PrintfulSyncQueue(models.Model):
    """
    Represents a sync session/batch job for fetching products from Printful.
    Each queue can contain multiple product items to sync.
    """
    _name = 'printful.sync.queue'
    _description = 'Printful Sync Queue'
    _order = 'create_date desc'

    name = fields.Char(
        string='Sync Batch',
        required=True,
        default=lambda self: _('New Sync'),
        copy=False,
    )
    printful_config_id = fields.Many2one(
        comodel_name='printful.printful',
        string='Printful Store',
        required=True,
        ondelete='cascade',
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Queued'),
        ('running', 'Running'),
        ('done', 'Completed'),
        ('error', 'Error'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    # Queue items
    item_ids = fields.One2many(
        comodel_name='printful.sync.queue.item',
        inverse_name='queue_id',
        string='Products to Sync',
    )

    # Statistics
    total_items = fields.Integer(
        string='Total Products',
        compute='_compute_statistics',
        store=True,
    )
    pending_items = fields.Integer(
        string='Pending',
        compute='_compute_statistics',
        store=True,
    )
    completed_items = fields.Integer(
        string='Completed',
        compute='_compute_statistics',
        store=True,
    )
    error_items = fields.Integer(
        string='Errors',
        compute='_compute_statistics',
        store=True,
    )
    progress = fields.Float(
        string='Progress',
        compute='_compute_statistics',
        store=True,
    )

    # Timing
    started_at = fields.Datetime(string='Started At')
    completed_at = fields.Datetime(string='Completed At')
    duration = fields.Float(
        string='Duration (min)',
        compute='_compute_duration',
    )

    # Error tracking
    error_message = fields.Text(string='Error Details')

    @api.depends('item_ids', 'item_ids.state')
    def _compute_statistics(self):
        for record in self:
            items = record.item_ids
            record.total_items = len(items)
            record.pending_items = len(items.filtered(lambda x: x.state in ('pending', 'in_progress')))
            record.completed_items = len(items.filtered(lambda x: x.state == 'done'))
            record.error_items = len(items.filtered(lambda x: x.state == 'error'))
            record.progress = (record.completed_items / record.total_items * 100) if record.total_items else 0

    @api.depends('started_at', 'completed_at')
    def _compute_duration(self):
        for record in self:
            if record.started_at and record.completed_at:
                delta = record.completed_at - record.started_at
                record.duration = delta.total_seconds() / 60
            elif record.started_at:
                delta = fields.Datetime.now() - record.started_at
                record.duration = delta.total_seconds() / 60
            else:
                record.duration = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New Sync')) == _('New Sync'):
                vals['name'] = self.env['ir.sequence'].next_by_code('printful.sync.queue') or _('New Sync')
        return super().create(vals_list)

    def action_populate_queue(self):
        """Fetch product list from Printful and populate the queue."""
        self.ensure_one()
        if self.state not in ('draft',):
            raise UserError(_('Can only populate a draft queue.'))

        config = self.printful_config_id
        # Use centralized auth method for consistent token validation
        headers = config._get_auth_headers()
        url = "https://api.printful.com/store/products"

        try:
            response = config._make_api_request(url, headers)
            data = response.json()

            if data.get('code') != 200:
                raise UserError(_('Failed to fetch products: %s') % data.get('result', 'Unknown error'))

            products = data.get('result', [])
            items_to_create = []

            for product in products:
                # Check if this product is already in queue
                existing = self.item_ids.filtered(
                    lambda x: x.printful_product_id == str(product['id'])
                )
                if not existing:
                    items_to_create.append({
                        'queue_id': self.id,
                        'name': product.get('name', 'Unknown Product'),
                        'printful_product_id': str(product['id']),
                        'printful_external_id': str(product.get('external_id', '')),
                        'thumbnail_url': product.get('thumbnail_url', ''),
                        'variant_count': product.get('variants', 0),
                        'state': 'pending',
                    })

            if items_to_create:
                self.env['printful.sync.queue.item'].create(items_to_create)

            self.state = 'pending'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Queue Populated'),
                    'message': _('%d products added to sync queue.') % len(items_to_create),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            self.write({
                'state': 'error',
                'error_message': str(e),
            })
            raise UserError(_('Failed to populate queue: %s') % str(e))

    def action_start_sync(self):
        """
        Start processing the sync queue.

        This method sets the queue state to 'running' and processes the first
        batch of items. The cron job (cron_process_sync_queue) will continue
        processing remaining items in batches every 2 minutes to avoid
        HTTP request timeouts on large catalogs.
        """
        self.ensure_one()
        if self.state not in ('pending', 'error'):
            raise UserError(_('Can only start a pending or errored queue.'))

        self.write({
            'state': 'running',
            'started_at': fields.Datetime.now(),
            'error_message': False,
        })

        # Reset error items for retry
        error_items = self.item_ids.filtered(lambda x: x.state == 'error')
        if error_items:
            error_items.write({'state': 'pending', 'error_message': False})

        # Process first batch immediately to give user feedback
        # Remaining items will be processed by the cron job
        self.action_process_next_batch(batch_size=5)

        # Return notification to inform user about background processing
        remaining = len(self.item_ids.filtered(lambda x: x.state == 'pending'))
        if remaining > 0:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Started'),
                    'message': _(
                        'First batch processed. %d products remaining will be '
                        'synced automatically in the background (every 2 minutes).'
                    ) % remaining,
                    'type': 'info',
                    'sticky': False,
                }
            }

    def action_process_next_batch(self, batch_size=5):
        """
        Process next batch of items. Called by cron or manually.

        Uses database-level locking to prevent concurrent cron jobs from
        processing the same items simultaneously.
        """
        self.ensure_one()
        if self.state != 'running':
            return False

        # Use raw SQL with FOR UPDATE SKIP LOCKED to safely select items
        # that aren't being processed by another transaction
        self.env.cr.execute("""
            SELECT id FROM printful_sync_queue_item
            WHERE queue_id = %s AND state = 'pending'
            ORDER BY sequence, id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """, (self.id, batch_size))

        item_ids = [row[0] for row in self.env.cr.fetchall()]

        if not item_ids:
            # No items available (either none pending or all locked by other transactions)
            # Check if queue is actually complete
            remaining_count = self.env.cr.execute("""
                SELECT COUNT(*) FROM printful_sync_queue_item
                WHERE queue_id = %s AND state = 'pending'
            """, (self.id,))
            remaining = self.env.cr.fetchone()[0]

            if remaining == 0:
                self.write({
                    'state': 'done' if self.error_items == 0 else 'error',
                    'completed_at': fields.Datetime.now(),
                })
                return False
            else:
                # Items exist but are locked by another process
                _logger.debug(
                    "Queue %s: %d pending items locked by another process",
                    self.name, remaining
                )
                return True  # Still work to do, but handled by other process

        # Process the locked items
        pending_items = self.env['printful.sync.queue.item'].browse(item_ids)

        for item in pending_items:
            try:
                item.action_sync_product()
            except Exception as e:
                _logger.error("Failed to sync product %s: %s", item.name, str(e))
                item.write({
                    'state': 'error',
                    'error_message': str(e),
                })

        # Check if queue is complete
        remaining = self.item_ids.filtered(lambda x: x.state == 'pending')
        if not remaining:
            self.write({
                'state': 'done' if self.error_items == 0 else 'error',
                'completed_at': fields.Datetime.now(),
            })
            return False

        return True

    def action_retry_errors(self):
        """Retry all failed items."""
        self.ensure_one()
        error_items = self.item_ids.filtered(lambda x: x.state == 'error')
        error_items.write({'state': 'pending', 'error_message': False})

        if self.state == 'error':
            self.state = 'pending'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Retry Queued'),
                'message': _('%d items reset for retry.') % len(error_items),
                'type': 'info',
                'sticky': False,
            }
        }

    def action_cancel(self):
        """Cancel the sync queue."""
        self.ensure_one()
        if self.state == 'done':
            raise UserError(_('Cannot cancel a completed queue.'))
        self.state = 'cancelled'

    def action_reset_to_draft(self):
        """Reset queue to draft state."""
        self.ensure_one()
        self.item_ids.unlink()
        self.write({
            'state': 'draft',
            'started_at': False,
            'completed_at': False,
            'error_message': False,
        })

    @api.model
    def _recover_stale_queues(self, stale_timeout_minutes=30):
        """
        Recover queues that have been stuck in 'running' state for too long.

        This handles cases where:
        - Server crashed during sync
        - Cron job failed unexpectedly
        - Network issues caused sync to hang

        Args:
            stale_timeout_minutes: Minutes after which a running queue is considered stale

        Returns:
            Number of queues recovered
        """
        # Use Odoo's fields.Datetime.now() for UTC consistency
        # datetime.now() is timezone-naive local time, but Odoo stores in UTC
        stale_threshold = fields.Datetime.now() - timedelta(minutes=stale_timeout_minutes)

        # Find queues that have been running for too long
        stale_queues = self.search([
            ('state', '=', 'running'),
            ('started_at', '<', stale_threshold),
        ])

        recovered_count = 0
        for queue in stale_queues:
            # Check if there are still pending items
            pending_items = queue.item_ids.filtered(lambda x: x.state == 'pending')
            in_progress_items = queue.item_ids.filtered(lambda x: x.state == 'in_progress')

            # Reset any stuck "in_progress" items back to pending
            if in_progress_items:
                in_progress_items.write({
                    'state': 'pending',
                    'error_message': _('Reset due to stale queue recovery'),
                })

            if pending_items or in_progress_items:
                # There's still work to do - keep running but log warning
                _logger.warning(
                    "Queue %s (ID: %d) has been running for over %d minutes. "
                    "Resetting %d in-progress items and continuing.",
                    queue.name, queue.id, stale_timeout_minutes, len(in_progress_items)
                )
            else:
                # No pending work - mark as done or error based on results
                if queue.error_items > 0:
                    queue.write({
                        'state': 'error',
                        'completed_at': fields.Datetime.now(),
                        'error_message': _(
                            'Queue recovered from stale state. '
                            'Some items failed during sync.'
                        ),
                    })
                else:
                    queue.write({
                        'state': 'done',
                        'completed_at': fields.Datetime.now(),
                    })
                _logger.info(
                    "Recovered stale queue %s (ID: %d) - marked as %s",
                    queue.name, queue.id, queue.state
                )
            recovered_count += 1

        return recovered_count


class PrintfulSyncQueueItem(models.Model):
    """
    Represents a single product to sync from Printful.
    Tracks progress of variant synchronization.
    """
    _name = 'printful.sync.queue.item'
    _description = 'Printful Sync Queue Item'
    _order = 'sequence, id'

    queue_id = fields.Many2one(
        comodel_name='printful.sync.queue',
        string='Sync Queue',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(string='Sequence', default=10)

    # Product identification
    name = fields.Char(string='Product Name', required=True)
    printful_product_id = fields.Char(string='Printful Product ID', required=True)
    printful_external_id = fields.Char(string='External ID')
    thumbnail_url = fields.Char(string='Thumbnail URL')

    # Linked Odoo product
    product_template_id = fields.Many2one(
        comodel_name='product.template',
        string='Odoo Product',
    )

    # State tracking
    state = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'Syncing'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], string='Status', default='pending')

    # Progress tracking
    variant_count = fields.Integer(string='Total Variants')
    variants_synced = fields.Integer(string='Variants Synced', default=0)
    progress = fields.Float(
        string='Progress',
        compute='_compute_progress',
        store=True,
    )

    # Variant breakdown (for display)
    color_count = fields.Integer(string='Colors', default=0)
    size_count = fields.Integer(string='Sizes', default=0)
    variant_summary = fields.Char(
        string='Variants',
        compute='_compute_variant_summary',
    )

    # Timing
    started_at = fields.Datetime(string='Started At')
    completed_at = fields.Datetime(string='Completed At')

    # Error tracking
    error_message = fields.Text(string='Error Details')

    # Kanban color
    color = fields.Integer(string='Color Index', compute='_compute_color')

    @api.depends('variant_count', 'variants_synced')
    def _compute_progress(self):
        for record in self:
            if record.variant_count:
                record.progress = (record.variants_synced / record.variant_count) * 100
            else:
                record.progress = 0

    @api.depends('color_count', 'size_count', 'variant_count')
    def _compute_variant_summary(self):
        for record in self:
            parts = []
            if record.color_count:
                parts.append(_('%d colors') % record.color_count)
            if record.size_count:
                parts.append(_('%d sizes') % record.size_count)
            if parts:
                record.variant_summary = ' × '.join(parts) + f' = {record.variant_count} variants'
            else:
                record.variant_summary = _('%d variants') % record.variant_count

    @api.depends('state')
    def _compute_color(self):
        color_map = {
            'pending': 0,      # Gray
            'in_progress': 4,  # Blue
            'done': 10,        # Green
            'error': 1,        # Red
        }
        for record in self:
            record.color = color_map.get(record.state, 0)

    def action_sync_product(self):
        """Sync this product from Printful."""
        self.ensure_one()
        self.write({
            'state': 'in_progress',
            'started_at': fields.Datetime.now(),
            'error_message': False,
        })

        config = self.queue_id.printful_config_id
        try:
            # Delegate to the refactored sync method
            product_template = config._sync_single_product(
                self.printful_product_id,
                progress_callback=self._update_progress,
            )
            self.write({
                'state': 'done',
                'completed_at': fields.Datetime.now(),
                'product_template_id': product_template.id if product_template else False,
            })
        except Exception as e:
            _logger.exception("Error syncing product %s", self.name)
            self.write({
                'state': 'error',
                'error_message': str(e),
                'completed_at': fields.Datetime.now(),
            })
            raise

    def _update_progress(self, variants_synced, variant_count, colors=0, sizes=0):
        """
        Callback to update sync progress.

        This method is resilient to failures - progress updates are non-critical
        and should never cause the main sync operation to fail.

        Note: Progress updates are committed at the end of the transaction.
        For real-time UI updates, consider using websocket notifications
        or implement this with a separate cursor:

        with self.pool.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.uid, self.env.context)
            item = new_env[self._name].browse(self.id)
            item.write({...})
            new_cr.commit()
        """
        try:
            self.write({
                'variants_synced': variants_synced,
                'variant_count': variant_count,
                'color_count': colors,
                'size_count': sizes,
            })
        except Exception as e:
            # Progress updates are non-critical - log and continue sync
            _logger.warning(
                "Failed to update sync progress for item %s: %s. "
                "Sync will continue.",
                self.name, str(e)
            )

    def action_retry(self):
        """Retry syncing this product."""
        self.ensure_one()
        if self.state != 'error':
            raise UserError(_('Can only retry failed items.'))
        self.write({
            'state': 'pending',
            'error_message': False,
            'variants_synced': 0,
        })
        return self.action_sync_product()

    def action_view_product(self):
        """Open the linked Odoo product."""
        self.ensure_one()
        if not self.product_template_id:
            raise UserError(_('No product linked yet. Sync first.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': self.product_template_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
