# Printful Connect for Odoo 18

A comprehensive Odoo 18 integration module for [Printful](https://www.printful.com/) print-on-demand services. Synchronize products, fulfill orders, and track shipments seamlessly between Odoo and Printful.

**Version:** 18.0.2.0.0
**License:** LGPL-3
**Author:** Jon Mitchell

## Features

### Product Management
- **Visual Sync Queue** - Kanban-style interface for managing product synchronization
- **Batch Processing** - Handles large catalogs without timeouts (5 products per batch)
- **Variant Tracking** - Detailed progress for size/color combinations
- **Size Guide Generation** - Automatic HTML size guides from Printful data
- **Auto-Category Assignment** - Creates product categories based on Printful catalog
- **SEO Auto-Population** - Configurable templates for meta titles, descriptions, and keywords
- **Image Handling** - Downloads and stores product images and thumbnails

### Order Management
- **Order Push** - Send confirmed Odoo orders to Printful for fulfillment
- **Auto-Fulfillment** - Optionally push orders automatically on confirmation
- **Order Import** - Import orders created through Printful storefronts
- **Cost Tracking** - Detailed breakdown of costs (subtotal, shipping, fees, tax)
- **Fulfillment Status** - Track orders through pending → in_production → shipped → delivered

### Real-Time Webhooks
- **Instant Notifications** - Receive order status, shipment, and stock updates in real-time
- **Event Logging** - Full audit trail of all webhook events
- **HMAC Validation** - Secure webhook verification using shared secrets
- **Supported Events:**
  - Order: created, updated, failed, canceled
  - Shipping: shipped, returned
  - Product: synced, updated, deleted
  - Stock updates

### Shipping Management
- **Configurable Methods** - Map Printful shipping methods to Odoo carriers
- **Price Markups** - Apply fixed surcharges or percentage markups
- **Rate Caching** - Cache shipping calculations to reduce API calls
- **Real-Time Rates** - Calculate shipping with customer address at checkout

### API Integration
- **API v1 & v2 Support** - Compatible with both legacy and modern Printful APIs
- **Rate Limiting** - Built-in leaky bucket algorithm (120 requests/min)
- **Automatic Retries** - Handles rate limit responses with exponential backoff

## Requirements

### Odoo Modules
- `base`, `web`, `stock`, `product`, `sale`, `website`, `website_sale`, `delivery`

### Python Dependencies
```
requests
tabulate
markupsafe
```

### External Services
- Printful account with API access
- API token from Printful Dashboard

## Installation

1. Clone or copy the module to your Odoo addons directory:
   ```bash
   git clone https://github.com/your-repo/printful_connect.git /path/to/odoo/addons/
   ```

2. Install Python dependencies:
   ```bash
   pip install requests tabulate markupsafe
   ```

3. Update the Odoo apps list:
   - Navigate to **Apps** → **Update Apps List**

4. Install the module:
   - Search for "Printful Connect"
   - Click **Install**

## Configuration

### 1. API Setup

1. Navigate to **Printful** → **Configuration** → **Settings**
2. Enter your Printful API token
3. Select API version (v2 recommended)
4. Configure currency for pricing

### 2. Product Attributes

Set up the product attributes that will be used for variants:
- **Size Attribute** - Maps to Printful size options
- **Color Attribute** - Maps to Printful color options

### 3. Shipping Methods

Configure shipping methods under **Printful** → **Configuration** → **Shipping Methods**:

| Field | Description |
|-------|-------------|
| Name | Display name for the method |
| Printful Method | STANDARD, EXPRESS, OVERNIGHT, etc. |
| Fixed Surcharge | Flat fee to add (e.g., $2.50) |
| Percentage Markup | Percentage to add (e.g., 10%) |
| Delivery Carrier | Optional link to Odoo carrier |

### 4. Default Address

Set a default shipping address for accurate rate estimates during product sync:
- Navigate to **Printful** → **Configuration** → **Default Addresses**
- Create an address and mark it as **Primary**

### 5. Webhooks

1. Navigate to **Printful** → **Configuration** → **Webhooks**
2. Copy the displayed webhook URL
3. In Printful Dashboard, add the webhook URL
4. Copy the webhook secret back to Odoo
5. Enable the event types you want to receive

### 6. SEO Settings (Optional)

Enable automatic SEO field population:
- Toggle **Auto-populate SEO Fields**
- Customize templates using variables:
  - `{product_name}` - Product title
  - `{brand}` - Printful brand
  - `{type}` - Product type
  - `{description_short}` - First 150 characters

### 7. Packing Slip Branding (Optional)

Customize packing slips with your branding:
- Store name and logo URL
- Contact email and phone
- Custom message
- Default gift message

## Usage

### Syncing Products

1. Navigate to **Printful** → **Sync Queue**
2. Click **Create** to start a new sync session
3. Click **Populate Queue** to fetch products from Printful
4. Click **Start Sync** to begin processing
5. The system processes 5 products every 2 minutes via cron
6. Monitor progress in the Kanban view
7. Retry failed items individually or in batch

### Fulfilling Orders

**Manual Fulfillment:**
1. Open a confirmed sale order
2. Click **Push to Printful**
3. Order status updates to "In Production"

**Automatic Fulfillment:**
1. Enable **Auto-fulfill Orders** in settings
2. Orders are automatically pushed when confirmed

### Tracking Shipments

When Printful ships an order:
- Tracking information appears on the order
- Carrier, tracking number, and URL are captured
- Estimated delivery date is displayed
- Order chatter shows shipment notifications

### Monitoring Webhooks

View webhook activity under **Printful** → **Webhook Events**:
- Filter by event type or status
- View full payload details
- Retry failed events manually
- Events auto-process every 5 minutes

## Project Structure

```
printful_connect/
├── models/
│   ├── printful.py              # Core API and product sync
│   ├── printful_sync_queue.py   # Batch sync queue
│   ├── printful_webhook.py      # Webhook processing
│   ├── printful_shipping.py     # Shipping methods and rates
│   ├── printful_product.py      # Product variant extensions
│   ├── product.py               # Product template extensions
│   └── sale.py                  # Sale order extensions
├── views/
│   ├── printful_views.xml       # Configuration forms
│   ├── printful_sync_queue_views.xml
│   ├── printful_webhook_views.xml
│   ├── product_views.xml
│   ├── sale_views.xml
│   └── website_views.xml
├── controllers/
│   └── main.py                  # HTTP endpoints
├── security/
│   ├── printful_security.xml    # Access groups
│   └── ir.model.access.csv      # Model permissions
├── data/
│   └── fetch_product_cron.xml   # Scheduled jobs
├── static/
│   └── src/css/
└── tests/
```

## Security

### Access Control

Three permission levels:
- **User** - View sync queues, orders, and products
- **Manager** - Manage sync, push orders, view configuration
- **Administrator** - Modify API tokens and webhook secrets

### Data Protection

- API tokens stored as password fields (masked in UI)
- Webhook secrets required for signature validation
- HMAC-SHA256 signature verification on all webhooks
- HTML content sanitized to prevent XSS

## Scheduled Jobs

| Job | Interval | Description |
|-----|----------|-------------|
| Process Sync Queue | 2 min | Process next batch of products |
| Process Webhook Events | 5 min | Handle pending webhook events |
| Cleanup Shipping Cache | 1 hour | Remove expired rate cache |

## Troubleshooting

### Products Not Syncing

1. Check API token is valid in Printful settings
2. Verify size/color attributes are configured
3. Check sync queue for error messages
4. Review Odoo logs for API errors

### Webhooks Not Received

1. Verify webhook URL is accessible from internet
2. Check webhook secret matches Printful dashboard
3. Ensure event types are enabled
4. Review webhook event log for failures

### Shipping Rates Not Displaying

1. Configure at least one shipping method
2. Set a primary default address
3. Check shipping cache TTL setting
4. Verify products have Printful variant IDs

### Order Push Failing

1. Ensure products have `printful_variant_ref` set
2. Check order has valid shipping address
3. Verify API token has order permissions
4. Review order chatter for error messages

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Submit a pull request

## Support

- **Issues:** Report bugs via GitHub Issues
- **Documentation:** [Printful API Docs](https://developers.printful.com/)

## License

This module is licensed under LGPL-3. See [LICENSE](LICENSE) for details.
