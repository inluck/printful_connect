# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import logging

_logger = logging.getLogger(__name__)


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

    def _push_to_printful(self):
        """Internal method to push order to Printful."""
        self.ensure_one()

        # Get API token
        printful_config = self.env['printful.printful'].search([], limit=1)
        if not printful_config or not printful_config.token:
            raise UserError(_('Please configure a Printful API token.'))

        headers = {
            'Authorization': 'Bearer ' + printful_config.token,
            'Content-Type': 'application/json',
        }

        # Build order data
        order_data = self._build_printful_order_data()

        # Create order
        url = "https://api.printful.com/orders"
        response = requests.post(url, headers=headers, data=json.dumps(order_data))
        result = response.json()

        if result.get('code') != 200:
            raise UserError(_('Failed to create Printful order: %s\n\nData sent: %s') % (
                str(result), str(order_data)
            ))

        # Update order with Printful response
        self._update_from_printful_response(result)

        # Confirm order
        confirm_url = f"https://api.printful.com/orders/@{result['result']['external_id']}/confirm"
        confirm_response = requests.post(confirm_url, headers=headers)
        confirm_result = confirm_response.json()

        if confirm_result.get('code') != 200:
            raise UserError(_('Failed to confirm Printful order: %s') % str(confirm_result))

        # Update with confirmed data
        self._update_from_printful_response(confirm_result)

    def _build_printful_order_data(self):
        """Build the order data payload for Printful API."""
        # Get Printful configuration
        printful_config = self.env['printful.printful'].search([], limit=1)

        # Generate unique external ID using UUID for security
        import uuid
        external_id = "ODOO" + str(uuid.uuid4())

        # Build items list
        items = []
        price_subtotal_grand = 0.0
        shipping_cost = 0.0

        for line in self.order_line:
            price_subtotal_grand += line.price_subtotal

            if "Delivery" in (line.name or ''):
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
