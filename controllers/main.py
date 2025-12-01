# -*- coding: utf-8 -*-
from odoo import http
from odoo.addons.website_sale.controllers.main import WebsiteSale


class WebsiteSalePrintful(WebsiteSale):
    """
    Extension of WebsiteSale controller for Printful integration.

    Note: The size guide is rendered via the QWeb template (s_size_guide)
    which has direct access to product.printful_sizeguide field.
    No controller override is needed for basic functionality.

    This class is kept for potential future enhancements like:
    - Custom size guide rendering
    - Printful-specific product page modifications
    - Stock availability checks against Printful API
    """
    pass
