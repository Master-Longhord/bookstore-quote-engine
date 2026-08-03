import base64
import os
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
        .header-container { display: flex; align-items: center; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }
        .logo { max-height: 80px; margin-right: 20px; }
        h1 { margin: 0; }
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
    <div class="header-container">
        {% if logo_base64 %}
        <img class="logo" src="data:image/png;base64,{{ logo_base64 }}" alt="Book Depot Logo">
        {% endif %}
        <h1>Reciept From ** {{ store_name }}</h1>
    </div>
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
        self._logo_base64 = self._load_logo()

    def _load_logo(self) -> str:
        """Loads the logo from the root directory and encodes it for embedding."""
        # Adjust the path relative to where you run main.py (the root folder)
        logo_path = os.path.join(os.getcwd(), "Book Depot logo 3.png")
        try:
            with open(logo_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode("utf-8")
        except FileNotFoundError:
            print(f"Warning: Logo not found at {logo_path}")
            return ""

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
            logo_base64=self._logo_base64,
            store_name=self._store_name,  
            customer_name=inquiry.sender_name or inquiry.sender,
            lines=lines_data,
            total=jmd(quote.total)
        )
        
        # 3. Convert to PDF bytes in memory
        html_doc = HTML(string=html_content)
        return html_doc.write_pdf()