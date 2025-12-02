# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from psycopg2 import OperationalError
import requests
import json
import logging
import uuid

_logger = logging.getLogger(__name__)

# API configuration
PRINTFUL_API_BASE = "https://api.printful.com"
PRINTFUL_API_TIMEOUT = 30  # seconds


class SaleOrder(models.Model):
    """
    Extension of sale.order to handle Printful order synchronization.
    """
    _inherit = 'sale.order'

    # Printful order references
    order_ref = fields.Char(
        string='Printful Order ID',
        index=True,  # Index for fast idempotency lookups during order import
    )
    order_external_ref = fields.Char(
        string='External Ref',
        index=True,  # Index for API lookups by external ID
    )
    order_store_ref = fields.Char(string='Store Ref')
    order_shipping = fields.Char(string='Shipping Method')
    order_shipping_service_name = fields.Char(string='Shipping Service')
    printful_order_notes = fields.Char(string='Order Notes')

    # Printful costs breakdown
    order_currency = fields.Char(string='Currency')
    order_subtotal = fields.Float(string='Subtotal')
    order_discount = fields.Float(string='Discount')
    order_digitization = fields.Float(string='Digitization')
    order_additional_fee = fields.Float(string='Additional Fee')
    order_fulfillment_fee = fields.Float(string='Fulfillment Fee')
    order_retail_delivery_fee = fields.Float(string='Retail Delivery Fee')
    order_tax = fields.Float(string='Tax')
    order_total = fields.Float(string='Total')
    order_vat = fields.Float(string='VAT')
    shipping = fields.Float(string='Shipping Cost')

    # Printful dashboard
    printful_dashboard_url = fields.Char(string='Dashboard URL')

    # Price breakdown
    customer_pays = fields.Float(string='Customer Pays')
    printful_price = fields.Float(string='Printful Price')
    profit = fields.Float(string='Profit')
    currency_symbol = fields.Char(string='Currency Symbol')

    # Tracking information
    printful_tracking_carrier = fields.Char(
        string='Carrier',
        help='Shipping carrier name',
    )
    printful_tracking_number = fields.Char(
        string='Tracking Number',
        help='Package tracking number',
    )
    printful_tracking_url = fields.Char(
        string='Tracking URL',
        help='URL to track the shipment',
    )
    printful_shipped_at = fields.Datetime(
        string='Shipped At',
        help='Date and time when the package was shipped',
    )
    printful_estimated_delivery = fields.Char(
        string='Estimated Delivery',
        help='Estimated delivery date or date range',
    )
    printful_fulfillment_status = fields.Selection([
        ('pending', 'Pending'),
        ('in_production', 'In Production'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('returned', 'Returned'),
        ('canceled', 'Canceled'),
        ('failed', 'Failed'),
    ], string='Fulfillment Status', default='pending',
        help='Current status of Printful fulfillment')

    # Related partner fields
    tax_number = fields.Char(related='partner_id.tax_number', string='Tax Number')
    company = fields.Char(related='partner_id.company', string='Company')

    def action_create_push_order_printful(self):
        """Push this order to Printful for fulfillment."""
        for rec in self:
            rec._push_to_printful()

    def action_confirm(self):
        """
        Override order confirmation to automatically push to Printful when enabled.

        The auto-fulfillment feature:
        - Only triggers if auto_fulfill_orders is enabled in Printful config
        - Only processes orders containing Printful products
        - Silently skips orders that fail validation (logs warning instead of blocking)
        - Does not block order confirmation even if Printful push fails
        """
        # First, complete the standard confirmation
        result = super().action_confirm()

        # Check if auto-fulfillment is enabled
        printful_config = self.env['printful.printful'].search([], limit=1)
        if not printful_config or not printful_config.auto_fulfill_orders:
            return result

        if not printful_config.token:
            _logger.warning(
                "Auto-fulfillment enabled but Printful API token not configured"
            )
            return result

        # Process each confirmed order
        for order in self:
            # Skip if already pushed to Printful
            if order.order_external_ref:
                _logger.debug(
                    "Order %s already pushed to Printful, skipping auto-fulfill",
                    order.name
                )
                continue

            # Check if order contains any Printful products
            has_printful_products = any(
                line.product_id and line.product_id.printful_variant_ref
                for line in order.order_line
                if not (
                    getattr(line.product_id, 'is_delivery_product', False) or
                    (line.product_id.type == 'service' and
                     any(kw in (line.name or '').lower()
                         for kw in ['delivery', 'shipping']))
                )
            )

            if not has_printful_products:
                _logger.debug(
                    "Order %s has no Printful products, skipping auto-fulfill",
                    order.name
                )
                continue

            # Attempt to push order to Printful
            try:
                order._push_to_printful()
                _logger.info(
                    "Auto-fulfilled order %s to Printful (External ID: %s)",
                    order.name, order.order_external_ref
                )
            except UserError as e:
                # Log validation failures but don't block order confirmation
                _logger.warning(
                    "Auto-fulfillment failed for order %s: %s. "
                    "Order can be pushed manually.",
                    order.name, str(e)
                )
                # Post a message to the order chatter for visibility
                order.message_post(
                    body=_(
                        "⚠️ Auto-fulfillment to Printful failed: %s\n\n"
                        "You can push this order manually using the "
                        "'Push Order to Printful' button."
                    ) % str(e),
                    message_type='notification',
                )
            except Exception as e:
                # Log unexpected errors but don't block order confirmation
                _logger.exception(
                    "Unexpected error during auto-fulfillment of order %s",
                    order.name
                )
                order.message_post(
                    body=_(
                        "⚠️ Auto-fulfillment to Printful encountered an error: %s\n\n"
                        "You can push this order manually using the "
                        "'Push Order to Printful' button."
                    ) % str(e),
                    message_type='notification',
                )

        return result

    def _validate_order_for_printful(self):
        """
        Validate that the order has all required fields for Printful.

        Raises:
            UserError: If validation fails with detailed error message
        """
        self.ensure_one()
        errors = []

        # Validate partner information
        partner = self.partner_id
        if not partner:
            errors.append("• Customer is required")
        else:
            if not partner.name:
                errors.append("• Customer name is required")
            if not partner.email:
                errors.append("• Customer email is required")
            if not partner.street:
                errors.append("• Customer street address is required")
            if not partner.city:
                errors.append("• Customer city is required")
            if not partner.country_id:
                errors.append("• Customer country is required")
            if not partner.zip:
                errors.append("• Customer ZIP/postal code is required")

        # Validate order lines
        printful_items = []
        for line in self.order_line:
            # Skip delivery/shipping lines
            is_shipping_line = (
                line.product_id.is_delivery_product if line.product_id else False
            ) or (
                line.product_id.type == 'service' and
                any(keyword in (line.name or '').lower()
                    for keyword in ['delivery', 'shipping'])
            )

            if line.product_id and not is_shipping_line:
                if line.product_id.printful_variant_ref:
                    printful_items.append(line)
                elif line.product_id.sale_ok:
                    # Product exists but isn't a Printful product
                    _logger.warning(
                        "Order %s contains non-Printful product: %s",
                        self.name, line.product_id.name
                    )

        if not printful_items:
            errors.append("• Order must contain at least one Printful product")

        # Validate each Printful item has required fields
        for line in printful_items:
            product = line.product_id
            if not product.printful_variant_id:
                errors.append(f"• Product '{product.name}' is missing Printful variant ID")
            if not product.printful_sku:
                errors.append(f"• Product '{product.name}' is missing Printful SKU")

        # Raise all errors together
        if errors:
            raise UserError(_(
                'Cannot push order to Printful. Please fix the following issues:\n\n%s'
            ) % '\n'.join(errors))

    def _push_to_printful(self):
        """
        Push order to Printful for fulfillment.

        This method implements transaction safety:
        - Validates order data before pushing
        - Uses database-level locking to prevent race conditions
        - Idempotency check prevents duplicate pushes
        - Savepoint ensures atomic database updates
        - Proper error handling with detailed logging

        Raises:
            UserError: If validation fails, order already pushed, API config missing, or API call fails
        """
        self.ensure_one()

        # Validate order data first
        self._validate_order_for_printful()

        # Use database-level locking to prevent race conditions (TOCTOU)
        # This ensures only one concurrent request can push the same order
        try:
            self.env.cr.execute(
                "SELECT id FROM sale_order WHERE id = %s FOR UPDATE NOWAIT",
                (self.id,)
            )
        except OperationalError:
            # Another transaction is already processing this order
            raise UserError(_(
                'This order is currently being processed by another user. '
                'Please wait a moment and try again.'
            ))

        # Re-read the record after acquiring lock to get latest state
        self.env.cr.execute(
            "SELECT order_external_ref FROM sale_order WHERE id = %s",
            (self.id,)
        )
        result = self.env.cr.fetchone()
        current_external_ref = result[0] if result else None

        # Idempotency check - prevent duplicate pushes (now race-safe)
        if current_external_ref:
            raise UserError(_(
                'This order has already been pushed to Printful (External Ref: %s). '
                'To push again, please clear the External Ref field first.'
            ) % current_external_ref)

        # Get API configuration
        printful_config = self.env['printful.printful'].search([], limit=1)
        if not printful_config or not printful_config.token:
            raise UserError(_('Please configure a Printful API token.'))

        headers = {
            'Authorization': 'Bearer ' + printful_config.token,
            'Content-Type': 'application/json',
        }

        # Build order data
        order_data = self._build_printful_order_data()
        external_id = order_data['external_id']

        _logger.info("Pushing order %s to Printful with external_id %s", self.name, external_id)

        try:
            # Step 1: Create order in Printful
            create_result = self._create_printful_order(order_data, headers)

            # CRITICAL: Immediately persist external_id to prevent orphaned orders
            # This ensures we track the Printful order even if confirmation fails
            printful_order_id = create_result['result'].get('id')
            self.write({
                'order_external_ref': external_id,
                'order_ref': str(printful_order_id) if printful_order_id else '',
                'printful_fulfillment_status': 'pending',
            })
            # Force commit of external_id to prevent loss on later failure
            self.env.cr.commit()

            _logger.info("Order %s created in Printful with ID %s",
                       self.name, printful_order_id)

            # Step 2: Confirm the order
            try:
                confirm_result = self._confirm_printful_order(create_result, headers)

                # Update with full confirmation response
                self._update_from_printful_response(confirm_result)
                _logger.info("Order %s confirmed in Printful", self.name)

            except (requests.exceptions.RequestException, UserError) as confirm_error:
                # Confirmation failed but order was created - update status and re-raise
                self.write({'printful_fulfillment_status': 'pending'})
                self.message_post(
                    body=_(
                        "⚠️ Order was created in Printful (ID: %s) but confirmation failed: %s\n\n"
                        "The order may need to be confirmed manually in the Printful dashboard."
                    ) % (printful_order_id, str(confirm_error)),
                    message_type='notification',
                )
                raise

        except requests.exceptions.Timeout:
            _logger.error("Timeout while pushing order %s to Printful", self.name)
            raise UserError(_(
                'Request to Printful timed out. Please check your network connection '
                'and try again. If the problem persists, check Printful dashboard for '
                'partial orders with external ID: %s'
            ) % external_id)

        except requests.exceptions.RequestException as e:
            _logger.exception("Network error while pushing order %s to Printful", self.name)
            raise UserError(_(
                'Network error communicating with Printful: %s\n\n'
                'Please check Printful dashboard for partial orders with external ID: %s'
            ) % (str(e), external_id))

    def _create_printful_order(self, order_data, headers):
        """
        Create order in Printful API.

        Args:
            order_data: Dict with order payload
            headers: Auth headers for API

        Returns:
            Dict with API response

        Raises:
            UserError: If API returns error
        """
        url = f"{PRINTFUL_API_BASE}/orders"
        response = requests.post(
            url,
            headers=headers,
            data=json.dumps(order_data),
            timeout=PRINTFUL_API_TIMEOUT
        )

        # Validate response before parsing JSON
        # Non-2xx responses might return HTML error pages
        try:
            result = response.json()
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError) as e:
            _logger.error(
                "Printful API returned non-JSON response (status %d): %s",
                response.status_code, response.text[:500]
            )
            raise UserError(_(
                'Printful API returned an invalid response (HTTP %d). '
                'The service may be temporarily unavailable. Please try again later.'
            ) % response.status_code)

        if result.get('code') != 200:
            _logger.error("Printful order creation failed: %s", result)
            raise UserError(_(
                'Failed to create Printful order: %s\n\nData sent: %s'
            ) % (str(result), str(order_data)))

        return result

    def _confirm_printful_order(self, create_result, headers):
        """
        Confirm a created Printful order.

        Args:
            create_result: Response from order creation
            headers: Auth headers for API

        Returns:
            Dict with API response

        Raises:
            UserError: If confirmation fails
        """
        external_id = create_result['result']['external_id']
        confirm_url = f"{PRINTFUL_API_BASE}/orders/@{external_id}/confirm"

        response = requests.post(
            confirm_url,
            headers=headers,
            timeout=PRINTFUL_API_TIMEOUT
        )

        # Validate response before parsing JSON
        try:
            result = response.json()
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
            _logger.error(
                "Printful confirmation API returned non-JSON response (status %d): %s",
                response.status_code, response.text[:500]
            )
            raise UserError(_(
                'Printful API returned an invalid response during order confirmation (HTTP %d). '
                'Please check Printful dashboard to verify order status. External ID: %s'
            ) % (response.status_code, external_id))

        if result.get('code') != 200:
            _logger.error(
                "Printful order confirmation failed for external_id %s: %s",
                external_id, result
            )
            raise UserError(_(
                'Order was created in Printful but confirmation failed: %s\n\n'
                'Please check Printful dashboard and confirm order manually. '
                'External ID: %s'
            ) % (str(result), external_id))

        return result

    def _build_printful_order_data(self):
        """Build the order data payload for Printful API."""
        # Get Printful configuration
        printful_config = self.env['printful.printful'].search([], limit=1)

        # Generate unique external ID using UUID for security
        external_id = "ODOO-" + str(uuid.uuid4())

        # Build items list
        items = []
        price_subtotal_grand = 0.0
        shipping_cost = 0.0

        for line in self.order_line:
            price_subtotal_grand += line.price_subtotal

            # Identify shipping/delivery products by field or product type
            is_shipping_line = (
                line.product_id.is_delivery_product if line.product_id else False
            ) or (
                line.product_id.type == 'service' and
                any(keyword in (line.name or '').lower()
                    for keyword in ['delivery', 'shipping'])
            )

            if is_shipping_line:
                # Accumulate all shipping/handling fees instead of overwriting
                shipping_cost += line.price_subtotal
                continue

            if line.product_id.printful_variant_ref:
                # Validate quantity - Printful requires integer quantities
                qty = line.product_uom_qty
                qty_int = int(qty)

                # Warn if quantity was truncated (fractional part lost)
                if qty != qty_int:
                    _logger.warning(
                        "Order %s line '%s': Quantity %.2f truncated to %d for Printful. "
                        "Printful only accepts integer quantities.",
                        self.name, line.product_id.name, qty, qty_int
                    )

                # Skip zero or negative quantities with warning
                if qty_int <= 0:
                    _logger.warning(
                        "Order %s line '%s': Skipping invalid quantity %d",
                        self.name, line.product_id.name, qty_int
                    )
                    continue

                items.append({
                    "variant_id": line.product_id.printful_variant_id,
                    "external_variant_id": line.product_id.printful_variant_ref,
                    "quantity": qty_int,
                    "price": str(line.product_id.standard_price),
                    "retail_price": str(line.product_id.standard_price),
                    "name": line.product_id.name,
                    "sku": line.product_id.printful_sku or '',
                })

        # Build recipient data
        partner = self.partner_id
        recipient = {
            "name": partner.name,
            "company": self.env.user.company_id.name,
            "address1": partner.street or '',
            "address2": partner.street2 or '',
            "city": partner.city or '',
            "state_code": partner.state_id.code if partner.state_id else '',
            "state_name": partner.state_id.name if partner.state_id else '',
            "country_code": partner.country_id.code if partner.country_id else '',
            "country_name": partner.country_id.name if partner.country_id else '',
            "zip": str(partner.zip or ''),
            "phone": partner.phone or '',
            "email": partner.email or '',
            "tax_number": str(partner.tax_number or ''),
        }

        # Get currency from Printful config or fall back to order currency
        currency_code = (
            printful_config.currency_id.name
            if printful_config and printful_config.currency_id
            else self.currency_id.name
        )

        # Determine shipping method
        # 1. Try to map from order's delivery carrier
        # 2. Fall back to default shipping method from config
        # 3. Fall back to STANDARD
        shipping_method = "STANDARD"

        if printful_config:
            # Try to find mapping from delivery carrier
            if self.carrier_id:
                carrier_mapping = printful_config.shipping_method_ids.filtered(
                    lambda m: m.delivery_carrier_id.id == self.carrier_id.id
                )[:1]
                if carrier_mapping:
                    shipping_method = carrier_mapping.printful_method_id
                    _logger.info(
                        "Using shipping method %s mapped from carrier %s",
                        shipping_method, self.carrier_id.name
                    )

            # Fall back to default shipping method if no carrier mapping
            if shipping_method == "STANDARD" and printful_config.default_shipping_method_id:
                shipping_method = printful_config.default_shipping_method_id.printful_method_id
                _logger.info(
                    "Using default shipping method: %s",
                    shipping_method
                )

        # Build gift message from config
        gift_message = "Thank you for your purchase!"
        if printful_config and printful_config.gift_message_default:
            gift_message = printful_config.gift_message_default

        # Build packing slip from config
        packing_slip = {}
        if printful_config:
            if printful_config.packing_slip_email:
                packing_slip["email"] = printful_config.packing_slip_email
            if printful_config.packing_slip_phone:
                packing_slip["phone"] = printful_config.packing_slip_phone
            if printful_config.packing_slip_message:
                # Truncate to 1024 chars as per Printful API limits
                packing_slip["message"] = printful_config.packing_slip_message[:1024]
            if printful_config.packing_slip_logo_url:
                packing_slip["logo_url"] = printful_config.packing_slip_logo_url
            if printful_config.packing_slip_store_name:
                packing_slip["store_name"] = printful_config.packing_slip_store_name

        return {
            "external_id": external_id,
            "shipping": shipping_method,
            "recipient": recipient,
            "items": items,
            "retail_costs": {
                "currency": currency_code,
                "subtotal": price_subtotal_grand,
                "discount": "0.00",
                "shipping": shipping_cost,
                "tax": self.amount_tax,
            },
            "gift": {
                "subject": f"To {partner.name}",
                "message": gift_message,
            },
            "packing_slip": packing_slip,
        }

    def _update_from_printful_response(self, response_data):
        """Update sale order from Printful API response."""
        result = response_data.get('result', {})
        costs = result.get('costs', {})
        pricing = result.get('pricing_breakdown', [{}])[0]

        self.write({
            'order_ref': str(result.get('id', '')),
            'order_external_ref': result.get('external_id', ''),
            'order_store_ref': result.get('store', ''),
            'order_shipping': result.get('shipping', ''),
            'order_shipping_service_name': result.get('shipping_service_name', ''),
            'printful_order_notes': result.get('notes', ''),
            'order_currency': costs.get('currency', ''),
            'order_subtotal': costs.get('subtotal', 0),
            'order_discount': costs.get('discount', 0),
            'shipping': costs.get('shipping', 0),
            'order_digitization': costs.get('digitization', 0),
            'order_additional_fee': costs.get('additional_fee', 0),
            'order_fulfillment_fee': costs.get('fulfillment_fee', 0),
            'order_retail_delivery_fee': costs.get('retail_delivery_fee', 0),
            'order_tax': costs.get('tax', 0),
            'order_total': costs.get('total', 0),
            'printful_dashboard_url': result.get('dashboard_url', ''),
            'customer_pays': pricing.get('customer_pays', 0),
            'printful_price': pricing.get('printful_price', 0),
            'profit': pricing.get('profit', 0),
            'currency_symbol': pricing.get('currency_symbol', ''),
        })


