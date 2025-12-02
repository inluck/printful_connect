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

    # Printful product metadata (for SEO template population)
    printful_brand = fields.Char(
        string='Brand',
        help='Product brand from Printful catalog',
    )
    printful_type = fields.Char(
        string='Printful Product Type',
        help='Product type from Printful (e.g., T-SHIRT, HOODIE)',
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

    def _generate_seo_metadata(self, config=None):
        """
        Generate SEO metadata based on Printful configuration templates.

        Args:
            config: PrintfulPrintful config record (optional)

        Returns:
            dict with website_meta_title, website_meta_description, website_meta_keywords
        """
        self.ensure_one()

        if not config:
            config = self.env['printful.printful'].search([], limit=1)

        if not config or not config.seo_auto_populate:
            return {}

        # Build template variables
        description_short = (self.description_sale or '')[:150]
        if len(self.description_sale or '') > 150:
            description_short += '...'

        template_vars = {
            'product_name': self.name or '',
            'brand': self.printful_brand or '',
            'type': self.printful_type or '',
            'description_short': description_short,
        }

        result = {}

        # Generate meta title
        if config.seo_title_template:
            try:
                result['website_meta_title'] = config.seo_title_template.format(**template_vars).strip()
                # Clean up empty placeholders
                result['website_meta_title'] = result['website_meta_title'].replace(' |  |', ' |').replace('| |', '|').strip(' |')
            except (KeyError, ValueError):
                result['website_meta_title'] = self.name

        # Generate meta description
        if config.seo_description_template:
            try:
                result['website_meta_description'] = config.seo_description_template.format(**template_vars).strip()
                # Truncate to recommended length (155-160 chars)
                if len(result['website_meta_description']) > 160:
                    result['website_meta_description'] = result['website_meta_description'][:157] + '...'
            except (KeyError, ValueError):
                result['website_meta_description'] = description_short

        # Generate meta keywords from brand, type, and product name
        keywords = []
        if self.printful_brand:
            keywords.append(self.printful_brand)
        if self.printful_type:
            keywords.append(self.printful_type.replace('-', ' ').title())
        if self.name:
            # Extract meaningful words from product name
            name_words = [w for w in self.name.split() if len(w) > 3]
            keywords.extend(name_words[:3])
        if keywords:
            result['website_meta_keywords'] = ', '.join(keywords)

        return result


class ProductPublicCategory(models.Model):
    """
    Extension of product.public.category to store Printful category mapping.
    """
    _inherit = 'product.public.category'

    printful_catid = fields.Char(
        string='Printful Category ID',
        help='Printful category identifier for mapping',
    )
