"""Generate User_Manual.docx."""

from doc_helpers import (
    create_doc, add_table, add_tip, add_screenshot_placeholder,
    add_numbered_steps, add_bullet, add_bold_paragraph, save_doc,
)


def generate():
    doc = create_doc("User Manual")

    # ── 1. Introduction ──────────────────────────────────────────────
    doc.add_heading("1. Introduction", level=1)
    doc.add_paragraph(
        "The Boeing Data Hub is a product data management system that allows your team to "
        "search for Boeing aviation parts, review pricing and availability data, and publish "
        "those parts directly to your Shopify retail storefront. It also continuously "
        "synchronizes prices and inventory levels so your store always reflects the latest "
        "information from Boeing."
    )
    doc.add_paragraph(
        "This manual is written for operations staff, procurement specialists, and store "
        "administrators who use the Boeing Data Hub dashboard on a daily basis. No technical "
        "background is required."
    )

    # ── 2. Getting Started ───────────────────────────────────────────
    doc.add_heading("2. Getting Started", level=1)

    doc.add_heading("2.1 Accessing the System", level=2)
    doc.add_paragraph(
        "The Boeing Data Hub is a web application. Open it in a modern browser such as "
        "Google Chrome, Mozilla Firefox, or Microsoft Edge."
    )
    add_numbered_steps(doc, [
        "Open your browser and navigate to your organization's Boeing Data Hub URL "
        "(e.g., https://boeing-data-hub.skynetparts.com).",
        "You will be redirected to the **Aviation Gateway** single sign-on (SSO) page.",
        "Enter your corporate credentials (username and password) and click **Sign In**.",
        "After successful authentication you are redirected back to the dashboard.",
    ])
    add_tip(doc, "Bookmark the dashboard URL for quick access.")
    add_screenshot_placeholder(doc, "Aviation Gateway Login Page")

    doc.add_heading("2.2 Browser Requirements", level=2)
    add_bullet(doc, "Google Chrome 90+ (recommended)")
    add_bullet(doc, "Mozilla Firefox 90+")
    add_bullet(doc, "Microsoft Edge 90+")
    add_bullet(doc, "JavaScript must be enabled")

    # ── 3. Dashboard Overview ────────────────────────────────────────
    doc.add_heading("3. Dashboard Overview", level=1)
    doc.add_paragraph(
        "After logging in you see the main dashboard. It is organized into three tabs along "
        "the top of the screen."
    )
    add_screenshot_placeholder(doc, "Main Dashboard — Tab Navigation")

    doc.add_heading("3.A  Fetch & Process Tab", level=2)
    doc.add_paragraph(
        "This is where you search for Boeing parts, view extraction results, and publish "
        "products to Shopify. It contains:"
    )
    add_bullet(doc, "A search input area for entering part numbers (one per line or comma-separated).")
    add_bullet(doc, "A batch list panel showing all current and past search/publish operations with real-time status indicators.")
    add_bullet(doc, "A product table that displays extracted parts with their pricing, inventory, and status.")
    add_bullet(doc, "Action buttons for publishing individual or bulk products to Shopify.")

    doc.add_heading("3.B  Published Products Tab", level=2)
    doc.add_paragraph(
        "Displays all products that have been successfully published to your Shopify store. "
        "Includes search, pagination (50 items per page), and direct links to each product "
        "in the Shopify admin panel."
    )

    doc.add_heading("3.C  Auto-Sync Tab", level=2)
    doc.add_paragraph(
        "A monitoring dashboard for the automatic price and inventory synchronization system. "
        "Shows status cards, an hourly distribution chart, sync history, and a list of "
        "products that have failed synchronization."
    )

    # ── 4. Core Features ─────────────────────────────────────────────
    doc.add_heading("4. Core Features", level=1)

    # 4.1 Bulk Search
    doc.add_heading("4.1 Searching for Boeing Parts", level=2)
    doc.add_paragraph(
        "Use the Fetch & Process tab to search Boeing's Part Number Availability (PNA) system."
    )
    add_numbered_steps(doc, [
        "Click the **Fetch & Process** tab.",
        "In the search text area, enter one or more part numbers. You can separate them by "
        "pressing Enter (one per line) or by using commas.",
        "Click the **Search** button.",
        "A new batch appears in the batch list with status **Processing**. The system fetches "
        "data from Boeing, normalizes it, and stages it for review.",
        "When the batch status changes to **Completed**, the extracted products appear in "
        "the product table below.",
    ])
    doc.add_paragraph(
        "You should now see a table of parts with columns for Part Number, Title, Vendor, "
        "Price, Inventory Quantity, and Status."
    )
    add_tip(doc, "You can search up to 50,000 part numbers in a single bulk search.")
    add_screenshot_placeholder(doc, "Bulk Search — Entering Part Numbers")
    add_screenshot_placeholder(doc, "Batch Processing — Progress Indicators")

    # 4.2 Product Table
    doc.add_heading("4.2 Reviewing Extracted Products", level=2)
    add_numbered_steps(doc, [
        "After a search completes, click on the batch in the batch list to load its products.",
        "Each row shows the part number, title, vendor, price, inventory, and current status.",
        "Click the **expand arrow** on any row to see full product details including Boeing "
        "description, hazmat codes, FAA approval, dimensions, and location availability.",
        "Click **View Raw Data** to see the original JSON response from the Boeing API.",
    ])
    doc.add_paragraph(
        "Status indicators are color-coded:"
    )
    add_table(doc,
        ["Status", "Color", "Meaning"],
        [
            ["Extracted", "Blue", "Data fetched from Boeing, awaiting normalization"],
            ["Normalized", "Amber", "Data processed and ready to publish"],
            ["Published", "Green", "Product live on your Shopify store"],
            ["Failed", "Red", "An error occurred during processing"],
            ["Blocked", "Orange", "Product was skipped (e.g., duplicate or missing data)"],
        ],
    )
    add_screenshot_placeholder(doc, "Product Table with Status Indicators")

    # 4.3 Publishing
    doc.add_heading("4.3 Publishing Products to Shopify", level=2)

    doc.add_heading("Single Product Publish", level=3)
    add_numbered_steps(doc, [
        "In the product table, find the product you want to publish.",
        "Click the **Publish** button on that row.",
        "A confirmation toast appears. The product status changes to **Published** "
        "and a Shopify product ID is assigned.",
    ])

    doc.add_heading("Bulk Publish", level=3)
    add_numbered_steps(doc, [
        "Select multiple products using the checkboxes in the product table, "
        "or use the **Select All** option.",
        "Click the **Bulk Publish** button in the toolbar.",
        "A new publish batch is created. Monitor progress in the batch list panel.",
        "When complete, all selected products will have status **Published**.",
    ])
    add_tip(doc, "Products that are already published will be skipped automatically.")
    add_screenshot_placeholder(doc, "Bulk Publish — Selecting Products")

    # 4.4 Published Products
    doc.add_heading("4.4 Viewing Published Products", level=2)
    add_numbered_steps(doc, [
        "Click the **Published Products** tab.",
        "Browse the list of all products currently live on your Shopify store.",
        "Use the **search bar** at the top to filter by part number or title.",
        "Click on any product to expand its details.",
        "Click the **Shopify** link to open the product directly in your Shopify admin panel.",
    ])
    doc.add_paragraph(
        "The list is paginated at 50 products per page. Use the pagination controls at the "
        "bottom to navigate between pages."
    )
    add_screenshot_placeholder(doc, "Published Products Panel")

    # 4.5 Auto-Sync
    doc.add_heading("4.5 Auto-Sync Monitoring", level=2)
    doc.add_paragraph(
        "The Auto-Sync system automatically keeps your Shopify store's prices and inventory "
        "in sync with Boeing's latest data. You can monitor its operation from the Auto-Sync tab."
    )
    add_numbered_steps(doc, [
        "Click the **Auto-Sync** tab.",
        "At the top, review the **status cards** showing: total synced products, active syncs, "
        "pending syncs, and failed syncs.",
        "Below the cards, the **Hourly Distribution Chart** shows how sync tasks are spread "
        "across each hour of the day.",
        "The **Sync History** table lists recent sync operations with timestamps, status, "
        "and any error messages.",
        "The **Failed Products** section shows products that have failed synchronization, "
        "along with the number of consecutive failures and the last error message.",
    ])

    doc.add_heading("Reactivating a Failed Product", level=3)
    add_numbered_steps(doc, [
        "In the Failed Products list, find the product you want to retry.",
        "Click the **Reactivate** button next to that product.",
        "The product is re-added to the sync schedule and will be retried in the next cycle.",
    ])
    add_tip(doc, "Products are automatically deactivated after 5 consecutive sync failures to prevent repeated errors.")

    doc.add_heading("Triggering an Immediate Sync", level=3)
    add_numbered_steps(doc, [
        "Navigate to the Auto-Sync tab.",
        "Find the product you want to sync immediately.",
        "Click the **Trigger Sync** button.",
        "The system immediately fetches the latest data from Boeing and updates Shopify.",
    ])
    add_screenshot_placeholder(doc, "Auto-Sync Dashboard — Status Cards and Chart")

    # 4.6 Editing
    doc.add_heading("4.6 Editing Product Details", level=2)
    add_numbered_steps(doc, [
        "In the product table (Fetch & Process tab), click the **Edit** button on the product row.",
        "The **Edit Product** modal opens with editable fields for title, description, price, "
        "and other attributes.",
        "Make your changes and click **Save**.",
        "The product details are updated. If the product is already published, the changes "
        "are pushed to Shopify.",
    ])
    add_screenshot_placeholder(doc, "Edit Product Modal")

    # 4.7 Cancelling a Batch
    doc.add_heading("4.7 Cancelling a Batch Operation", level=2)
    add_numbered_steps(doc, [
        "In the batch list panel, find the batch you want to cancel.",
        "Click the **Cancel** button (or the X icon) next to the batch.",
        "The batch status changes to **Cancelled** and any remaining items are skipped.",
    ])
    doc.add_paragraph(
        "Note: Items that were already processed before cancellation will keep their "
        "current status. Only pending items are skipped."
    )

    # ── 5. Settings ──────────────────────────────────────────────────
    doc.add_heading("5. Settings", level=1)
    doc.add_paragraph(
        "The Boeing Data Hub does not currently have a user-facing settings page. System "
        "configuration (such as sync frequency, rate limits, and API connections) is managed "
        "by your system administrator through environment variables on the server."
    )
    doc.add_paragraph(
        "If you need to change a setting, contact your system administrator or refer to the "
        "Configuration Reference document."
    )

    # ── 6. Logout ────────────────────────────────────────────────────
    doc.add_heading("6. Logging Out", level=1)
    add_numbered_steps(doc, [
        "Click the **Logout** button in the top-right corner of the header bar.",
        "You are signed out of both the Boeing Data Hub and the Aviation Gateway SSO session.",
        "You are redirected to the login page.",
    ])
    doc.add_paragraph(
        "After logging out, you will need to re-authenticate through Aviation Gateway to "
        "access the dashboard again."
    )
    add_tip(doc, "Always log out when using a shared or public computer.")

    # ── 7. FAQ ───────────────────────────────────────────────────────
    doc.add_heading("7. Frequently Asked Questions (FAQ)", level=1)

    faq = [
        (
            "Q: How many part numbers can I search at once?",
            "A: You can search up to 50,000 part numbers in a single bulk search. The system "
            "automatically splits them into batches of 10 for processing."
        ),
        (
            "Q: Why is my batch stuck on 'Processing'?",
            "A: Boeing's API has a rate limit of 2 requests per minute. Large batches take time "
            "to process. If a batch remains stuck for more than an hour, contact your system "
            "administrator."
        ),
        (
            "Q: A product shows 'Failed' status. What should I do?",
            "A: Expand the product row or check the batch details to see the error message. "
            "Common causes include invalid part numbers or temporary Boeing API outages. "
            "You can retry by running a new search with the failed part number."
        ),
        (
            "Q: How often does Auto-Sync update prices and inventory?",
            "A: In production mode, the system syncs each product once every 24 hours. Products "
            "are distributed across hourly time slots so the system processes a manageable number "
            "each hour. Failed products are retried every 4 hours."
        ),
        (
            "Q: Can I publish the same product twice?",
            "A: No. The system checks for duplicate SKUs before publishing. If a product with the "
            "same SKU already exists in Shopify, the publish action will skip it and notify you."
        ),
        (
            "Q: What does the markup factor mean?",
            "A: The system applies a 10% markup (factor of 1.1x) to Boeing's list price when "
            "publishing to Shopify. This is configured by your administrator."
        ),
        (
            "Q: I can't log in. What should I do?",
            "A: Ensure you are using the correct Aviation Gateway credentials. If your session "
            "expired, refresh the page and log in again. If the problem persists, contact your "
            "IT support team."
        ),
    ]

    for q, a in faq:
        add_bold_paragraph(doc, q)
        doc.add_paragraph(a)

    # ── 8. Support Contact ───────────────────────────────────────────
    doc.add_heading("8. Support Contact", level=1)
    doc.add_paragraph(
        "If you encounter issues or have questions that are not covered in this manual, "
        "please reach out to your support team:"
    )
    add_table(doc,
        ["Contact", "Details"],
        [
            ["System Administrator", "[TODO: Add admin contact]"],
            ["IT Support Email", "[TODO: Add support email]"],
            ["Slack Channel", "[TODO: Add Slack channel name]"],
            ["Issue Tracker", "[TODO: Add issue tracker URL]"],
        ],
    )

    return save_doc(doc, "User_Manual.docx")
