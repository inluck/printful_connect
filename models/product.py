# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    printful_ref = fields.Char()
    printful_external_ref = fields.Char()
    printful_product_ref = fields.Char()
    printful_product_external_ref = fields.Char()

class ProductPublicCategory(models.Model):
    _inherit = 'product.public.category'

    printful_catid = fields.Char()
