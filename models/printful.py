# -*- coding: utf-8 -*-
import requests
import base64
import json
from ratelimit import limits, sleep_and_retry
from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError
import logging
_logger = logging.getLogger(__name__)
class PrintfulPrintful(models.Model):
    _name = 'printful.printful'
    _description = "Printful Configuration"

    @sleep_and_retry
    @limits(calls=100, period=60)
    def make_api_request(url, headers):
        response = requests.get(url, headers=headers)
        if response.status_code == 429:
            raise requests.exceptions.RequestException('Rate limit exceeded')
        response.raise_for_status()
        return response
    
    store = fields.Char(string="PrintFul Store")
    token = fields.Char(string="PrintFul Token")
    size_attribute_id = fields.Many2one(comodel_name="product.attribute", string="Size Attribute")
    color_attribute_id = fields.Many2one(comodel_name="product.attribute", string="Color Attribute")
    product_public_category_id = fields.Many2one(comodel_name="product.public.category", string="Public Category")

    def action_get_printful_order(self):
        so = self.env['sale.order'].search([])
        partner = self.env['res.partner']
        headers = {'Authorization': 'Bearer ' + self.env['printful.printful'].search([], limit=1).token}

        url = "https://api.printful.com/orders"
        response = make_api_request(url, headers)
        printful = response.json()
        for order in printful['result']:
            so_exists = so.filtered(lambda x: x.order_ref == "#PF" + str(order['id']))
            if not so_exists:
                customer = partner.create({
                    'name': order['recipient']['name'] if order['recipient']['name'] else "Print Full Customer",
                    'city': order['recipient']['city'],
                    'street': str(order['recipient']['address1']) + " " + str(order['recipient']['address2']) + " " + str(order['recipient']['state_code']),
                    'street2': str(order['recipient']['country_name']) + " " + str(order['recipient']['state_name']) + " " + str(order['recipient']['country_code']),
                    'zip': order['recipient']['zip'],
                    'email': order['recipient']['email'],
                    'phone': order['recipient']['phone'],
                    'tax_number': str(order['recipient']['tax_number']),
                    'company': order['recipient']['company'],
                })
                lines = []
                for item in order['items']:
                    product = self.env['product.product'].search([('printful_variant_ref', '=', str(item['external_variant_id']))])
                    lines.append((0, 0, {
                        'name': product.display_name,
                        'product_id': product.id,
                        'price_unit': item['price'],
                        'product_uom_qty': item['quantity'],
                        'tax_id': None,
                    }),)
                so_val = {
                    'order_ref': "#PF" + str(order['id']),
                    'order_external_ref': order['external_id'],
                    'order_store_ref': order['store'],
                    'order_shipping': order['shipping'],
                    'order_shipping_service_name': order['shipping_service_name'],
                    'printful_order_notes': order['notes'],

                    'order_currency': order['costs']['currency'],
                    'order_subtotal': order['costs']['subtotal'],
                    'order_discount': order['costs']['discount'],
                    'shipping': order['costs']['shipping'],
                    'order_digitization': order['costs']['digitization'],
                    'order_additional_fee': order['costs']['additional_fee'],
                    'order_fulfillment_fee': order['costs']['fulfillment_fee'],
                    'order_retail_delivery_fee': order['costs']['retail_delivery_fee'],
                    'order_tax': order['costs']['tax'],
                    'order_vat': order['costs']['vat'],
                    'order_total': order['costs']['total'],

                    'printful_dashboard_url': order['dashboard_url'],
                    'customer_pays': order['pricing_breakdown'][0]['customer_pays'],
                    'printful_price': order['pricing_breakdown'][0]['printful_price'],
                    'profit': order['pricing_breakdown'][0]['profit'],
                    'currency_symbol': order['pricing_breakdown'][0]['currency_symbol'],

                    'partner_id': customer.id,
                    'order_line': lines,
                }
                so.create(so_val)
    
    def action_get_printful_product(self):
        headers = {'Authorization': 'Bearer ' + self.env['printful.printful'].search([], limit=1).token}
        url = "https://api.printful.com/store/products"
        response = make_api_request(url, headers)
        printful = response.json()

        size_attribute = self.env['printful.printful'].search([], limit=1).size_attribute_id
        color_attribute = self.env['printful.printful'].search([], limit=1).color_attribute_id
        #_logger.debug(printful['result'])
        for product in printful['result']:
            img = response = make_api_request(url, headers={})
            pt_obj = None
            pt_exist = self.env['product.template'].search([
                    ('printful_ref', '=', str(product['id']))])
            #_logger.debug(pt_exist)
            if not pt_exist:
                pt_obj = self.env['product.template'].create({
                    'name': product['name'],
                    'default_code': str(product['external_id']),
                    'printful_ref': str(product['id']),
                    'printful_external_ref': str(product['external_id']),
                    'image_1920': base64.b64encode(img.content)
                })
            else:
                #_logger.debug("rewrite")
                pt_obj = pt_exist[0]
                pt_obj.write({
                    'name': product['name'],
                    'printful_ref': str(product['id']),
                    'printful_external_ref': str(product['external_id']),
                    'image_1920': base64.b64encode(img.content)
                })

            product_details_endpoint = f"https://api.printful.com/store/products/{product['id']}"
            product_details_response = make_api_request(product_details_endpoint,  headers=headers)
            product_details = json.loads(product_details_response.text)

            if product_details['code'] != 200:
                _logger.error(f"Failed to retrieve Printful product details: {product_details['result']}")
                continue
                
