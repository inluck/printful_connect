# -*- coding: utf-8 -*-
from odoo import models, fields
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
    printful_currency = fields.Char(string='Printful Currency')
    printful_size = fields.Char(string='Size')
    printful_color = fields.Char(string='Color')
    printful_product_in_stock = fields.Boolean(string='In Stock', default=True)
    printful_product = fields.Many2one(
        comodel_name='product.template',
        string='Printful Product',
    )
    printful_shipping = fields.Char(string='Estimated Delivery')
    printful_sizeguide = fields.Html(
        string='Size Guide',
        sanitize=True,  # Sanitize to prevent XSS from external API data
        sanitize_tags=True,
        sanitize_attributes=True,
        sanitize_style=True,
        strip_style=False,
        strip_classes=False,
    )
    is_delivery_product = fields.Boolean(
        string='Is Delivery Product',
        default=False,
        help='Check this for shipping/delivery fee products',
    )
