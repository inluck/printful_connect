# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import random
import json

# import logging
# _logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    order_ref = fields.Char()

    order_external_ref = fields.Char()
    order_store_ref = fields.Char()
    order_shipping = fields.Char()
    order_shipping_service_name = fields.Char()
    printful_order_notes = fields.Char()
    order_currency = fields.Char()
    order_subtotal = fields.Float()
    order_discount = fields.Float()
    order_digitization = fields.Float()
    order_additional_fee = fields.Float()
    order_fulfillment_fee = fields.Float()
    order_retail_delivery_fee = fields.Float()
    order_tax = fields.Float()
    order_total = fields.Float()

    printful_dashboard_url = fields.Char()

    # price break down
    customer_pays = fields.Float()
    printful_price = fields.Float()
    profit = fields.Float()
    currency_symbol = fields.Char()
    shipping = fields.Float()
    order_vat = fields.Float()

    tax_number = fields.Char(related='partner_id.tax_number')
    company = fields.Char(related='partner_id.company')

    def action_create_push_order_printful(self):
        for rec in self:
            headers = {'Authorization': 'Bearer ' + self.env['printful.printful'].search([], limit=1).token}

            url = "https://api.printful.com/orders"
            external_id = random.random()
            external_id = "ODOO" + str(external_id).split(".")[1]
            items = []
            price_subtotal_grand = 0.0
            shipping = 0.0
            for line in rec.order_line:
                # _logger.debug(line.price_subtotal)
                if "Delivery" in line.name:
                    shipping = line.price_subtotal
#                 _logger.debug(line.price_subtotal_grand)
                price_subtotal_grand = price_subtotal_grand + line.price_subtotal
                if line.product_id.printful_variant_ref:
                    items.append({
                            "variant_id": line.product_id.printful_variant_id,
                            "external_variant_id": line.product_id.printful_variant_ref,
                            "quantity": line.product_uom_qty,
                            "price": str(line.product_id.standard_price),
                            "retail_price": str(line.product_id.standard_price),
                            "name": line.product_id.name,
                            "sku": line.product_id.printful_sku
                        })
            data = {
                "external_id": external_id,
                "shipping": "STANDARD",
                "recipient": {
                    "name": rec.partner_id.name,
                    "company": self.env.user.company_id.name,
                    "address1": rec.partner_id.street,
                    "address2": rec.partner_id.street2,
                    "city": rec.partner_id.city,
                    "state_code": rec.partner_id.state_id.code if rec.partner_id.state_id else "",
                    "state_name": rec.partner_id.state_id.name if rec.partner_id.state_id else "",
                    "country_code": rec.partner_id.country_id.code if rec.partner_id.country_id else "",
                    "country_name": rec.partner_id.country_id.name if rec.partner_id.country_id else "",
                    "zip": str(rec.partner_id.zip),
                    "phone": rec.partner_id.phone,
                    "email": rec.partner_id.email,
                    "tax_number": str(rec.partner_id.tax_number),
                },
                "items": items,
                "retail_costs": {
                    "currency": "CAD",
                    "subtotal": price_subtotal_grand,
                    "discount": "0.00",
                    "shipping": shipping,
                    "tax": rec.amount_tax
                },
                "gift": {
                    "subject": "To " + rec.partner_id.name,
                    "message": "Enjoy your merch!"
                },
                "packing_slip": {}
            }
            data = json.dumps(data)
            response = requests.post(url, headers=headers, data=data)
            printfull = response.json()
            if printfull['code'] != 200:
                one = str(printfull)
                two = str(data)
                raise UserError(str(one + two))
            else:
                rec.order_ref = "#PF" + str(printfull['result']['id'])
                rec.order_external_ref = printfull['result']['external_id']
                rec.order_store_ref = printfull['result']['store']
                rec.order_shipping = printfull['result']['shipping']
                rec.order_shipping_service_name = printfull['result']['shipping_service_name']
                rec.printful_order_notes = printfull['result']['notes']
                rec.order_currency = printfull['result']['costs']['currency']
                rec.order_subtotal = printfull['result']['costs']['subtotal']
                rec.order_discount = printfull['result']['costs']['discount']
                rec.shipping = printfull['result']['costs']['shipping']
                rec.order_digitization = printfull['result']['costs']['digitization']
                rec.order_additional_fee = printfull['result']['costs']['additional_fee']
                rec.order_fulfillment_fee = printfull['result']['costs']['fulfillment_fee']
                rec.order_retail_delivery_fee = printfull['result']['costs']['retail_delivery_fee']
                rec.order_tax = printfull['result']['costs']['tax']
                rec.order_total = printfull['result']['costs']['total']
                rec.printful_dashboard_url = printfull['result']['dashboard_url']
                rec.customer_pays = printfull['result']['pricing_breakdown'][0]['customer_pays']
                rec.printful_price = printfull['result']['pricing_breakdown'][0]['printful_price']
                rec.profit = printfull['result']['pricing_breakdown'][0]['profit']
                rec.currency_symbol = printfull['result']['pricing_breakdown'][0]['currency_symbol']
                one = str(printfull)
                two = str(data)
                raise UserError(str(one + two))

class ResPartner(models.Model):
    _inherit = 'res.partner'

    tax_number = fields.Char()
    company = fields.Char()

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    price_unit = fields.Float(
        string="Unit Price",
        related='product_id.standard_price',
        store=True, required=True)
    # Generic configuration fields
    product_id = fields.Many2one(
        comodel_name='product.product',
        string="Product",
        change_default=True, ondelete='restrict', check_company=True, index='btree_not_null',
        domain="[('printful_product_in_stock', '=', True),('sale_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]")
