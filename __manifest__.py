# -*- coding: utf-8 -*-
{
    'name': "PrintFul",
    'description': """
    """,

    'author': "Sajjad",
    'website': "https://sajjad.hussain/",
    'license': "LGPL-3",
    'category': 'Production',
    'version': '16.0',

    # any module necessary for this one to work correctly
    'depends': ['base', 'web', 'stock', 'product', 'sale'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/sale_views.xml',
        'views/printful_views.xml',
        'views/printful_product.xml',
        'data/fetch_product_cron.xml',
    ],

    'installable': True,
    'application': True,
    'auto_install': False,
}
