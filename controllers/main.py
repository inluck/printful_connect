# -*- coding: utf-8 -*-
import json
import logging

from odoo import fields, http, _
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.exceptions import AccessError, MissingError

_logger = logging.getLogger(__name__)


class PrintfulCustomerPortal(CustomerPortal):
    """
    Extend the customer portal to show Printful fulfillment tracking.

    This provides customers with:
    - Order tracking status (pending → in production → shipped → delivered)
    - Carrier and tracking number with clickable link
    - Estimated delivery dates
    - Full order timeline
    """

    def _prepare_home_portal_values(self, counters):
        """Add Printful-specific counters to portal home if needed."""
        values = super()._prepare_home_portal_values(counters)
        return values

    @http.route(
        ['/my/orders/<int:order_id>/tracking'],
        type='http',
        auth='public',
        website=True,
    )
    def portal_order_tracking(self, order_id, access_token=None, **kw):
        """
        Dedicated tracking page for Printful orders.

        Accessible via:
        - Authenticated portal users (their own orders)
        - Public access with valid access_token (from email links)

        Args:
            order_id: The sale.order ID
            access_token: Optional token for unauthenticated access

        Returns:
            Rendered tracking page or error
        """
        try:
            order_sudo = self._document_check_access(
                'sale.order', order_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.redirect('/my')

        # Build tracking timeline based on fulfillment status
        timeline = self._build_fulfillment_timeline(order_sudo)

        values = {
            'order': order_sudo,
            'timeline': timeline,
            'page_name': 'order_tracking',
            'access_token': access_token,
        }

        return request.render(
            'printful_connect.portal_order_tracking',
            values
        )

    def _build_fulfillment_timeline(self, order):
        """
        Build a timeline of fulfillment events for the order.

        Args:
            order: sale.order record

        Returns:
            List of timeline event dicts with status, label, date, and active flag
        """
        status = order.printful_fulfillment_status or 'pending'

        # Define the standard fulfillment flow
        steps = [
            ('pending', _('Order Received'), order.date_order),
            ('in_production', _('In Production'), None),
            ('shipped', _('Shipped'), order.printful_shipped_at),
            ('delivered', _('Delivered'), None),
        ]

        # Map status to step index
        status_order = ['pending', 'in_production', 'shipped', 'delivered']

        # Handle special statuses
        if status in ('canceled', 'failed', 'returned'):
            # Add special status at the end
            steps.append((status, order._get_fulfillment_status_display(), None))
            current_index = len(steps) - 1
        else:
            current_index = status_order.index(status) if status in status_order else 0

        timeline = []
        for i, (step_status, label, date) in enumerate(steps):
            timeline.append({
                'status': step_status,
                'label': label,
                'date': date,
                'completed': i < current_index,
                'active': i == current_index,
                'future': i > current_index,
            })

        return timeline


class WebsiteSalePrintful(WebsiteSale):
    """
    Extension of WebsiteSale controller for Printful integration.

    Note: The size guide is rendered via the QWeb template (s_size_guide)
    which has direct access to product.printful_sizeguide field.
    No controller override is needed for basic functionality.

    This class is kept for potential future enhancements like:
    - Custom size guide rendering
    - Printful-specific product page modifications
    - Stock availability checks against Printful API
    """
    pass


class PrintfulWebhookController(http.Controller):
    """
    Controller for handling incoming Printful webhooks.

    Printful sends webhook notifications for various events:
    - Order status changes (created, updated, failed, canceled)
    - Shipping updates (shipped, returned)
    - Product changes (synced, updated, deleted)
    - Stock updates

    All webhooks are validated using HMAC signature verification
    when a webhook secret is configured.
    """

    @http.route(
        '/printful/webhook',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def handle_webhook_default(self, **kwargs):
        """
        Handle incoming webhook without specific config ID.
        Attempts to match based on payload content.
        """
        return self._process_webhook(config_id=None)

    @http.route(
        '/printful/webhook/<int:config_id>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def handle_webhook(self, config_id, **kwargs):
        """
        Handle incoming webhook for a specific Printful configuration.

        Args:
            config_id: ID of the printful.printful configuration

        Returns:
            JSON response with status
        """
        return self._process_webhook(config_id)

    def _process_webhook(self, config_id):
        """
        Process an incoming webhook request.

        Args:
            config_id: Printful configuration ID (or None for auto-detect)

        Returns:
            HTTP response
        """
        try:
            # Get raw request data
            raw_data = request.httprequest.get_data()
            signature = request.httprequest.headers.get('X-Printful-Signature', '')

            # Parse JSON payload
            try:
                payload = json.loads(raw_data.decode('utf-8'))
            except json.JSONDecodeError as e:
                _logger.error("Invalid JSON in webhook payload: %s", str(e))
                return request.make_json_response(
                    {'error': 'Invalid JSON payload'},
                    status=400
                )

            # Find webhook configuration
            Webhook = request.env['printful.webhook'].sudo()
            webhook = None

            if config_id:
                webhook = Webhook.search([
                    ('printful_config_id', '=', config_id),
                    ('active', '=', True),
                ], limit=1)
            else:
                # Try to find any active webhook
                webhook = Webhook.search([('active', '=', True)], limit=1)

            if not webhook:
                _logger.warning(
                    "No active webhook configuration found for config_id=%s",
                    config_id
                )
                # Still return 200 to prevent Printful from retrying
                return request.make_json_response({'status': 'no_config'})

            # Validate signature
            if not webhook.validate_signature(raw_data, signature):
                _logger.warning(
                    "Invalid webhook signature for config_id=%s",
                    config_id
                )
                return request.make_json_response(
                    {'error': 'Invalid signature'},
                    status=401
                )

            # Extract event type
            event_type = payload.get('type', 'unknown')

            # Check if we're subscribed to this event type
            subscribed_events = webhook.get_subscribed_events()
            if event_type not in subscribed_events and event_type != 'unknown':
                _logger.info(
                    "Ignoring unsubscribed event type: %s",
                    event_type
                )
                return request.make_json_response({
                    'status': 'ignored',
                    'reason': 'not_subscribed',
                })

            # Create event record
            WebhookEvent = request.env['printful.webhook.event'].sudo()
            event = WebhookEvent.create({
                'name': payload.get('id', f'event_{event_type}'),
                'webhook_id': webhook.id,
                'event_type': event_type if event_type in dict(WebhookEvent._fields['event_type'].selection) else 'unknown',
                'payload': json.dumps(payload, indent=2),
                'state': 'pending',
            })

            # Update webhook last received timestamp
            webhook.write({'last_received_at': fields.Datetime.now()})

            # Process event immediately (or defer to cron for heavy processing)
            try:
                event._process_event()
            except Exception as e:
                _logger.exception("Error processing webhook event: %s", str(e))
                # Event will be marked as failed, can be retried later

            _logger.info(
                "Webhook event processed: type=%s, id=%s, state=%s",
                event_type, event.id, event.state
            )

            return request.make_json_response({
                'status': 'received',
                'event_id': event.id,
            })

        except Exception as e:
            _logger.exception("Unexpected error processing webhook")
            return request.make_json_response(
                {'error': 'Internal server error'},
                status=500
            )

    @http.route(
        '/printful/shipping/rates',
        type='json',
        auth='public',
        methods=['POST'],
    )
    def get_shipping_rates(self, **kwargs):
        """
        Get shipping rates for a cart.
        Used by frontend to display shipping options at checkout.

        Expected payload:
        {
            "country_code": "US",
            "state_code": "CA",
            "zip": "90210",
            "items": [
                {"variant_id": "12345", "quantity": 2}
            ]
        }

        Returns:
            List of available shipping options with rates
        """
        try:
            data = request.jsonrequest or {}

            country_code = data.get('country_code')
            state_code = data.get('state_code')
            zip_code = data.get('zip')
            items = data.get('items', [])

            if not country_code:
                return {'error': 'country_code is required'}

            if not items:
                return {'error': 'items are required'}

            # Get Printful configuration
            PrintfulConfig = request.env['printful.printful'].sudo()
            config = PrintfulConfig.search([], limit=1)

            if not config:
                return {'error': 'Printful not configured'}

            # Calculate rates
            rates = config._get_shipping_rates_v2(
                country_code=country_code,
                state_code=state_code,
                zip_code=zip_code,
                items=items,
            )

            return {'rates': rates}

        except Exception as e:
            _logger.exception("Error getting shipping rates")
            return {'error': str(e)}

    @http.route(
        '/printful/webhook/test',
        type='http',
        auth='user',
        methods=['GET'],
    )
    def test_webhook_endpoint(self, **kwargs):
        """
        Test endpoint to verify webhook URL is accessible.
        Only available to authenticated users.
        """
        return request.make_json_response({
            'status': 'ok',
            'message': 'Webhook endpoint is active',
        })
