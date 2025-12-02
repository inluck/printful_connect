# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class PrintfulShippingMethod(models.Model):
    """
    Configuration model for mapping Odoo delivery carriers to Printful shipping methods.
    """
    _name = 'printful.shipping.method'
    _description = 'Printful Shipping Method Mapping'
    _order = 'sequence, id'

    name = fields.Char(
        string="Name",
        required=True,
        help="Display name for this shipping method",
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
        help="Determines the order of display",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )

    # Printful shipping method identifier
    printful_method_id = fields.Selection([
        ('STANDARD', 'Standard Shipping'),
        ('STANDARD_HEAVY', 'Standard Heavy Shipping'),
        ('EXPRESS', 'Express Shipping'),
        ('EXPRESS_HEAVY', 'Express Heavy Shipping'),
        ('OVERNIGHT', 'Overnight Shipping'),
        ('PRINTFUL_FAST', 'Printful Fast'),
    ], string="Printful Method", required=True, default='STANDARD')

    # Optional mapping to Odoo delivery carrier
    delivery_carrier_id = fields.Many2one(
        comodel_name='delivery.carrier',
        string="Odoo Delivery Carrier",
        help="Map this Printful method to an Odoo delivery carrier for automatic selection",
        ondelete='set null',
    )

    # Configuration
    printful_config_id = fields.Many2one(
        comodel_name='printful.printful',
        string="Printful Configuration",
        required=True,
        ondelete='cascade',
    )
    is_default = fields.Boolean(
        string="Default Method",
        default=False,
        help="Use this method when no specific mapping is found",
    )

    # Pricing
    estimated_min_days = fields.Integer(
        string="Min Delivery Days",
        help="Minimum estimated delivery days (for display purposes)",
    )
    estimated_max_days = fields.Integer(
        string="Max Delivery Days",
        help="Maximum estimated delivery days (for display purposes)",
    )
    fixed_surcharge = fields.Float(
        string="Fixed Surcharge",
        default=0.0,
        help="Additional fixed amount to add to Printful shipping cost",
    )
    percentage_markup = fields.Float(
        string="Markup %",
        default=0.0,
        help="Percentage markup on Printful shipping cost (e.g., 10 for 10%)",
    )

    @api.constrains('is_default', 'printful_config_id')
    def _check_single_default(self):
        """Ensure only one default method per configuration."""
        for record in self:
            if record.is_default:
                other_defaults = self.search([
                    ('printful_config_id', '=', record.printful_config_id.id),
                    ('is_default', '=', True),
                    ('id', '!=', record.id),
                ])
                if other_defaults:
                    raise UserError(_(
                        'Only one shipping method can be set as default per Printful configuration. '
                        'Please uncheck the default option on "%s" first.'
                    ) % other_defaults[0].name)

    def calculate_price(self, base_price):
        """
        Calculate final shipping price with markup and surcharge.

        Args:
            base_price: The base price from Printful API

        Returns:
            Final price after applying markup and surcharge (minimum 0.00)
        """
        self.ensure_one()

        # Validate base_price - treat negative/invalid as zero with warning
        try:
            price = float(base_price)
            if price < 0:
                _logger.warning(
                    "Shipping method %s received negative base_price %.2f, treating as 0",
                    self.name, price
                )
                price = 0.0
        except (ValueError, TypeError):
            _logger.warning(
                "Shipping method %s received invalid base_price %r, treating as 0",
                self.name, base_price
            )
            price = 0.0

        # Apply percentage markup (clamp to prevent extreme negative results)
        if self.percentage_markup:
            # Prevent markup from creating negative price (min multiplier is 0)
            multiplier = max(0, 1 + self.percentage_markup / 100)
            price *= multiplier

        # Apply fixed surcharge
        if self.fixed_surcharge:
            price += self.fixed_surcharge

        # Ensure final price is never negative
        final_price = max(0.0, round(price, 2))

        if final_price == 0 and (base_price or self.fixed_surcharge):
            _logger.warning(
                "Shipping method %s calculated zero price from base=%.2f, "
                "markup=%.1f%%, surcharge=%.2f. Check configuration.",
                self.name, float(base_price or 0),
                self.percentage_markup or 0, self.fixed_surcharge or 0
            )

        return final_price


