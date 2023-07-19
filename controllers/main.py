from odoo import http
from odoo.addons.website_sale.controllers.main import WebsiteSale

class WebsiteSaleInherit(WebsiteSale):
    @http.route()
    def product(self, product, category='', search='', **kwargs):
        if product.printful_sizeguide:
            product = http.request.env['product.template'].search([('id','=',product.id)], limit=1)
            product.printful_sizeguide = product.printful_sizeguide

        res = super(WebsiteSaleInherit, self).product(product, category, search, **kwargs)
        return res
