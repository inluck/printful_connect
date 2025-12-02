# -*- coding: utf-8 -*-
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from markupsafe import Markup, escape as html_escape

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class PrintfulWebhook(models.Model):
    """
    Configuration model for Printful webhooks.
    Stores webhook secret for signature validation and tracks webhook status.
    """
    _name = 'printful.webhook'
    _description = 'Printful Webhook Configuration'
    _order = 'create_date desc'

    name = fields.Char(
        string="Name",
        required=True,
        default="Printful Webhook",
    )
    active = fields.Boolean(string="Active", default=True)

    # Configuration
    printful_config_id = fields.Many2one(
        comodel_name='printful.printful',
        string="Printful Configuration",
        required=True,
        ondelete='cascade',
    )

    # Webhook secret for HMAC signature validation
    webhook_secret = fields.Char(
        string="Webhook Secret",
        help="Secret key for validating webhook signatures. "
             "This is provided by Printful when you create a webhook.",
    )

    # Webhook URL (computed based on Odoo base URL)
    webhook_url = fields.Char(
        string="Webhook URL",
        compute='_compute_webhook_url',
        help="URL to configure in Printful dashboard",
    )

    # Printful webhook ID (if registered via API)
    printful_webhook_id = fields.Char(
        string="Printful Webhook ID",
        help="Webhook ID returned by Printful API",
    )

    # Event subscriptions
    event_order_created = fields.Boolean(string="Order Created", default=True)
    event_order_updated = fields.Boolean(string="Order Updated", default=True)
    event_order_failed = fields.Boolean(string="Order Failed", default=True)
    event_order_canceled = fields.Boolean(string="Order Canceled", default=True)
    event_package_shipped = fields.Boolean(string="Package Shipped", default=True)
    event_package_returned = fields.Boolean(string="Package Returned", default=True)
    event_product_synced = fields.Boolean(string="Product Synced", default=False)
    event_product_updated = fields.Boolean(string="Product Updated", default=False)
    event_product_deleted = fields.Boolean(string="Product Deleted", default=False)
    event_stock_updated = fields.Boolean(string="Stock Updated", default=True)

    # Statistics
    last_received_at = fields.Datetime(string="Last Event Received")
    events_received_count = fields.Integer(
        string="Events Received",
        compute='_compute_events_count',
    )
    events_failed_count = fields.Integer(
        string="Failed Events",
        compute='_compute_events_count',
    )

    @api.depends('printful_config_id')
    def _compute_webhook_url(self):
        """Compute the webhook URL based on Odoo base URL."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            if record.printful_config_id:
                record.webhook_url = f"{base_url}/printful/webhook/{record.printful_config_id.id}"
            else:
                record.webhook_url = f"{base_url}/printful/webhook"

    def _compute_events_count(self):
        """Compute event statistics."""
        WebhookEvent = self.env['printful.webhook.event']
        for record in self:
            record.events_received_count = WebhookEvent.search_count([
                ('webhook_id', '=', record.id)
            ])
            record.events_failed_count = WebhookEvent.search_count([
                ('webhook_id', '=', record.id),
                ('state', '=', 'failed')
            ])

    def validate_signature(self, payload, signature):
        """
        Validate the HMAC signature of an incoming webhook.

        Args:
            payload: Raw request body (bytes)
            signature: X-Printful-Signature header value

        Returns:
            True if signature is valid, False otherwise

        Security Note:
            This method requires a webhook secret to be configured.
            Webhooks without secrets will be rejected to prevent
            unauthorized access to order/product manipulation endpoints.
        """
        self.ensure_one()

        if not self.webhook_secret:
            _logger.error(
                "SECURITY: Webhook %s has no secret configured. "
                "Rejecting request. Please configure a webhook secret in Printful "
                "dashboard and update this webhook configuration.",
                self.name
            )
            return False  # Reject webhooks without secret for security

        if not signature:
            _logger.warning(
                "SECURITY: No signature provided for webhook %s. Rejecting request.",
                self.name
            )
            return False

        # Calculate expected signature
        expected = hmac.new(
            self.webhook_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

        # Compare signatures (timing-safe comparison)
        is_valid = hmac.compare_digest(expected, signature)

        if not is_valid:
            _logger.warning(
                "SECURITY: Invalid signature for webhook %s. "
                "Expected: %s..., Got: %s...",
                self.name, expected[:8], signature[:8] if signature else 'None'
            )

        return is_valid

    def get_subscribed_events(self):
        """
        Get list of event types this webhook is subscribed to.

        Returns:
            List of Printful event type strings
        """
        self.ensure_one()
        events = []

        event_mapping = {
            'event_order_created': 'order_created',
            'event_order_updated': 'order_updated',
            'event_order_failed': 'order_failed',
            'event_order_canceled': 'order_canceled',
            'event_package_shipped': 'package_shipped',
            'event_package_returned': 'package_returned',
            'event_product_synced': 'product_synced',
            'event_product_updated': 'product_updated',
            'event_product_deleted': 'product_deleted',
            'event_stock_updated': 'stock_updated',
        }

        for field, event_type in event_mapping.items():
            if getattr(self, field, False):
                events.append(event_type)

        return events

    def action_test_webhook(self):
        """Action to test webhook connectivity."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhook URL'),
                'message': _('Configure this URL in your Printful dashboard: %s') % self.webhook_url,
                'type': 'info',
                'sticky': False,
            }
        }