#             #_logger.debug(product_details)

            sync_variants = product_details['result']['sync_variants']
            if not sync_variants:
                _logger.warning(f"No sync variants found for Printful product ID {product.printful_id}")
                continue

            size_attribute_line_ids = []
            size_attribute_line = None
            size_attribute_line_ids = []
            color_attribute_line = None
            color_attribute_line_ids = []
            new_variant = None
            new_color_variant = None
            
            
            lowest_price = min(product["retail_price"] for product in sync_variants)

            for sync_variant in sync_variants:

          
#                 variants_endpoint = f"https://api.printful.com/store/variants/@{sync_variant['external_id']}"
#                 variants_response = requests.get(variants_endpoint, headers=headers)
#                 variant = json.loads(variants_response.text)

#                 if variant['code'] != 200:
#                     _logger.error(f"Failed to retrieve Printful variants: {variant['result']}")
#                     continue
                    
                variant_data_endpoint = f"https://api.printful.com/products/variant/{sync_variant['variant_id']}"
                variant_response = make_api_request(variant_data_endpoint, headers={})

                if variant_response.status_code != 200:
                    _logger.warning(f"Failed to retrieve Printful variant data for variant ID {sync_variant['variant_id']}: {variant_response.text}")
                    continue

                variant_data = json.loads(variant_response.text)['result']['variant']
                variant_product_data = json.loads(variant_response.text)['result']['product']
