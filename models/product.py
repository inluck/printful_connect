# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class ProductTemplate(models.Model):
    """
    Extension of product.template to store Printful product data.
    """
    _inherit = 'product.template'

    # Printful identifiers
    printful_ref = fields.Char(
        string='Printful ID',
        help='Printful sync product ID',
        index=True,
    )
    printful_external_ref = fields.Char(
        string='External ID',
        help='External reference from Printful',
    )
    printful_product_ref = fields.Char(
        string='Product Ref',
        help='Printful product reference',
    )
    printful_product_external_ref = fields.Char(
        string='Product External Ref',
    )

    # Printful metadata
    printful_shipping = fields.Char(
        string='Estimated Delivery',
        help='Estimated shipping time from Printful',
    )
    printful_sizeguide = fields.Html(
        string='Size Guide',
        help='HTML size guide from Printful',
        sanitize=True,  # Sanitize to prevent XSS from external API data
        sanitize_tags=True,
        sanitize_attributes=True,
        sanitize_style=True,
        strip_style=False,  # Preserve styling for tables
        strip_classes=False,  # Preserve classes for formatting
    )


class ProductPublicCategory(models.Model):
    """
    Extension of product.public.category to store Printful category mapping.
    """
    _inherit = 'product.public.category'

    printful_catid = fields.Char(
        string='Printful Category ID',
        help='Printful category identifier for mapping',
    )