class PrintfulShippingRate(models.Model):
    """
    Cache model for storing shipping rate calculations.
    This helps reduce API calls for repeated rate requests.
    """
    _name = 'printful.shipping.rate'
    _description = 'Printful Shipping Rate Cache'
    _order = 'create_date desc'

    # Cache key components
    country_code = fields.Char(string="Country Code", required=True, index=True)
    state_code = fields.Char(string="State Code", index=True)
    zip_code = fields.Char(string="ZIP Code", index=True)
    product_variant_ids = fields.Char(
        string="Product Variants",
        help="JSON-encoded list of variant IDs and quantities",
    )

    # Rate data
    shipping_method_id = fields.Many2one(
        comodel_name='printful.shipping.method',
        string="Shipping Method",
        ondelete='cascade',
    )
    printful_method = fields.Char(string="Printful Method ID")
    rate = fields.Float(string="Shipping Rate")
    currency = fields.Char(string="Currency")
    min_delivery_days = fields.Integer(string="Min Delivery Days")
    max_delivery_days = fields.Integer(string="Max Delivery Days")

    # Metadata
    printful_config_id = fields.Many2one(
        comodel_name='printful.printful',
        string="Printful Configuration",
        ondelete='cascade',
    )
    expires_at = fields.Datetime(
        string="Expires At",
        help="Cache expiration time",
    )

    @api.model
    def cleanup_expired(self):
        """Remove expired cache entries."""
        expired = self.search([
            ('expires_at', '<', fields.Datetime.now())
        ])
        if expired:
            _logger.info("Cleaning up %d expired shipping rate cache entries", len(expired))
            expired.unlink()

    @api.model
    def get_cached_rate(self, country_code, state_code, zip_code, variant_data, printful_method):
        """
        Get a cached rate if available and not expired.

        Args:
            country_code: Destination country code
            state_code: Destination state code
            zip_code: Destination ZIP code
            variant_data: JSON string of variant/quantity data
            printful_method: Printful shipping method ID

        Returns:
            printful.shipping.rate record or None
        """
        return self.search([
            ('country_code', '=', country_code),
            ('state_code', '=', state_code or False),
            ('zip_code', '=', zip_code or False),
            ('product_variant_ids', '=', variant_data),
            ('printful_method', '=', printful_method),
            ('expires_at', '>', fields.Datetime.now()),
        ], limit=1)


class PrintfulDefaultAddress(models.Model):
    """
    Default address configuration for shipping estimates.
    Used when calculating shipping during product sync (before customer is known).
    """
    _name = 'printful.default.address'
    _description = 'Printful Default Address for Shipping Estimates'

    name = fields.Char(
        string="Name",
        required=True,
        help="Label for this address (e.g., 'US Default', 'EU Default')",
    )
    active = fields.Boolean(string="Active", default=True)

    # Address components
    country_id = fields.Many2one(
        comodel_name='res.country',
        string="Country",
        required=True,
    )
    state_id = fields.Many2one(
        comodel_name='res.country.state',
        string="State/Province",
        domain="[('country_id', '=', country_id)]",
    )
    city = fields.Char(string="City", required=True)
    zip_code = fields.Char(string="ZIP/Postal Code", required=True)
    address1 = fields.Char(string="Street Address", required=True)

    # Configuration link
    printful_config_id = fields.Many2one(
        comodel_name='printful.printful',
        string="Printful Configuration",
        required=True,
        ondelete='cascade',
    )
    is_primary = fields.Boolean(
        string="Primary Address",
        default=False,
        help="Use this address as the primary default for shipping estimates",
    )

    @api.constrains('is_primary', 'printful_config_id')
    def _check_single_primary(self):
        """Ensure only one primary address per configuration."""
        for record in self:
            if record.is_primary:
                other_primaries = self.search([
                    ('printful_config_id', '=', record.printful_config_id.id),
                    ('is_primary', '=', True),
                    ('id', '!=', record.id),
                    ('active', '=', True),
                ])
                if other_primaries:
                    raise UserError(_(
                        'Only one address can be set as primary per Printful configuration. '
                        'Please uncheck the primary option on "%s" first.'
                    ) % other_primaries[0].name)

    def get_recipient_data(self):
        """
        Convert this address to Printful API recipient format.

        Returns:
            Dict suitable for Printful shipping/rates API
        """
        self.ensure_one()
        data = {
            "country_code": self.country_id.code,
            "city": self.city,
            "address1": self.address1,
            "zip": self.zip_code,
            "phone": "0000000000",  # Required by API but not used for rate calculation
        }

        if self.state_id:
            data["state_code"] = self.state_id.code

        return data
