# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import requests
import random
import string
import requests
import base64
import io
from PyPDF2 import PdfFileReader
from odoo.exceptions import UserError, AccessError
from odoo.tools import pdf
import logging
import re
import html2text
import json

class ProductProduct(models.Model):
    _inherit = 'product.product'

    printful_ref = fields.Char()
    printful_external_ref = fields.Char()
    printful_variant_ref = fields.Char()
    printful_variant_external_ref = fields.Char()
    printful_sku = fields.Char()
    printful_currency = fields.Char()
    printful_size = fields.Char()
    printful_color = fields.Char()
    printful_product_in_stock = fields.Boolean(default=True)
    printful_variant_id = fields.Char()
    printful_product = fields.Many2one(comodel_name='product.template')
    printful_shipping = fields.Char(string='Estimated Delivery')
    printful_sizeguide = fields.Char(string='Size Guide')
    
    def action_set_printful_data(self):
        for rec in self:
            rem = False
#             headers = {'Authorization': 'Bearer ' + self.env['printful.printful'].search([], limit=1).token}
#             url = "https://api.printful.com/store/variants/" + rec.printful_variant_external_ref
#             response = requests.get(url, headers=headers)
#             printful = response.json()

#             if printful['code'] != 200:
#                 raise UserError(str(printful))
#             else:
#                 img = requests.get(printful['result']['product']['image'], headers={})
#                 in_stock = True
#                 stocks = requests.get("https://api.printful.com/products/variant/" + str(printful['result']['variant_id']))
#                 instock = stocks.json()
#                 if instock['code'] == 200:
#                     in_stock = instock['result']['variant']['in_stock']

#                 color = None
#                 size = None
#                 na = printful['result']['product']['name']
#                 color = None
#                 size = None
#                 try:
#                     color = na.split('(')[1].split(')')[0].split('/')[0]
#                 except:
#                     pass
#                 try:
#                     size = na.split('(')[1].split(')')[0].split('/')[1]
#                 except:
#                     pass
#                 rec.printful_sku = printful['result']['sku']
#                 rec.printful_currency = printful['result']['currency']
#                 rec.printful_size = size
#                 rec.printful_color = color
#                 rec.image_1920 = base64.b64encode(img.content)
#                 rec.name = printful['result']['name']
#                 rec.printful_product_in_stock = in_stock
#                 rec.printful_variant_id = printful['result']['variant_id']
