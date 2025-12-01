# -*- coding: utf-8 -*-
import requests
import base64
import json
from tabulate import tabulate
from ratelimit import limits, sleep_and_retry
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class PrintfulPrintful(models.Model):
    _name = 'printful.printful'
    _description = "Printful Configuration"

    name = fields.Char(string="Printful Store")
    token = fields.Char(string="Printful Token")
    size_attribute_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Size Attribute",
    )
    color_attribute_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Color Attribute",
    )
    product_public_category_id = fields.Many2one(
        comodel_name="product.public.category",
        string="Public Category",
    )
    multiple_product_images = fields.Boolean(string="Multiple Product Images")
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        help="Currency used for Printful pricing. Defaults to company currency.",
    )
    default_shipping_country_id = fields.Many2one(
        comodel_name="res.country",
        string="Default Shipping Country",
        help="Default country for shipping estimates. If not set, will try to use customer address.",
    )

    # Relationship to sync queues
    sync_queue_ids = fields.One2many(
        comodel_name='printful.sync.queue',
        inverse_name='printful_config_id',
        string='Sync Queues',
    )

    # ==========================================
    # PUBLIC ACTIONS
    # ==========================================

    def action_get_printful_order(self):
        """Fetch orders from Printful and create sale orders in Odoo."""
        self.ensure_one()
        headers = self._get_auth_headers()

        url = "https://api.printful.com/orders"
        response = self._make_api_request(url, headers)
        data = response.json()

        for order in data.get('result', []):
            self._process_printful_order(order)

    def action_get_all_printful_products(self):
        """Fetch products from all configured stores."""
        for rec in self:
            rec.action_get_printful_products()

    def action_get_printful_products(self):
        """Fetch all products from this store (legacy synchronous method)."""
        self.ensure_one()
        self.action_get_printful_product(self)

    def action_create_sync_queue(self):
        """Create a new sync queue for this store."""
        self.ensure_one()
        queue = self.env['printful.sync.queue'].create({
            'printful_config_id': self.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'printful.sync.queue',
            'res_id': queue.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_get_printful_product(self, printful):
        """
        Legacy synchronous sync method.
        Fetches all products and syncs them in a single transaction.
        For large catalogs, use the queue-based approach instead.
        """
        headers = self._get_auth_headers(printful)
        url = "https://api.printful.com/store/products"
        response = self._make_api_request(url, headers)
        data = response.json()

        for product in data.get('result', []):
            try:
                self._sync_single_product(
                    str(product['id']),
                    config=printful,
                )
            except Exception as e:
                _logger.error("Failed to sync product %s: %s", product.get('name'), str(e))
                continue

    # ==========================================
    # QUEUE-BASED SYNC METHOD
    # ==========================================

    def _sync_single_product(self, printful_product_id, config=None, progress_callback=None):
        """
        Sync a single product from Printful.

        This method implements transaction safety:
        - Uses savepoint for atomic product creation/update
        - Tracks failed variants for reporting
        - Raises on critical failures, continues on partial variant failures

        Args:
            printful_product_id: The Printful product ID to sync
            config: PrintfulPrintful config record (defaults to self)
            progress_callback: Optional callback function(variants_synced, total, colors, sizes)

        Returns:
            product.template record

        Raises:
            UserError: If product fetch fails or all variants fail to sync
        """
        config = config or self
        headers = self._get_auth_headers(config)

        # Fetch product details
        product_url = f"https://api.printful.com/store/products/{printful_product_id}"
        product_response = self._make_api_request(product_url, headers)
        product_data = product_response.json()

        if product_data.get('code') != 200:
            raise UserError(_('Failed to fetch product: %s') % product_data.get('result', 'Unknown error'))

        result = product_data['result']
        sync_product = result['sync_product']
        sync_variants = result.get('sync_variants', [])

        if not sync_variants:
            _logger.warning("No sync variants found for product %s", printful_product_id)
            return None

        _logger.info("Syncing product %s (%s) with %d variants",
                    sync_product.get('name'), printful_product_id, len(sync_variants))

        # Use savepoint to ensure atomic product template creation
        with self.env.cr.savepoint():
            # Create or update product template
            product_template = self._upsert_product_template(sync_product, headers)

        # Calculate lowest price for base price
        lowest_price = min(
            float(sv.get('retail_price', 0))
            for sv in sync_variants
        ) if sync_variants else 0

        # Track attribute lines for the product
        size_attribute_line = None
        color_attribute_line = None

        # Track sync progress and failures
        colors = set()
        sizes = set()
        variants_synced = 0
        failed_variants = []

        # Process each variant
        for idx, sync_variant in enumerate(sync_variants):
            variant_id = sync_variant.get('id', 'unknown')
            try:
                # Each variant sync in its own savepoint for isolation
                with self.env.cr.savepoint():
                    variant_info = self._process_sync_variant(
                        sync_variant,
                        product_template,
                        config,
                        headers,
                        lowest_price,
                    )

                    # Track colors and sizes for statistics
                    if variant_info.get('size'):
                        sizes.add(variant_info['size'])
                    if variant_info.get('color'):
                        colors.add(variant_info['color'])

                    # Update attribute lines reference
                    if variant_info.get('size_line'):
                        size_attribute_line = variant_info['size_line']
                    if variant_info.get('color_line'):
                        color_attribute_line = variant_info['color_line']

                    variants_synced += 1

            except Exception as e:
                _logger.warning("Failed to process variant %s: %s", variant_id, str(e))
                failed_variants.append({
                    'id': variant_id,
                    'name': sync_variant.get('name', 'Unknown'),
                    'error': str(e),
                })
                # Continue processing other variants

            # Report progress (including failed ones in count)
            if progress_callback:
                progress_callback(
                    variants_synced,
                    len(sync_variants),
                    len(colors),
                    len(sizes),
                )

        # Check if sync was successful
        if variants_synced == 0 and len(sync_variants) > 0:
            error_details = '\n'.join([
                f"  - {v['name']}: {v['error']}" for v in failed_variants[:5]
            ])
            raise UserError(_(
                'Failed to sync any variants for product %s.\n\nErrors:\n%s'
            ) % (sync_product.get('name'), error_details))

        # Log partial failures
        if failed_variants:
            _logger.warning(
                "Product %s synced with %d/%d variants. Failed variants: %s",
                sync_product.get('name'),
                variants_synced,
                len(sync_variants),
                [v['id'] for v in failed_variants]
            )

        # Update product template with attribute lines
        if size_attribute_line or color_attribute_line:
            attribute_lines = []
            if size_attribute_line:
                attribute_lines.append((4, size_attribute_line.id))
            if color_attribute_line:
                attribute_lines.append((4, color_attribute_line.id))

            product_template.write({
                'attribute_line_ids': attribute_lines,
            })

        _logger.info("Successfully synced product %s: %d/%d variants",
                    sync_product.get('name'), variants_synced, len(sync_variants))

        return product_template

    # ==========================================
    # PRODUCT TEMPLATE OPERATIONS
    # ==========================================

    def _upsert_product_template(self, sync_product, headers):
        """Create or update a product template from Printful sync_product data."""
        ProductTemplate = self.env['product.template']

        # Download thumbnail
        thumbnail_url = sync_product.get('thumbnail_url')
        image_data = False
        if thumbnail_url:
            try:
                img_response = self._make_api_request(thumbnail_url, headers={})
                image_data = base64.b64encode(img_response.content)
            except Exception as e:
                _logger.warning("Failed to download thumbnail: %s", str(e))

        # Search for existing product
        existing = ProductTemplate.search([
            ('printful_ref', '=', str(sync_product['id']))
        ], limit=1)

        vals = {
            'name': sync_product.get('name', 'Unknown Product'),
            'default_code': str(sync_product.get('external_id', '')),
            'printful_ref': str(sync_product['id']),
            'printful_external_ref': str(sync_product.get('external_id', '')),
        }

        if image_data:
            vals['image_1920'] = image_data

        if existing:
            existing.write(vals)
            return existing
        else:
            return ProductTemplate.create(vals)

    # ==========================================
    # VARIANT PROCESSING
    # ==========================================

    def _process_sync_variant(self, sync_variant, product_template, config, headers, lowest_price):
        """
        Process a single sync variant from Printful.

        Returns dict with:
            - size: size value
            - color: color value
            - size_line: product.template.attribute.line for size
            - color_line: product.template.attribute.line for color
        """
        result = {
            'size': None,
            'color': None,
            'size_line': None,
            'color_line': None,
        }

        # Fetch variant details from Printful catalog
        variant_id = sync_variant.get('variant_id')
        variant_url = f"https://api.printful.com/products/variant/{variant_id}"
        variant_response = self._make_api_request(variant_url, headers={})

        if variant_response.status_code != 200:
            _logger.warning("Failed to fetch variant %s", variant_id)
            return result

        variant_json = variant_response.json()
        variant_data = variant_json['result']['variant']
        variant_product_data = variant_json['result']['product']

        # Extract size and color
        size_value = variant_data.get('size')
        color_value = variant_data.get('color')
        result['size'] = size_value
        result['color'] = color_value

        # Calculate price
        retail_price = float(sync_variant.get('retail_price', 0))
        price_extra = retail_price - float(lowest_price)

        # Process size attribute
        if size_value and config.size_attribute_id:
            size_attr_value = self._get_or_create_attribute_value(
                config.size_attribute_id,
                size_value,
            )
            size_line = self._get_or_create_attribute_line(
                config.size_attribute_id,
                product_template,
                size_attr_value,
            )
            result['size_line'] = size_line

            # Set price extra on the attribute value
            self._update_attribute_value_price(
                size_line,
                size_value,
                product_template,
                price_extra,
            )

        # Process color attribute
        if color_value and config.color_attribute_id:
            color_attr_value = self._get_or_create_attribute_value(
                config.color_attribute_id,
                color_value,
            )
            color_line = self._get_or_create_attribute_line(
                config.color_attribute_id,
                product_template,
                color_attr_value,
            )
            result['color_line'] = color_line

        # Find and update the product variant
        product_variant = self._find_product_variant(
            product_template,
            result['size_line'],
            result['color_line'],
        )

        if product_variant:
            # Get shipping info
            shipping_info = self._get_shipping_info(sync_variant, headers)

            # Get category
            category_ids = self._get_category_ids(
                sync_variant,
                config,
                headers,
            )

            # Get size guide
            size_guide = self._get_size_guide_safe(
                headers,
                sync_variant.get('product', {}).get('product_id'),
            )

            # Update variant
            variant_vals = {
                'list_price': lowest_price,
                'standard_price': float(lowest_price),
                'default_code': sync_variant.get('sku', ''),
                'printful_variant_ref': str(sync_variant.get('external_id', '')),
                'printful_variant_id': str(variant_id),
                'printful_sku': sync_variant.get('sku', ''),
                'printful_currency': sync_variant.get('currency', ''),
                'printful_size': size_value,
                'printful_color': color_value,
                'printful_product': product_template.id,
                'printful_variant_external_ref': str(sync_variant.get('id', '')),
                'printful_product_in_stock': variant_data.get('in_stock', False),
                'description_sale': variant_product_data.get('description', ''),
                'website_published': variant_data.get('in_stock', False),
                'printful_sizeguide': size_guide or '',
                'printful_shipping': shipping_info or '',
            }

            if category_ids:
                variant_vals['public_categ_ids'] = [(4, cid) for cid in category_ids]

            if shipping_info:
                # Store shipping rate in volume field
                try:
                    rate_info = shipping_info.split()[0] if shipping_info else '0'
                    variant_vals['volume'] = float(rate_info) if rate_info.replace('.', '').isdigit() else 0
                except (ValueError, IndexError):
                    pass

            product_variant.write(variant_vals)

        return result

    # ==========================================
    # ATTRIBUTE HELPERS
    # ==========================================

    def _get_or_create_attribute_value(self, attribute, value_name):
        """Get or create an attribute value."""
        if not value_name:
            return None

        AttributeValue = self.env['product.attribute.value']
        existing = AttributeValue.search([
            ('attribute_id', '=', attribute.id),
            ('name', '=', value_name),
        ], limit=1)

        if existing:
            return existing.id

        return AttributeValue.create({
            'name': value_name,
            'attribute_id': attribute.id,
        }).id

    def _get_or_create_attribute_line(self, attribute, product_template, attr_value_id):
        """Get or create a product template attribute line."""
        AttributeLine = self.env['product.template.attribute.line']

        existing = AttributeLine.search([
            ('attribute_id', '=', attribute.id),
            ('product_tmpl_id', '=', product_template.id),
        ], limit=1)

        if existing:
            # Add the value to existing line
            existing.write({
                'value_ids': [(4, attr_value_id)]
            })
            return existing

        return AttributeLine.create({
            'product_tmpl_id': product_template.id,
            'attribute_id': attribute.id,
            'value_ids': [(4, attr_value_id)],
        })

    def _update_attribute_value_price(self, attr_line, value_name, product_template, price_extra):
        """Update price extra on product template attribute value."""
        ptav = self.env['product.template.attribute.value'].search([
            ('attribute_line_id', '=', attr_line.id),
            ('name', '=', value_name),
            ('product_tmpl_id', '=', product_template.id),
        ], limit=1)

        if ptav:
            ptav.write({'price_extra': price_extra})

    def _find_product_variant(self, product_template, size_line, color_line):
        """Find a product variant by its attribute lines."""
        ProductProduct = self.env['product.product']

        domain = [('product_tmpl_id', '=', product_template.id)]

        if size_line and color_line:
            domain.append(('attribute_line_ids', 'in', [size_line.id, color_line.id]))
        elif size_line:
            domain.append(('attribute_line_ids', 'in', [size_line.id]))
        elif color_line:
            domain.append(('attribute_line_ids', 'in', [color_line.id]))

        variants = ProductProduct.search(domain, order='id desc', limit=1)
        return variants[0] if variants else None

    # ==========================================
    # LEGACY ATTRIBUTE HELPERS (for backward compatibility)
    # ==========================================

    def _get_attribute_value(self, attribute_id, value_name):
        """Legacy method for getting attribute values."""
        return self._get_or_create_attribute_value(attribute_id[0], value_name)

    def _get_attribute_line(self, attribute_id, tmpl_id):
        """Legacy method for getting attribute lines."""
        AttributeLine = self.env['product.template.attribute.line']
        line = AttributeLine.search([
            ('attribute_id', '=', attribute_id[0].id),
            ('product_tmpl_id', '=', tmpl_id),
        ], limit=1)
        return line[0] if line else None

    # ==========================================
    # SHIPPING & CATEGORY HELPERS
    # ==========================================

    def _get_shipping_info(self, sync_variant, headers):
        """Get shipping rate information for a variant."""
        try:
            # Use configured shipping country or fallback to company country
            config = self
            country = config.default_shipping_country_id or self.env.company.country_id

            if not country:
                _logger.warning("No shipping country configured. Skipping shipping estimate.")
                return None

            # Get currency code
            currency_code = config.currency_id.name if config.currency_id else self.env.company.currency_id.name

            # Build recipient data with configured/company defaults
            recipient_data = {
                "country_code": country.code,
                "phone": "string"  # Required by API but not used for rate calculation
            }

            # Add state code if available (for US/CA)
            if country.code in ('US', 'CA'):
                # Use first state in country as default
                state = self.env['res.country.state'].search([('country_id', '=', country.id)], limit=1)
                if state:
                    recipient_data["state_code"] = state.code
                    recipient_data["city"] = "City"  # Generic city
                    recipient_data["address1"] = "123 Main St"  # Generic address
                    recipient_data["zip"] = "00000"  # Generic zip

            shipping_data = {
                "recipient": recipient_data,
                "items": [{
                    "variant_id": sync_variant.get('variant_id'),
                    "external_variant_id": sync_variant.get('external_id'),
                    "quantity": 1,
                    "value": sync_variant.get('retail_price', '0')
                }],
                "currency": currency_code,
                "locale": "en_US"
            }

            shipping_response = requests.post(
                "https://api.printful.com/shipping/rates",
                json=shipping_data,
                headers=headers,
            )
            shipping_result = shipping_response.json()

            for rate in shipping_result.get('result', []):
                if rate.get('id') == "STANDARD":
                    min_days = rate.get('minDeliveryDays', 0)
                    max_days = rate.get('maxDeliveryDays', 0)
                    return f"{min_days}-{max_days} Business Days"

        except Exception as e:
            _logger.warning("Failed to get shipping info: %s", str(e))

        return None

    def _get_category_ids(self, sync_variant, config, headers):
        """Get or create category IDs for the variant."""
        category_ids = []

        # Get Printful category
        main_category_id = sync_variant.get('main_category_id')
        if main_category_id:
            try:
                cat_url = f"https://api.printful.com/categories/{main_category_id}"
                cat_response = self._make_api_request(cat_url, headers={})
                cat_data = cat_response.json()

                if cat_data.get('code') == 200:
                    cat_info = cat_data['result']['category']
                    cat_id = self._get_or_create_category(
                        cat_info.get('title'),
                        cat_info.get('image_url'),
                    )
                    if cat_id:
                        category_ids.append(cat_id)
            except Exception as e:
                _logger.warning("Failed to get Printful category: %s", str(e))

        # Add store category if configured
        if config.product_public_category_id:
            category_ids.append(config.product_public_category_id.id)

        return category_ids

    def _get_or_create_category(self, name, image_url):
        """Get or create a product public category."""
        if not name:
            return None

        Category = self.env['product.public.category']
        existing = Category.search([('name', '=', name)], limit=1)

        if existing:
            return existing.id

        # Download image
        image_data = False
        if image_url:
            try:
                img_response = self._make_api_request(image_url, headers={})
                image_data = base64.b64encode(img_response.content)
            except Exception as e:
                _logger.warning("Failed to download category image: %s", str(e))

        vals = {'name': name}
        if image_data:
            vals['image_1920'] = image_data

        return Category.create(vals).id

    # Legacy method kept for backward compatibility
    def _get_category_id(self, name, image_url):
        """Legacy method for getting category ID."""
        return self._get_or_create_category(name, image_url)

    # ==========================================
    # SIZE GUIDE
    # ==========================================

    def _get_size_guide_safe(self, headers, product_id):
        """Safely get size guide HTML, returning empty string on error."""
        if not product_id:
            return ""
        try:
            return self._get_size_guide(headers, product_id)
        except Exception as e:
            _logger.warning("Failed to get size guide: %s", str(e))
            return ""

    def _get_size_guide(self, headers, product_id):
        """Generate HTML size guide from Printful data."""
        url = f"https://api.printful.com/store/products/{product_id}/sizes"
        response = self._make_api_request(url, headers)
        data = response.json()
        product_info = data['result']

        output_str = ""

        for table in product_info.get('size_tables', []):
            table_type = table.get('type', '').replace('_', ' ').title()
            output_str += f"<h2>{table_type} Guide</h2>\n"

            if table.get('image_url'):
                output_str += f"<img src='{table['image_url']}' alt='Guide Image'>\n"
            if table.get('image_description'):
                output_str += f"<div>{table['image_description']}</div>\n"
            if table.get('description'):
                output_str += f"<div>{table['description']}</div>\n"

            # Build table headers
            table_headers = ["Size"]
            available_sizes = product_info.get('available_sizes', [])
            size_data = {size: [] for size in available_sizes}

            for measurement in table.get('measurements', []):
                unit = table.get('unit', '')
                table_headers.append(f"{measurement['type_label']} ({unit})")

                for size_info in measurement.get('values', []):
                    size = size_info.get('size')
                    if size in size_data:
                        if 'min_value' in size_info and 'max_value' in size_info:
                            size_data[size].append(
                                f"{size_info['min_value']} - {size_info['max_value']}"
                            )
                        else:
                            size_data[size].append(size_info.get('value', ''))

            # Build table rows
            table_data = []
            for size in available_sizes:
                row = [size] + size_data.get(size, [])
                table_data.append(row)

            output_str += tabulate(table_data, headers=table_headers, tablefmt='html')
            output_str += "<br>"

        return output_str

    # ==========================================
    # IMAGE HANDLING
    # ==========================================

    def _upsert_product_image(self, name, image_url, product):
        """Create or update a product image."""
        try:
            img_response = self._make_api_request(image_url, headers={})
            image_data = base64.b64encode(img_response.content)
        except Exception as e:
            _logger.warning("Failed to download image %s: %s", name, str(e))
            return

        ProductImage = self.env['product.image']
        existing = ProductImage.search([
            ('name', '=', name),
            ('product_tmpl_id', '=', product.id),
        ], limit=1)

        if existing:
            existing.write({'image_1920': image_data})
        else:
            new_image = ProductImage.create({
                'name': name,
                'product_tmpl_id': product.id,
                'image_1920': image_data,
            })
            product.product_template_image_ids = [(4, new_image.id)]

    # ==========================================
    # ORDER PROCESSING
    # ==========================================

    def _process_printful_order(self, order):
        """Process a single order from Printful."""
        SaleOrder = self.env['sale.order']
        Partner = self.env['res.partner']

        # Check if order already exists
        order_ref = "#PF" + str(order['id'])
        existing = SaleOrder.search([('order_ref', '=', order_ref)], limit=1)
        if existing:
            return existing

        # Create customer
        recipient = order.get('recipient', {})
        customer = Partner.create({
            'name': recipient.get('name') or "Printful Customer",
            'city': recipient.get('city', ''),
            'street': ' '.join(filter(None, [
                recipient.get('address1', ''),
                recipient.get('address2', ''),
                recipient.get('state_code', ''),
            ])),
            'street2': ' '.join(filter(None, [
                recipient.get('country_name', ''),
                recipient.get('state_name', ''),
                recipient.get('country_code', ''),
            ])),
            'zip': recipient.get('zip', ''),
            'email': recipient.get('email', ''),
            'phone': recipient.get('phone', ''),
            'tax_number': str(recipient.get('tax_number', '')),
            'company': recipient.get('company', ''),
        })

        # Create order lines
        lines = []
        for item in order.get('items', []):
            product = self.env['product.product'].search([
                ('printful_variant_ref', '=', str(item.get('external_variant_id')))
            ], limit=1)

            if product:
                lines.append((0, 0, {
                    'name': product.display_name,
                    'product_id': product.id,
                    'price_unit': item.get('price', 0),
                    'product_uom_qty': item.get('quantity', 1),
                    'tax_id': None,
                }))

        # Create sale order
        costs = order.get('costs', {})
        pricing = order.get('pricing_breakdown', [{}])[0]

        so_vals = {
            'order_ref': order_ref,
            'order_external_ref': order.get('external_id', ''),
            'order_store_ref': order.get('store', ''),
            'order_shipping': order.get('shipping', ''),
            'order_shipping_service_name': order.get('shipping_service_name', ''),
            'printful_order_notes': order.get('notes', ''),
            'order_currency': costs.get('currency', ''),
            'order_subtotal': costs.get('subtotal', 0),
            'order_discount': costs.get('discount', 0),
            'shipping': costs.get('shipping', 0),
            'order_digitization': costs.get('digitization', 0),
            'order_additional_fee': costs.get('additional_fee', 0),
            'order_fulfillment_fee': costs.get('fulfillment_fee', 0),
            'order_retail_delivery_fee': costs.get('retail_delivery_fee', 0),
            'order_tax': costs.get('tax', 0),
            'order_vat': costs.get('vat', 0),
            'order_total': costs.get('total', 0),
            'printful_dashboard_url': order.get('dashboard_url', ''),
            'customer_pays': pricing.get('customer_pays', 0),
            'printful_price': pricing.get('printful_price', 0),
            'profit': pricing.get('profit', 0),
            'currency_symbol': pricing.get('currency_symbol', ''),
            'partner_id': customer.id,
            'order_line': lines,
        }

        return SaleOrder.create(so_vals)

    # ==========================================
    # API HELPERS
    # ==========================================

    def _get_auth_headers(self, config=None):
        """Get authentication headers for Printful API."""
        config = config or self
        if not config.token:
            raise UserError(_('Please configure a Printful API token.'))
        return {'Authorization': 'Bearer ' + config.token}

    @sleep_and_retry
    @limits(calls=30, period=60)
    def _make_api_request(self, url, headers):
        """Make a rate-limited API request."""
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 429:
            raise requests.exceptions.RequestException('Rate limit exceeded')
        response.raise_for_status()
        return response
