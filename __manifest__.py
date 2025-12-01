# -*- coding: utf-8 -*-
{
    'name': "Printful Connect",
    'summary': "Sync products and orders with Printful print-on-demand service",
    'description': """
Printful Connect - Odoo 18 Integration
======================================

This module provides bi-directional integration between Odoo and Printful:

* **Product Sync Queue** - Visual queue system showing products waiting to sync
* **Variant Tracking** - Track sync progress for each size/color combination
* **Order Push** - Send orders to Printful for fulfillment
* **Order Pull** - Import orders from Printful into Odoo
* **Size Guides** - Automatic size guide generation for website

Features:
---------
* Kanban view of sync queue with progress tracking
* Batch processing to prevent timeouts
* Error recovery and retry functionality
* Real-time sync status updates
    """,

    'author': "Jon Mitchell",
    'website': "https://easier.digital/",
    'license': "LGPL-3",
    'category': 'Inventory/Inventory',
    'version': '18.0.1.0.0',

    'depends': ['base', 'web', 'stock', 'product', 'sale', 'website', 'website_sale'],

    'data': [
        'security/ir.model.access.csv',
        'views/printful_sync_queue_views.xml',
        'views/product_views.xml',
        'views/sale_views.xml',
        'views/website_views.xml',
        'views/printful_views.xml',
        'data/fetch_product_cron.xml',
    ],

    'assets': {
        'web.assets_backend': [
            'printful_connect/static/src/css/sync_queue.css',
        ],
    },

    'installable': True,
    'application': True,
    'auto_install': False,
}
