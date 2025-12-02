# -*- coding: utf-8 -*-
import json
import logging

from odoo import fields, http, _
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)


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
