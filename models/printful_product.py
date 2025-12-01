# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    """
    Extension of product.product to store Printful variant data.
    """
    _inherit = 'product.product'

    # Printful identifiers
    printful_ref = fields.Char(string='Printful Ref')
    printful_external_ref = fields.Char(string='External Ref')
    printful_variant_ref = fields.Char(string='Variant Ref', index=True)
    printful_variant_external_ref = fields.Char(string='Variant External Ref')
    printful_variant_id = fields.Char(string='Variant ID')
    printful_sku = fields.Char(string='Printful SKU')

    # Printful metadata
    printful_currency = fields.Char(string='Currency')
    printful_size = fields.Char(string='Size')
    printful_color = fields.Char(string='Color')
    printful_product_in_stock = fields.Boolean(string='In Stock', default=True)
    printful_product = fields.Many2one(
        comodel_name='product.template',
        string='Printful Product',
    )
    printful_shipping = fields.Char(string='Estimated Delivery')
    printful_sizeguide = fields.Html(string='Size Guide')