class PrintfulWebhookEvent(models.Model):
    """
    Log of received webhook events for auditing and debugging.
    """
    _name = 'printful.webhook.event'
    _description = 'Printful Webhook Event Log'
    _order = 'create_date desc'

    name = fields.Char(
        string="Event ID",
        help="Unique event identifier from Printful",
    )
    webhook_id = fields.Many2one(
        comodel_name='printful.webhook',
        string="Webhook",
        required=True,
        ondelete='cascade',
    )
    printful_config_id = fields.Many2one(
        comodel_name='printful.printful',
        string="Printful Configuration",
        related='webhook_id.printful_config_id',
        store=True,
    )

    # Event details
    event_type = fields.Selection([
        ('order_created', 'Order Created'),
        ('order_updated', 'Order Updated'),
        ('order_failed', 'Order Failed'),
        ('order_canceled', 'Order Canceled'),
        ('package_shipped', 'Package Shipped'),
        ('package_returned', 'Package Returned'),
        ('product_synced', 'Product Synced'),
        ('product_updated', 'Product Updated'),
        ('product_deleted', 'Product Deleted'),
        ('stock_updated', 'Stock Updated'),
        ('unknown', 'Unknown'),
    ], string="Event Type", required=True, index=True)

    payload = fields.Text(
        string="Payload",
        help="Raw JSON payload received from Printful",
    )

    # Processing state
    state = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('done', 'Processed'),
        ('failed', 'Failed'),
        ('ignored', 'Ignored'),
    ], string="State", default='pending', required=True, index=True)

    error_message = fields.Text(string="Error Message")
    processed_at = fields.Datetime(string="Processed At")
    retry_count = fields.Integer(string="Retry Count", default=0)

    # Related records (filled after processing)
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Related Order",
        ondelete='set null',
    )
    product_template_id = fields.Many2one(
        comodel_name='product.template',
        string="Related Product",
        ondelete='set null',
    )

    def action_retry(self):
        """Retry processing a failed event."""
        for record in self.filtered(lambda r: r.state == 'failed'):
            record.write({
                'state': 'pending',
                'error_message': False,
                'retry_count': record.retry_count + 1,
            })
            record._process_event()

    def action_ignore(self):
        """Mark event as ignored."""
        self.write({'state': 'ignored'})

    def _process_event(self):
        """
        Process this webhook event.
        Dispatches to appropriate handler based on event type.
        """
        self.ensure_one()

        if self.state != 'pending':
            return

        self.write({'state': 'processing'})

        try:
            payload = json.loads(self.payload) if self.payload else {}

            handler_method = f'_handle_{self.event_type}'
            if hasattr(self, handler_method):
                getattr(self, handler_method)(payload)
            else:
                _logger.warning("No handler for event type: %s", self.event_type)

            self.write({
                'state': 'done',
                'processed_at': fields.Datetime.now(),
            })

        except Exception as e:
            _logger.exception("Failed to process webhook event %s", self.id)
            self.write({
                'state': 'failed',
                'error_message': str(e),
            })

    def _handle_order_created(self, payload):
        """Handle order_created event - log for reference."""
        order_data = payload.get('data', {}).get('order', {})
        external_id = order_data.get('external_id', '')

        if external_id:
            sale_order = self.env['sale.order'].search([
                ('order_external_ref', '=', external_id)
            ], limit=1)
            if sale_order:
                self.sale_order_id = sale_order.id
                _logger.info(
                    "Order created event for Odoo order %s (Printful: %s)",
                    sale_order.name, order_data.get('id')
                )

    def _handle_order_updated(self, payload):
        """Handle order_updated event - update order status."""
        order_data = payload.get('data', {}).get('order', {})
        self._update_sale_order_from_payload(order_data)

    def _handle_order_failed(self, payload):
        """Handle order_failed event - log failure and notify customer."""
        order_data = payload.get('data', {}).get('order', {})
        sale_order = self._find_sale_order(order_data)

        if sale_order:
            self.sale_order_id = sale_order.id
            # Update fulfillment status
            sale_order.write({'printful_fulfillment_status': 'failed'})
            # Add internal note about failure
            failure_reason = order_data.get('error_message', 'Unknown reason')
            sale_order.message_post(
                body=_("❌ Printful order failed: %s") % failure_reason,
                message_type='notification',
            )
            _logger.warning(
                "Printful order failed for %s: %s",
                sale_order.name, failure_reason
            )
            # Send customer notification
            self._send_customer_email(sale_order, 'printful_connect.mail_template_printful_issue')

    def _handle_order_canceled(self, payload):
        """Handle order_canceled event and notify customer."""
        order_data = payload.get('data', {}).get('order', {})
        sale_order = self._find_sale_order(order_data)

        if sale_order:
            self.sale_order_id = sale_order.id
            sale_order.write({'printful_fulfillment_status': 'canceled'})
            sale_order.message_post(
                body=_("🚫 Printful order has been canceled."),
                message_type='notification',
            )
            _logger.info("Order canceled event for %s", sale_order.name)
            # Send customer notification
            self._send_customer_email(sale_order, 'printful_connect.mail_template_printful_issue')

    def _handle_package_shipped(self, payload):
        """Handle package_shipped event - update tracking info."""
        order_data = payload.get('data', {}).get('order', {})
        shipment_data = payload.get('data', {}).get('shipment', {})

        sale_order = self._find_sale_order(order_data)

        if sale_order:
            self.sale_order_id = sale_order.id

            # Extract tracking information
            tracking_number = shipment_data.get('tracking_number', '')
            tracking_url = shipment_data.get('tracking_url', '')
            carrier = shipment_data.get('carrier', '')
            ship_date = shipment_data.get('ship_date', '')
            estimated_delivery = shipment_data.get('estimated_delivery', '')

            # Parse ship date if provided
            shipped_at = False
            if ship_date:
                try:
                    # Try ISO format first
                    if 'T' in ship_date:
                        shipped_at = datetime.fromisoformat(ship_date.replace('Z', '+00:00'))
                    else:
                        shipped_at = datetime.strptime(ship_date, '%Y-%m-%d')
                except (ValueError, TypeError):
                    _logger.warning("Could not parse ship date: %s", ship_date)

            # Update sale order with tracking info
            update_vals = {
                'order_shipping': carrier,
                'printful_tracking_carrier': carrier,
                'printful_tracking_number': tracking_number,
                'printful_tracking_url': tracking_url,
                'printful_estimated_delivery': estimated_delivery,
                'printful_fulfillment_status': 'shipped',
            }
            if shipped_at:
                update_vals['printful_shipped_at'] = shipped_at

            # Build tracking message with XSS protection
            # All external data must be escaped before embedding in HTML
            tracking_msg = Markup("<strong>📦 Package Shipped!</strong><br/>")
            if carrier:
                tracking_msg += Markup("Carrier: %s<br/>") % html_escape(carrier)
            if tracking_number:
                tracking_msg += Markup("Tracking Number: %s<br/>") % html_escape(tracking_number)
            if tracking_url:
                # Validate URL scheme to prevent javascript: URLs
                safe_url = tracking_url if tracking_url.startswith(('http://', 'https://')) else '#'
                if safe_url == '#':
                    _logger.warning(
                        "SECURITY: Rejected potentially malicious tracking URL: %s",
                        tracking_url[:50]
                    )
                tracking_msg += Markup('<a href="%s" target="_blank" rel="noopener noreferrer">🔗 Track Package</a><br/>') % html_escape(safe_url)
            if estimated_delivery:
                tracking_msg += Markup("Estimated Delivery: %s") % html_escape(estimated_delivery)

            sale_order.write(update_vals)
            sale_order.message_post(
                body=tracking_msg,
                message_type='notification',
            )

            _logger.info(
                "Package shipped for order %s - Tracking: %s",
                sale_order.name, tracking_number
            )
            # Send customer notification with tracking info
            self._send_customer_email(sale_order, 'printful_connect.mail_template_printful_shipped')

    def _handle_package_returned(self, payload):
        """Handle package_returned event and notify customer."""
        order_data = payload.get('data', {}).get('order', {})
        sale_order = self._find_sale_order(order_data)

        if sale_order:
            self.sale_order_id = sale_order.id
            sale_order.write({'printful_fulfillment_status': 'returned'})
            return_reason = payload.get('data', {}).get('reason', 'Unknown reason')
            sale_order.message_post(
                body=_("↩️ Package returned: %s") % return_reason,
                message_type='notification',
            )
            _logger.info("Package returned for order %s: %s", sale_order.name, return_reason)
            # Send customer notification
            self._send_customer_email(sale_order, 'printful_connect.mail_template_printful_returned')

    def _handle_product_synced(self, payload):
        """Handle product_synced event."""
        product_data = payload.get('data', {}).get('sync_product', {})
        printful_id = str(product_data.get('id', ''))

        if printful_id:
            product = self.env['product.template'].search([
                ('printful_ref', '=', printful_id)
            ], limit=1)
            if product:
                self.product_template_id = product.id
                _logger.info("Product synced event for %s", product.name)

    def _handle_product_updated(self, payload):
        """Handle product_updated event - trigger resync."""
        product_data = payload.get('data', {}).get('sync_product', {})
        printful_id = str(product_data.get('id', ''))

        if printful_id:
            product = self.env['product.template'].search([
                ('printful_ref', '=', printful_id)
            ], limit=1)
            if product:
                self.product_template_id = product.id
                _logger.info(
                    "Product updated in Printful: %s. Consider resyncing.",
                    product.name
                )

    def _handle_product_deleted(self, payload):
        """Handle product_deleted event - mark product as inactive."""
        product_data = payload.get('data', {}).get('sync_product', {})
        printful_id = str(product_data.get('id', ''))

        if printful_id:
            product = self.env['product.template'].search([
                ('printful_ref', '=', printful_id)
            ], limit=1)
            if product:
                self.product_template_id = product.id
                # Mark as inactive rather than deleting
                product.write({'active': False})
                _logger.info("Product deleted in Printful, deactivated: %s", product.name)

    def _handle_stock_updated(self, payload):
        """Handle stock_updated event - update product stock status."""
        variant_data = payload.get('data', {}).get('sync_variant', {})
        variant_id = str(variant_data.get('id', ''))
        in_stock = variant_data.get('availability_status', '') == 'active'

        if variant_id:
            variant = self.env['product.product'].search([
                ('printful_variant_external_ref', '=', variant_id)
            ], limit=1)
            if variant:
                variant.write({'printful_product_in_stock': in_stock})
                _logger.info(
                    "Stock updated for variant %s: %s",
                    variant.display_name, 'In Stock' if in_stock else 'Out of Stock'
                )

    def _find_sale_order(self, order_data):
        """
        Find sale order by external_id or Printful order ID.

        Args:
            order_data: Order data from webhook payload

        Returns:
            sale.order record or None
        """
        external_id = order_data.get('external_id', '')
        printful_id = str(order_data.get('id', ''))

        # Try external ID first (our reference)
        if external_id:
            order = self.env['sale.order'].search([
                ('order_external_ref', '=', external_id)
            ], limit=1)
            if order:
                return order

        # Try Printful order ID
        if printful_id:
            order_ref = f"#PF{printful_id}"
            order = self.env['sale.order'].search([
                ('order_ref', '=', order_ref)
            ], limit=1)
            if order:
                return order

        return None

    def _send_customer_email(self, sale_order, template_xmlid):
        """
        Send a customer notification email using the specified template.

        This method is the heart of the webhook-driven email state machine.
        Each state transition triggers the appropriate customer-friendly email
        with portal links for tracking.

        Args:
            sale_order: The sale.order record to send email about
            template_xmlid: The XML ID of the mail.template to use

        Note:
            - Emails are sent asynchronously to avoid blocking webhook processing
            - Template must exist or sending is silently skipped with warning
            - Customer's email address must be set on partner
        """
        if not sale_order.partner_id.email:
            _logger.warning(
                "Cannot send customer email for order %s: no customer email",
                sale_order.name
            )
            return

        try:
            template = self.env.ref(template_xmlid, raise_if_not_found=False)
            if not template:
                _logger.warning(
                    "Email template %s not found, skipping customer notification",
                    template_xmlid
                )
                return

            # Send email with force_send=False to use mail queue
            # This prevents webhook timeouts on slow mail servers
            template.send_mail(
                sale_order.id,
                force_send=False,
                raise_exception=False,
            )
            _logger.info(
                "Queued customer email '%s' for order %s to %s",
                template.name, sale_order.name, sale_order.partner_id.email
            )
        except Exception as e:
            # Log but don't fail - customer email is not critical path
            _logger.exception(
                "Failed to send customer email for order %s: %s",
                sale_order.name, str(e)
            )

    def _update_sale_order_from_payload(self, order_data):
        """
        Update sale order with data from webhook payload.

        This method also detects status transitions and triggers
        appropriate customer email notifications as part of the
        webhook-driven state machine.
        """
        sale_order = self._find_sale_order(order_data)

        if not sale_order:
            _logger.warning(
                "Could not find sale order for webhook event. "
                "External ID: %s, Printful ID: %s",
                order_data.get('external_id'), order_data.get('id')
            )
            return

        self.sale_order_id = sale_order.id

        # Update costs if provided
        costs = order_data.get('costs', {})
        if costs:
            sale_order.write({
                'order_subtotal': costs.get('subtotal', sale_order.order_subtotal),
                'order_discount': costs.get('discount', sale_order.order_discount),
                'shipping': costs.get('shipping', sale_order.shipping),
                'order_tax': costs.get('tax', sale_order.order_tax),
                'order_total': costs.get('total', sale_order.order_total),
            })

        # Map Printful status to our fulfillment status
        status = order_data.get('status', '')
        if status:
            old_status = sale_order.printful_fulfillment_status
            new_status = self._map_printful_status(status)

            # Update status and post internal message
            if new_status and new_status != old_status:
                sale_order.write({'printful_fulfillment_status': new_status})
                sale_order.message_post(
                    body=_("Printful order status updated: %s → %s") % (
                        old_status or 'none', new_status
                    ),
                    message_type='notification',
                )

                # Trigger customer email based on status transition
                self._notify_customer_on_status_change(sale_order, old_status, new_status)
            else:
                # Status didn't change, just log it
                sale_order.message_post(
                    body=_("Printful order status: %s") % status,
                    message_type='notification',
                )

    def _map_printful_status(self, printful_status):
        """
        Map Printful API status to our fulfillment status selection.

        Args:
            printful_status: Status string from Printful API

        Returns:
            Our internal fulfillment status or None if no mapping
        """
        # Printful status values from API
        status_mapping = {
            'draft': 'pending',
            'pending': 'pending',
            'failed': 'failed',
            'canceled': 'canceled',
            'inprocess': 'in_production',
            'in_process': 'in_production',
            'onhold': 'pending',
            'on_hold': 'pending',
            'partial': 'in_production',
            'fulfilled': 'shipped',
            'shipped': 'shipped',
            'delivered': 'delivered',
            'returned': 'returned',
        }
        return status_mapping.get(printful_status.lower().replace('-', '_'))

    def _notify_customer_on_status_change(self, sale_order, old_status, new_status):
        """
        Send customer email notification based on status transition.

        This is the state machine dispatcher - each transition maps
        to a specific customer-friendly email template.

        Args:
            sale_order: The sale.order record
            old_status: Previous fulfillment status
            new_status: New fulfillment status
        """
        # Define which transitions trigger emails
        # (from_status, to_status) -> template_xmlid
        transition_templates = {
            # Enter production - exciting update!
            ('pending', 'in_production'): 'printful_connect.mail_template_printful_in_production',
            # Shipped - most important email with tracking
            ('in_production', 'shipped'): 'printful_connect.mail_template_printful_shipped',
            ('pending', 'shipped'): 'printful_connect.mail_template_printful_shipped',
            # Delivered - order complete
            ('shipped', 'delivered'): 'printful_connect.mail_template_printful_delivered',
            # Problem states
            ('pending', 'failed'): 'printful_connect.mail_template_printful_issue',
            ('in_production', 'failed'): 'printful_connect.mail_template_printful_issue',
            ('pending', 'canceled'): 'printful_connect.mail_template_printful_issue',
            ('in_production', 'canceled'): 'printful_connect.mail_template_printful_issue',
            # Returned
            ('shipped', 'returned'): 'printful_connect.mail_template_printful_returned',
            ('delivered', 'returned'): 'printful_connect.mail_template_printful_returned',
        }

        # Look up template for this transition
        template_xmlid = transition_templates.get((old_status, new_status))

        if template_xmlid:
            _logger.info(
                "Status transition %s → %s triggers email for order %s",
                old_status, new_status, sale_order.name
            )
            self._send_customer_email(sale_order, template_xmlid)
        else:
            _logger.debug(
                "No email template for transition %s → %s",
                old_status, new_status
            )

    @api.model
    def process_pending_events(self):
        """
        Cron job to process pending webhook events.
        Called periodically to handle any events that weren't processed immediately.
        """
        pending = self.search([
            ('state', '=', 'pending'),
            ('retry_count', '<', 3),  # Max 3 retries
        ], limit=50)

        for event in pending:
            try:
                event._process_event()
            except Exception as e:
                _logger.exception("Error processing event %s", event.id)
                event.write({
                    'state': 'failed',
                    'error_message': str(e),
                })

        # Cleanup old processed events (keep 30 days)
        cutoff = fields.Datetime.now() - timedelta(days=30)
        old_events = self.search([
            ('state', 'in', ['done', 'ignored']),
            ('create_date', '<', cutoff),
        ])
        if old_events:
            _logger.info("Cleaning up %d old webhook events", len(old_events))
            old_events.unlink()