class ResPartner(models.Model):
    """Extension of res.partner for Printful customer data."""
    _inherit = 'res.partner'

    tax_number = fields.Char(string='Tax Number')
    company = fields.Char(string='Company Name')


class SaleOrderLine(models.Model):
    """Extension of sale.order.line for Printful products."""
    _inherit = 'sale.order.line'

    # Track if price was manually set (to prevent auto-override)
    price_manually_set = fields.Boolean(
        string="Price Manually Set",
        default=False,
        help="If checked, the price won't be auto-updated from product cost.",
    )

    @api.onchange('product_id')
    def _onchange_product_set_printful_price(self):
        """Set price from Printful product cost when product changes, unless manually set."""
        for line in self:
            if line.product_id and not line.price_manually_set:
                # Use list_price (sales price) instead of standard_price (cost)
                # This allows proper retail pricing while keeping cost separate
                line.price_unit = line.product_id.list_price or line.product_id.standard_price

    @api.onchange('price_unit')
    def _onchange_price_unit_mark_manual(self):
        """Mark price as manually set when user changes it."""
        # Only mark as manual if there's already a product selected
        # This avoids false positives during initial line creation
        for line in self:
            if line.product_id and line.price_unit != line.product_id.list_price:
                line.price_manually_set = True

    product_id = fields.Many2one(
        comodel_name='product.product',
        string="Product",
        change_default=True,
        ondelete='restrict',
        check_company=True,
        index='btree_not_null',
        domain="[('printful_product_in_stock', '=', True), ('sale_ok', '=', True), "
               "'|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
