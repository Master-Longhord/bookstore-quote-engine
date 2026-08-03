import tempfile
from weasyprint import HTML
from jinja2 import Template

from app.domain.models import Inquiry, Quote
from app.services.quote_service import jmd

_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Quote</title>
    <style>
        @page { size: A4; margin: 20mm; }
        body { font-family: system-ui, sans-serif; color: #333; }
        h1 { border-bottom: 2px solid #333; padding-bottom: 5px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 10px; border-bottom: 1px solid #ddd; text-align: left; }
        th { background-color: #f9f9f9; }
        .total { font-weight: bold; font-size: 1.2em; text-align: right; margin-top: 20px; }
        
        /* Added footer styling */
        .footer {
            margin-top: 40px;
            text-align: center;
            font-size: 14px;
            font-weight: bold;
            color: #333333;
        }
    </style>
</head>
<body>
    <h1>Quotation from {{ store_name }}</h1>
    <p><strong>Customer:</strong> {{ customer_name }}</p>
    
    <table>
        <thead>
            <tr>
                <th>Item</th>
                <th>Qty</th>
                <th>Unit Price</th>
                <th>Total</th>
            </tr>
        </thead>
        <tbody>
            {% for line in lines %}
            <tr>
                <td>{{ line.title }}</td>
                <td>{{ line.qty }}</td>
                <td>{{ line.unit }}</td>
                <td>{{ line.line_total }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    
    <!-- Moved total above the footer -->
    <div class="total">Grand Total: {{ total }}</div>
    
    <div class="footer">
        <p>Thank you for shopping with us!</p>
    </div>
</body>
</html>
"""

class PdfGenerator:
    def __init__(self, store_name: str = "BookDepot"):
        self._store_name = store_name

    def generate_quote_pdf(self, inquiry: Inquiry, quote: Quote) -> bytes:
        """Renders the quote as HTML, converts to PDF, and returns the raw bytes."""
        
        # 1. Prepare data for the template
        lines_data = []
        for line in quote.lines:
            if line.matched and line.matched.in_stock:
                lines_data.append({
                    "title": line.matched.title,
                    "qty": line.quantity,
                    "unit": jmd(line.matched.price),
                    "line_total": jmd(line.line_total)
                })
        
        # 2. Render HTML
        template = Template(_HTML_TEMPLATE)
        html_content = template.render(
            store_name=self._store_name,  # Injecting the dynamic store name
            customer_name=inquiry.sender_name or inquiry.sender,
            lines=lines_data,
            total=jmd(quote.total)
        )
        
        # 3. Convert to PDF bytes in memory
        html_doc = HTML(string=html_content)
        return html_doc.write_pdf()