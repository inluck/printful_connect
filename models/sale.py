# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
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
    order_ref = fields.Char(string='Printful Order ID')
    order_external_ref = fields.Char(string='External Ref')
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

    # Related partner fields
    tax_number = fields.Char(related='partner_id.tax_number', string='Tax Number')
    company = fields.Char(related='partner_id.company', string='Company')

    def action_create_push_order_printful(self):
        """Push this order to Printful for fulfillment."""
        for rec in self:
            rec._push_to_printful()

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
        - Idempotency check prevents duplicate pushes
        - Savepoint ensures atomic database updates
        - Proper error handling with detailed logging

        Raises:
            UserError: If validation fails, order already pushed, API config missing, or API call fails
        """
        self.ensure_one()

        # Validate order data first
        self._validate_order_for_printful()

        # Idempotency check - prevent duplicate pushes
        if self.order_external_ref:
            raise UserError(_(
                'This order has already been pushed to Printful (External Ref: %s). '
                'To push again, please clear the External Ref field first.'
            ) % self.order_external_ref)

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
            # Use savepoint for atomic operation
            with self.env.cr.savepoint():
                # Step 1: Create order in Printful
                create_result = self._create_printful_order(order_data, headers)

                # Update order with creation response (within savepoint)
                self._update_from_printful_response(create_result)
                _logger.info("Order %s created in Printful with ID %s",
                           self.name, create_result['result'].get('id'))

                # Step 2: Confirm the order
                confirm_result = self._confirm_printful_order(create_result, headers)

                # Update with confirmation response
                self._update_from_printful_response(confirm_result)
                _logger.info("Order %s confirmed in Printful", self.name)

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
        result = response.json()

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
        result = response.json()

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
                shipping_cost = line.price_subtotal
                continue

            if line.product_id.printful_variant_ref:
                items.append({
                    "variant_id": line.product_id.printful_variant_id,
                    "external_variant_id": line.product_id.printful_variant_ref,
                    "quantity": int(line.product_uom_qty),
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

        return {
            "external_id": external_id,
            "shipping": "STANDARD",
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
                "message": "Enjoy your merch!",
            },
            "packing_slip": {},
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

    price_unit = fields.Float(
        string="Unit Price",
        related='product_id.standard_price',
        store=True,
        required=True,
    )

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
