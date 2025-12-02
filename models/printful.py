# -*- coding: utf-8 -*-
import requests
import base64
import json
import time
import threading
from datetime import datetime, timedelta
from html import escape as html_escape
from tabulate import tabulate
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

# API Configuration
PRINTFUL_API_V1_BASE = "https://api.printful.com"
PRINTFUL_API_V2_BASE = "https://api.printful.com/v2"

# Rate limiting configuration for v2 API (leaky bucket)
# 120 requests per minute with gradual refill
RATE_LIMIT_BUCKET_SIZE = 120
RATE_LIMIT_REFILL_RATE = 2  # requests per second


class PrintfulRateLimiter:
    """
    Leaky bucket rate limiter for Printful API v2.

    The v2 API uses a leaky bucket algorithm where:
    - Bucket holds up to 120 requests
    - Refills at 2 requests per second
    - Headers: X-Ratelimit-Limit, X-Ratelimit-Remaining, X-Ratelimit-Reset
    """
    _instances = {}
    _lock = threading.Lock()

    def __new__(cls, token):
        """Singleton per API token to share rate limit across all requests."""
        with cls._lock:
            if token not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[token] = instance
            return cls._instances[token]

    def __init__(self, token):
        if self._initialized:
            return
        self._initialized = True
        self._token = token
        self._bucket = RATE_LIMIT_BUCKET_SIZE
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def _refill(self):
        """Refill the bucket based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        refill_amount = elapsed * RATE_LIMIT_REFILL_RATE
        self._bucket = min(RATE_LIMIT_BUCKET_SIZE, self._bucket + refill_amount)
        self._last_refill = now

    def acquire(self, timeout=60):
        """
        Acquire permission to make a request.
        Blocks until a slot is available or timeout is reached.

        Returns:
            True if acquired, raises TimeoutError otherwise
        """
        start_time = time.time()

        while True:
            with self._lock:
                self._refill()
                if self._bucket >= 1:
                    self._bucket -= 1
                    return True

            # Check timeout
            if time.time() - start_time > timeout:
                raise TimeoutError("Rate limit timeout - too many requests")

            # Wait a bit before retrying
            time.sleep(0.5)

    def update_from_headers(self, headers):
        """
        Update bucket state from API response headers.

        Args:
            headers: Response headers containing rate limit info
        """
        remaining = headers.get('X-Ratelimit-Remaining')
        if remaining is not None:
            with self._lock:
                try:
                    self._bucket = int(remaining)
                except (ValueError, TypeError):
                    pass


class PrintfulPrintful(models.Model):
    _name = 'printful.printful'
    _description = "Printful Configuration"

    name = fields.Char(string="Printful Store")
    token = fields.Char(string="Printful Token", password=True)
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

    # API Version Selection
    api_version = fields.Selection([
        ('v1', 'API v1 (Legacy)'),
        ('v2', 'API v2 (Recommended)'),
    ], string="API Version", default='v2',
        help="Select which Printful API version to use. v2 is recommended for new features.")

    # Relationship to sync queues
    sync_queue_ids = fields.One2many(
        comodel_name='printful.sync.queue',
        inverse_name='printful_config_id',
        string='Sync Queues',
    )

    # Shipping method configuration
    shipping_method_ids = fields.One2many(
        comodel_name='printful.shipping.method',
        inverse_name='printful_config_id',
        string='Shipping Methods',
    )
    default_shipping_method_id = fields.Many2one(
        comodel_name='printful.shipping.method',
        string="Default Shipping Method",
        compute='_compute_default_shipping_method',
        help="Default shipping method used for order pushes",
    )

    # Default addresses for shipping estimates
    default_address_ids = fields.One2many(
        comodel_name='printful.default.address',
        inverse_name='printful_config_id',
        string='Default Addresses',
    )
    primary_default_address_id = fields.Many2one(
        comodel_name='printful.default.address',
        string="Primary Default Address",
        compute='_compute_primary_address',
        help="Primary address used for shipping estimates during product sync",
    )

    # Webhook configuration
    webhook_ids = fields.One2many(
        comodel_name='printful.webhook',
        inverse_name='printful_config_id',
        string='Webhooks',
    )

    # Shipping rate cache TTL
    shipping_cache_ttl = fields.Integer(
        string="Shipping Cache TTL (minutes)",
        default=60,
        help="How long to cache shipping rate calculations (in minutes)",
    )

    # Auto-fulfillment settings
    auto_fulfill_orders = fields.Boolean(
        string="Auto-fulfill Orders",
        default=False,
        help="Automatically push orders to Printful when they are confirmed. "
             "Orders containing Printful products will be sent for fulfillment "
             "without requiring manual intervention.",
    )

    # Packing slip / Custom branding settings
    packing_slip_email = fields.Char(
        string="Packing Slip Email",
        help="Store email address to display on packing slips",
    )
    packing_slip_phone = fields.Char(
        string="Packing Slip Phone",
        help="Store phone number to display on packing slips",
    )
    packing_slip_message = fields.Text(
        string="Packing Slip Message",
        help="Custom message to print on packing slips (max 1024 characters)",
    )
    packing_slip_logo_url = fields.Char(
        string="Packing Slip Logo URL",
        help="URL to your store logo for packing slips. Must be publicly accessible HTTPS URL. "
             "Recommended size: 600x100 pixels, PNG or JPG format.",
    )
    packing_slip_store_name = fields.Char(
        string="Packing Slip Store Name",
        help="Custom store name to display on packing slips. Leave empty to use Printful store name.",
    )
    gift_message_default = fields.Char(
        string="Default Gift Message",
        default="Thank you for your purchase!",
        help="Default message included with orders as a gift note",
    )

    # SEO Settings for Product Sync
    seo_auto_populate = fields.Boolean(
        string="Auto-populate SEO Fields",
        default=True,
        help="Automatically populate website meta title, description, and keywords "
             "when syncing products from Printful",
    )
    seo_title_template = fields.Char(
        string="SEO Title Template",
        default="{product_name} | {brand}",
        help="Template for meta title. Available variables: {product_name}, {brand}, {type}",
    )
    seo_description_template = fields.Text(
        string="SEO Description Template",
        default="Shop {product_name} by {brand}. {description_short}",
        help="Template for meta description. Available variables: {product_name}, {brand}, "
             "{type}, {description_short}",
    )

    @api.depends('shipping_method_ids', 'shipping_method_ids.is_default')
    def _compute_default_shipping_method(self):
        """Get the default shipping method for this configuration."""
        for record in self:
            default = record.shipping_method_ids.filtered(lambda m: m.is_default)[:1]
            record.default_shipping_method_id = default.id if default else False

    @api.depends('default_address_ids', 'default_address_ids.is_primary')
    def _compute_primary_address(self):
        """Get the primary default address for this configuration."""
        for record in self:
            primary = record.default_address_ids.filtered(
                lambda a: a.is_primary and a.active
            )[:1]
            record.primary_default_address_id = primary.id if primary else False

    def _get_api_base_url(self):
        """Get the appropriate API base URL based on version setting."""
        self.ensure_one()
        if self.api_version == 'v2':
            return PRINTFUL_API_V2_BASE
        return PRINTFUL_API_V1_BASE

    def _get_rate_limiter(self):
        """Get or create a rate limiter for this configuration's token."""
        self.ensure_one()
        if not self.token:
            raise UserError(_('Please configure a Printful API token.'))
        return PrintfulRateLimiter(self.token)

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

        # Validate input
        if not printful_product_id:
            raise UserError(_('Product ID is required for synchronization'))

        # Validate product ID format (should be numeric)
        try:
            int(printful_product_id)
        except (ValueError, TypeError):
            raise UserError(_(
                'Invalid product ID format: %s. Product ID must be numeric.'
            ) % printful_product_id)

        # Validate configuration
        if not config.size_attribute_id:
            _logger.warning(
                "Size attribute not configured for store '%s'. "
                "Size variants will not be created.",
                config.name
            )
        if not config.color_attribute_id:
            _logger.warning(
                "Color attribute not configured for store '%s'. "
                "Color variants will not be created.",
                config.name
            )

        # Initialize caches for this sync session to avoid redundant API calls
        size_guide_cache = {}
        category_cache = {}

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
                        size_guide_cache,
                        category_cache,
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

        # Generate and apply SEO metadata if enabled
        try:
            seo_vals = product_template._generate_seo_metadata(config)
            if seo_vals:
                product_template.write(seo_vals)
                _logger.debug(
                    "Generated SEO metadata for product %s: %s",
                    product_template.name, list(seo_vals.keys())
                )
        except Exception as e:
            _logger.warning(
                "Failed to generate SEO metadata for product %s: %s",
                product_template.name, str(e)
            )

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

    def _process_sync_variant(self, sync_variant, product_template, config, headers, lowest_price,
                              size_guide_cache=None, category_cache=None):
        """
        Process a single sync variant from Printful.

        Args:
            size_guide_cache: Dict to cache size guides by product_id
            category_cache: Dict to cache categories by category_id

        Returns dict with:
            - size: size value
            - color: color value
            - size_line: product.template.attribute.line for size
            - color_line: product.template.attribute.line for color
        """
        # Initialize caches if not provided
        if size_guide_cache is None:
            size_guide_cache = {}
        if category_cache is None:
            category_cache = {}
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

        # Validate API response before accessing result
        if variant_json.get('code') != 200:
            _logger.warning(
                "API error fetching variant %s: %s",
                variant_id, variant_json.get('error', {}).get('message', 'Unknown error')
            )
            return result

        if 'result' not in variant_json or 'variant' not in variant_json.get('result', {}):
            _logger.warning("Unexpected API response format for variant %s", variant_id)
            return result

        variant_data = variant_json['result']['variant']
        variant_product_data = variant_json['result'].get('product', {})

        # Update product template with brand and type for SEO (only once per template)
        brand = variant_product_data.get('brand', '')
        product_type = variant_product_data.get('type', '')
        if (brand or product_type) and not product_template.printful_brand:
            product_template.write({
                'printful_brand': brand,
                'printful_type': product_type,
            })

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
            size_value=size_value,
            color_value=color_value,
        )

        if product_variant:
            # Get shipping info
            shipping_info = self._get_shipping_info(sync_variant, headers)

            # Get category (with caching)
            category_ids = self._get_category_ids(
                sync_variant,
                config,
                headers,
                category_cache,
            )

            # Get size guide (with caching)
            product_id = sync_variant.get('product', {}).get('product_id')
            size_guide = self._get_size_guide_safe(
                headers,
                product_id,
                size_guide_cache,
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

    def _find_product_variant(self, product_template, size_line, color_line, size_value=None, color_value=None):
        """
        Find a product variant by its attribute values.

        Args:
            product_template: The product template record
            size_line: product.template.attribute.line for size (optional)
            color_line: product.template.attribute.line for color (optional)
            size_value: The size value name (e.g., "M", "Large") for precise matching
            color_value: The color value name (e.g., "Black", "Navy") for precise matching

        Returns:
            product.product record or None if not found
        """
        ProductProduct = self.env['product.product']
        PTAV = self.env['product.template.attribute.value']

        # Get all variants for this template
        variants = ProductProduct.search([
            ('product_tmpl_id', '=', product_template.id)
        ])

        if not variants:
            return None

        # Build the expected attribute value IDs
        expected_ptav_ids = set()

        # Find the PTAV for size
        if size_line and size_value:
            size_ptav = PTAV.search([
                ('attribute_line_id', '=', size_line.id),
                ('product_tmpl_id', '=', product_template.id),
                ('name', '=', size_value),
            ], limit=1)
            if size_ptav:
                expected_ptav_ids.add(size_ptav.id)

        # Find the PTAV for color
        if color_line and color_value:
            color_ptav = PTAV.search([
                ('attribute_line_id', '=', color_line.id),
                ('product_tmpl_id', '=', product_template.id),
                ('name', '=', color_value),
            ], limit=1)
            if color_ptav:
                expected_ptav_ids.add(color_ptav.id)

        # If no attribute values to match, return the first variant (single variant product)
        if not expected_ptav_ids:
            return variants[0] if variants else None

        # Find variant with exact attribute value combination
        for variant in variants:
            variant_ptav_ids = set(variant.product_template_attribute_value_ids.ids)
            # Check if this variant has exactly the expected attribute values
            if expected_ptav_ids.issubset(variant_ptav_ids):
                # For exact match, check the variant has the same number of relevant attributes
                if len(variant_ptav_ids) == len(expected_ptav_ids):
                    return variant
                # Or if variant has more attributes, it still matches our criteria
                return variant

        # Fallback: return first variant if no exact match
        _logger.warning(
            "Could not find exact variant match for template %s with size=%s, color=%s. "
            "Using first available variant.",
            product_template.name, size_value, color_value
        )
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
        """
        Get shipping rate information for a variant.

        Uses the configured primary default address for accurate shipping estimates.
        If no default address is configured, falls back to the legacy behavior
        with a warning.
        """
        try:
            config = self

            # Try to use configured default address first
            if config.primary_default_address_id:
                recipient_data = config.primary_default_address_id.get_recipient_data()
                _logger.debug(
                    "Using configured default address for shipping estimate: %s",
                    config.primary_default_address_id.name
                )
            else:
                # Fallback to legacy behavior with warning
                country = config.default_shipping_country_id or self.env.company.country_id

                if not country:
                    _logger.warning(
                        "No shipping address configured. "
                        "Please set up a default address in Printful configuration "
                        "for accurate shipping estimates."
                    )
                    return None

                _logger.warning(
                    "Using legacy generic address for shipping estimates. "
                    "Configure a default address in Printful settings for accurate rates."
                )

                recipient_data = {
                    "country_code": country.code,
                    "phone": "0000000000"
                }

                # Add state code if available (for US/CA)
                if country.code in ('US', 'CA'):
                    state = self.env['res.country.state'].search(
                        [('country_id', '=', country.id)], limit=1
                    )
                    if state:
                        recipient_data["state_code"] = state.code
                        recipient_data["city"] = "Anytown"
                        recipient_data["address1"] = "123 Default St"
                        recipient_data["zip"] = "10001" if country.code == 'US' else "A1A 1A1"

            # Get currency code
            currency_code = config.currency_id.name if config.currency_id else self.env.company.currency_id.name

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

            shipping_response = self._make_api_post_request(
                f"{PRINTFUL_API_V1_BASE}/shipping/rates",
                headers,
                shipping_data,
            )
            shipping_result = shipping_response.json()

            if shipping_result.get('code') != 200:
                _logger.warning("Shipping API error: %s", shipping_result.get('error', {}))
                return None

            # Get configured default shipping method or fall back to STANDARD
            target_method = 'STANDARD'
            if config.default_shipping_method_id:
                target_method = config.default_shipping_method_id.printful_method_id

            for rate in shipping_result.get('result', []):
                if rate.get('id') == target_method:
                    min_days = rate.get('minDeliveryDays', 0)
                    max_days = rate.get('maxDeliveryDays', 0)
                    return f"{min_days}-{max_days} Business Days"

            # If target method not found, use first available
            if shipping_result.get('result'):
                rate = shipping_result['result'][0]
                min_days = rate.get('minDeliveryDays', 0)
                max_days = rate.get('maxDeliveryDays', 0)
                return f"{min_days}-{max_days} Business Days ({rate.get('id', 'Unknown')})"

        except Exception as e:
            _logger.warning("Failed to get shipping info: %s", str(e))

        return None

    def _get_category_ids(self, sync_variant, config, headers, category_cache=None):
        """Get or create category IDs for the variant (with caching)."""
        if category_cache is None:
            category_cache = {}

        category_ids = []

        # Get Printful category (use cache to avoid repeated API calls)
        main_category_id = sync_variant.get('main_category_id')
        if main_category_id:
            # Check cache first
            if main_category_id in category_cache:
                cat_id = category_cache[main_category_id]
                if cat_id:
                    category_ids.append(cat_id)
            else:
                # Fetch from API and cache result
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
                        category_cache[main_category_id] = cat_id
                        if cat_id:
                            category_ids.append(cat_id)
                    else:
                        category_cache[main_category_id] = None
                except Exception as e:
                    _logger.warning("Failed to get Printful category: %s", str(e))
                    category_cache[main_category_id] = None

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

    def _get_size_guide_safe(self, headers, product_id, size_guide_cache=None):
        """Safely get size guide HTML (with caching), returning empty string on error."""
        if not product_id:
            return ""

        if size_guide_cache is None:
            size_guide_cache = {}

        # Check cache first
        if product_id in size_guide_cache:
            return size_guide_cache[product_id]

        # Fetch and cache the size guide
        try:
            size_guide = self._get_size_guide(headers, product_id)
            size_guide_cache[product_id] = size_guide
            return size_guide
        except Exception as e:
            _logger.warning("Failed to get size guide for product %s: %s", product_id, str(e))
            size_guide_cache[product_id] = ""
            return ""

    def _get_size_guide(self, headers, product_id):
        """Generate HTML size guide from Printful data."""
        url = f"https://api.printful.com/store/products/{product_id}/sizes"
        response = self._make_api_request(url, headers)
        data = response.json()
        product_info = data['result']

        output_str = ""

        for table in product_info.get('size_tables', []):
            # Escape all external content to prevent XSS
            table_type = html_escape(table.get('type', '').replace('_', ' ').title())
            output_str += f"<h2>{table_type} Guide</h2>\n"

            if table.get('image_url'):
                # Validate and escape URL
                image_url = html_escape(table['image_url'])
                output_str += f"<img src='{image_url}' alt='Guide Image'>\n"
            if table.get('image_description'):
                desc = html_escape(table['image_description'])
                output_str += f"<div>{desc}</div>\n"
            if table.get('description'):
                desc = html_escape(table['description'])
                output_str += f"<div>{desc}</div>\n"

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

        # Find or create customer (deduplicate by email)
        recipient = order.get('recipient', {})
        customer_email = recipient.get('email', '').strip().lower()
        customer = None

        # Try to find existing customer by email first
        if customer_email:
            customer = Partner.search([
                ('email', '=ilike', customer_email),
                '|', ('company_id', '=', False), ('company_id', '=', self.env.company.id)
            ], limit=1)

        # If no existing customer found, create a new one
        if not customer:
            customer_vals = {
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
            }
            customer = Partner.create(customer_vals)
            _logger.info("Created new customer '%s' for Printful order %s",
                        customer.name, order_ref)
        else:
            _logger.info("Using existing customer '%s' (ID: %d) for Printful order %s",
                        customer.name, customer.id, order_ref)

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
        return {
            'Authorization': 'Bearer ' + config.token,
            'Content-Type': 'application/json',
        }

    def _make_api_request(self, url, headers, timeout=30, max_retries=3):
        """
        Make a rate-limited API GET request using leaky bucket algorithm.

        Args:
            url: API endpoint URL
            headers: Request headers (including auth)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts for rate limiting (default: 3)

        Returns:
            requests.Response object

        Raises:
            requests.exceptions.HTTPError: If rate limit retries exhausted or other HTTP error
        """
        config = self
        retry_count = 0

        while True:
            if config.token:
                limiter = config._get_rate_limiter()
                limiter.acquire()

            response = requests.get(url, headers=headers, timeout=timeout)

            # Update rate limiter from response headers
            if config.token:
                limiter.update_from_headers(response.headers)

            if response.status_code == 429:
                retry_count += 1
                if retry_count > max_retries:
                    _logger.error(
                        "Rate limit retries exhausted (%d/%d) for URL: %s",
                        retry_count, max_retries, url
                    )
                    response.raise_for_status()  # Will raise HTTPError

                # Rate limited - wait and retry
                retry_after = int(response.headers.get('Retry-After', 5))
                _logger.warning(
                    "Rate limited. Waiting %d seconds before retry (%d/%d).",
                    retry_after, retry_count, max_retries
                )
                time.sleep(retry_after)
                continue  # Retry the loop

            response.raise_for_status()
            return response

    def _make_api_post_request(self, url, headers, data, timeout=30, max_retries=3):
        """
        Make a rate-limited API POST request using leaky bucket algorithm.

        Args:
            url: API endpoint URL
            headers: Request headers (including auth)
            data: JSON-serializable request body
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts for rate limiting (default: 3)

        Returns:
            requests.Response object

        Raises:
            requests.exceptions.HTTPError: If rate limit retries exhausted or other HTTP error
        """
        config = self
        retry_count = 0

        while True:
            if config.token:
                limiter = config._get_rate_limiter()
                limiter.acquire()

            response = requests.post(url, headers=headers, json=data, timeout=timeout)

            # Update rate limiter from response headers
            if config.token:
                limiter.update_from_headers(response.headers)

            if response.status_code == 429:
                retry_count += 1
                if retry_count > max_retries:
                    _logger.error(
                        "Rate limit retries exhausted (%d/%d) for POST URL: %s",
                        retry_count, max_retries, url
                    )
                    response.raise_for_status()  # Will raise HTTPError

                # Rate limited - wait and retry
                retry_after = int(response.headers.get('Retry-After', 5))
                _logger.warning(
                    "Rate limited. Waiting %d seconds before retry (%d/%d).",
                    retry_after, retry_count, max_retries
                )
                time.sleep(retry_after)
                continue  # Retry the loop

            response.raise_for_status()
            return response

    # ==========================================
    # V2 SHIPPING RATE METHODS
    # ==========================================

    def _get_shipping_rates_v2(self, country_code, state_code=None, zip_code=None, items=None):
        """
        Get shipping rates using v2 API with real customer address.

        Args:
            country_code: Destination country code (e.g., "US")
            state_code: Destination state code (e.g., "CA")
            zip_code: Destination ZIP/postal code
            items: List of dicts with variant_id and quantity

        Returns:
            List of shipping rate options
        """
        self.ensure_one()
        headers = self._get_auth_headers()

        if not items:
            return []

        # Build recipient data
        recipient = {
            "country_code": country_code,
        }
        if state_code:
            recipient["state_code"] = state_code
        if zip_code:
            recipient["zip"] = zip_code

        # Build items list
        api_items = []
        for item in items:
            variant_id = item.get('variant_id')
            quantity = item.get('quantity', 1)

            # Look up variant to get Printful variant ID
            if isinstance(variant_id, str) and not variant_id.isdigit():
                # It's a variant ref, look it up
                product = self.env['product.product'].search([
                    ('printful_variant_ref', '=', variant_id)
                ], limit=1)
                if product and product.printful_variant_id:
                    variant_id = product.printful_variant_id

            if variant_id:
                api_items.append({
                    "variant_id": int(variant_id) if str(variant_id).isdigit() else variant_id,
                    "quantity": int(quantity),
                })

        if not api_items:
            return []

        # Get currency
        currency_code = self.currency_id.name if self.currency_id else 'USD'

        # Build request payload
        payload = {
            "recipient": recipient,
            "items": api_items,
            "currency": currency_code,
            "locale": "en_US",
        }

        # Make API request (use v1 endpoint as shipping/rates may not be in v2 yet)
        url = f"{PRINTFUL_API_V1_BASE}/shipping/rates"
        try:
            response = self._make_api_post_request(url, headers, payload)
            result = response.json()

            if result.get('code') != 200:
                _logger.warning("Shipping rates API error: %s", result)
                return []

            rates = []
            for rate_data in result.get('result', []):
                rate = {
                    'id': rate_data.get('id'),
                    'name': rate_data.get('name'),
                    'rate': float(rate_data.get('rate', 0)),
                    'currency': rate_data.get('currency', currency_code),
                    'min_delivery_days': rate_data.get('minDeliveryDays'),
                    'max_delivery_days': rate_data.get('maxDeliveryDays'),
                }

                # Apply shipping method markup if configured
                shipping_method = self.shipping_method_ids.filtered(
                    lambda m: m.printful_method_id == rate_data.get('id')
                )[:1]
                if shipping_method:
                    rate['rate'] = shipping_method.calculate_price(rate['rate'])
                    rate['name'] = shipping_method.name

                rates.append(rate)

            return rates

        except Exception as e:
            _logger.exception("Failed to get shipping rates")
            return []

    def _get_cached_or_fresh_rates(self, country_code, state_code, zip_code, items):
        """
        Get shipping rates with caching support.

        Args:
            country_code: Destination country
            state_code: Destination state
            zip_code: Destination ZIP
            items: List of variant items

        Returns:
            List of shipping rate options
        """
        self.ensure_one()
        ShippingRateCache = self.env['printful.shipping.rate']

        # Build cache key
        variant_data = json.dumps(sorted(items, key=lambda x: x.get('variant_id', '')))

        # Check cache - collect all cached rates for this request
        cached_rates = []
        all_methods_cached = True

        for method in self.shipping_method_ids:
            cached = ShippingRateCache.get_cached_rate(
                country_code, state_code, zip_code, variant_data, method.printful_method_id
            )
            if cached:
                _logger.debug("Using cached shipping rate for %s", method.printful_method_id)
                cached_rates.append({
                    'id': cached.printful_method,
                    'name': method.name,
                    'rate': cached.rate,
                    'currency': cached.currency,
                    'min_delivery_days': cached.min_delivery_days,
                    'max_delivery_days': cached.max_delivery_days,
                })
            else:
                all_methods_cached = False

        # Return cached rates if we have valid cache for all configured methods
        if cached_rates and all_methods_cached:
            _logger.debug("Returning %d cached shipping rates", len(cached_rates))
            return cached_rates

        # Get fresh rates from API
        rates = self._get_shipping_rates_v2(country_code, state_code, zip_code, items)

        # Cache the results
        cache_expires = fields.Datetime.now() + timedelta(minutes=self.shipping_cache_ttl)
        for rate in rates:
            # Check if this rate is already cached (avoid duplicates)
            existing_cache = ShippingRateCache.search([
                ('country_code', '=', country_code),
                ('state_code', '=', state_code or False),
                ('zip_code', '=', zip_code or False),
                ('product_variant_ids', '=', variant_data),
                ('printful_method', '=', rate['id']),
                ('expires_at', '>', fields.Datetime.now()),
            ], limit=1)

            if not existing_cache:
                ShippingRateCache.create({
                    'country_code': country_code,
                    'state_code': state_code or False,
                    'zip_code': zip_code or False,
                    'product_variant_ids': variant_data,
                    'printful_method': rate['id'],
                    'rate': rate['rate'],
                    'currency': rate['currency'],
                    'min_delivery_days': rate.get('min_delivery_days'),
                    'max_delivery_days': rate.get('max_delivery_days'),
                    'printful_config_id': self.id,
                    'expires_at': cache_expires,
                })

        return rates