#                 #_logger.debug(variant_data)
                #_logger.debug(variant_data.get('size'))
                if not variant_data.get('size'):
                    _logger.warning(f"No size data found for variant ID {sync_variant['variant_id']}")
                
                markup_price = float(sync_variant['retail_price']) * float(1.00)
                list_price = markup_price
                size_attribute_value = self._get_attribute_value(size_attribute, variant_data['size'])
                variant_ids = []
                size_attribute_line = self._get_attribute_line(size_attribute, pt_obj.id)

                if variant_data['size']:                        
                    if size_attribute_line == None:
                        size_attribute_line = self.env['product.template.attribute.line'].create({
                            'product_tmpl_id': pt_obj.id,
                            'attribute_id': size_attribute[0].id,
                            'value_ids': [(4, size_attribute_value)]
                        })
                        size_attribute_line_ids.append(size_attribute_line)
                        variant_ids.append(size_attribute_line[0].id)
                    else:
                        size_attribute_line.write({
                            'value_ids': [(4, size_attribute_value)]
                        })
                        variant_ids.append(size_attribute_line[0].id)
                
                    ptav_obj = self.env['product.template.attribute.value'].search([
                        ('attribute_line_id', 'in', [size_attribute_line.id]),
                        ('name', '=', variant_data['size']),
                        ('product_tmpl_id', '=', pt_obj.id)]
                    )
                    price_extra = list_price - float(lowest_price)
                    ptav_obj.write({
                        'price_extra': price_extra
                    })

                if variant_data['color']:
                    color_attribute_value = self._get_attribute_value(color_attribute, variant_data['color'])
                    color_attribute_line = self._get_attribute_line(color_attribute, pt_obj.id)

                    if color_attribute_line == None:
                        color_attribute_line = self.env['product.template.attribute.line'].create({
                            'product_tmpl_id': pt_obj.id,
                            'attribute_id': color_attribute[0].id,
                            'value_ids': [(4, color_attribute_value)]
                        })
                        size_attribute_line_ids.append(color_attribute_line)
                        variant_ids.append(color_attribute_line[0].id)
                    else:
                        color_attribute_line.write({
                            'value_ids': [(4, color_attribute_value)]
                        })
                        variant_ids.append(color_attribute_line[0].id)

                product_variant = None
                if color_attribute_line == None:
                    product_variant = self.env['product.product'].search([
                        ('attribute_line_ids', 'in', [size_attribute_line.id]),
                        ('product_template_variant_value_ids', '!=', False),

                    ],order='id desc')
                elif size_attribute_line == None:
                    
                    product_variant = self.env['product.product'].search([
                        ('attribute_line_ids', 'in', [color_attribute_line.id]),
                        ('product_template_variant_value_ids', '!=', False),

                    ],order='id desc')
                elif size_attribute_line != None and color_attribute_line != None:

                    product_variant = self.env['product.product'].search([
                        ('attribute_line_ids', 'in', [color_attribute_line.id, size_attribute_line.id]),
                        ('product_template_variant_value_ids', '!=', False),
                    ],order='id desc')

                if product_variant:
                    shipping_data = {
                      "recipient": {
                        "address1": "88 Lester St",
                        "city": "St. John's",
                        "country_code": "CA",
                        "state_code": "NL",
                        "zip": "A1E2P8",
                        "phone": "string"
                      },
                      "items": [
                        {
                          "variant_id": sync_variant['variant_id'],
                          "external_variant_id": sync_variant['external_id'],
                          "quantity": 1,
                          "value": sync_variant['retail_price']
                        }
                      ],
                      "currency": "CAD",
                      "locale": "en_US"
                    }
                    shipping_endpoint = f"https://api.printful.com/shipping/rates"
                    shipping_response = requests.post(shipping_endpoint, json=shipping_data, headers=headers)
                    shipping_data = json.loads(shipping_response.text)
                    for rate in shipping_data['result']:

                        if rate['id'] == "STANDARD":
                            variant_data['shipping_rate'] = rate['rate']
                    img = None

                    for file in sync_variant['files']:
                        if file['type'] == 'preview':
                            img = make_api_request(file['preview_url'], headers={})
#                             _logger.debug(file['preview_url'])
#                             _logger.debug(variant['result']['name'])
                    product_variant[0].write({
                        'list_price': lowest_price,
                        'volume': variant_data['shipping_rate'],
                        'default_code': sync_variant['sku'],
                        'printful_variant_ref': sync_variant['variant_id'],
                        'printful_variant_id': sync_variant['variant_id'],
                        'name': product['name'],
                        'image_1920': base64.b64encode(img.content),
                        'printful_sku': sync_variant['sku'],
                        'printful_currency':  sync_variant['currency'],
                        'printful_size':  variant_data['size'],
                        'printful_color':  variant_data['color'],
                        'printful_product': pt_obj.id,
                        'printful_variant_ref': sync_variant['external_id'],
                        'printful_variant_external_ref': sync_variant['id'],
                        'printful_product_in_stock': variant_data['in_stock'],
                        'description_sale': variant_product_data['description'],
                        'website_published': variant_data['in_stock'],
#                         'website_description': ''
                        })
            pt_obj.write({
                'attribute_line_ids': [(4, line.id) for line in size_attribute_line_ids]
            })
                
    def _get_attribute_value(self, attribute_id, value_name):
        attribute_value = self.env['product.attribute.value'].search([
            ('attribute_id', '=', attribute_id[0].id),
            ('name', '=', value_name),
        ])
        if attribute_value.id:
            return attribute_value.id
            
        elif value_name:
            return self.env['product.attribute.value'].create({
                'name': value_name,
                'attribute_id': attribute_id[0].id,
            }).id


    def _get_attribute_line(self, attribute_id, tmpl_id):
        attribute_line = self.env['product.template.attribute.line'].search([
            ('attribute_id', '=', attribute_id[0].id),
            ('product_tmpl_id', '=', tmpl_id),
        ])
        try:
            return attribute_line[0]
            
        except:
            return None
