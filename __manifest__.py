# -*- coding: utf-8 -*-
{
    'name': "Printful Connect",
    'description': "Odoo + Printful Integration",

    'author': "Jon Mitchell",
    'website': "https://easier.digital/",
    'license': "LGPL-3",
    'category': 'Production',
    'version': '16.0',

    # any module necessary for this one to work correctly
    'depends': ['base', 'web', 'stock', 'product', 'sale', 'website', 'website_sale'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/sale_views.xml',
        'views/website_views.xml',
        'views/printful_views.xml',
        'views/printful_product.xml',
        'data/fetch_product_cron.xml',
    ],

    'installable': True,
    'application': True,
    'auto_install': False,
}
