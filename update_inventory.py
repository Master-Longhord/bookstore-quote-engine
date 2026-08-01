import glob
import re
import pandas as pd

def get_latest_file(pattern: str) -> str:
    """Finds the most recent file matching a glob pattern."""
    files = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(f"No files found matching pattern: {pattern}")
    # Sort files by modification time to get the latest export
    return max(files, key=lambda f: f)

def clean_price(val) -> float:
    """Sanitizes price values, removing currency symbols, commas, and handling invalid values."""
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    # Remove everything except digits and decimal points
    cleaned = re.sub(r'[^\d.]', '', str(val))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0

def clean_sku(val) -> str:
    """Ensures SKUs are formatted cleanly without trailing floats like '.0'."""
    if pd.isna(val):
        return 'UNKNOWN-SKU'
    val_str = str(val).strip()
    if val_str.endswith('.0'):
        val_str = val_str[:-2]
    return val_str

def build_inventory():
    print("Locating inventory exports...")
    books_file = get_latest_file("books_export_*.xlsx")
    stat_file = get_latest_file("stationery_export_*.xlsx")
    
    print(f"Loading {books_file}...")
    df_books = pd.read_excel(books_file, header=4)
    
    print(f"Loading {stat_file}...")
    df_stat = pd.read_excel(stat_file, header=4)

    # Format Books
    df_books_clean = df_books[['ISBN/SKU', 'Title', 'Author', 'Price ($)', 'Stock Qty']].copy()
    df_books_clean.columns = ['sku', 'title', 'author_or_publisher', 'price', 'stock']

    # Format Stationery
    df_stat_clean = df_stat[['SKU', 'Name', 'Category', 'Price ($)', 'Stock Qty']].copy()
    df_stat_clean.columns = ['sku', 'title', 'author_or_publisher', 'price', 'stock']

    # Combine both datasets
    df_combined = pd.concat([df_books_clean, df_stat_clean], ignore_index=True)

    # Clean data types strictly
    df_combined['sku'] = df_combined['sku'].apply(clean_sku)
    df_combined['title'] = df_combined['title'].fillna('Unknown Title').astype(str).str.strip()
    df_combined['author_or_publisher'] = df_combined['author_or_publisher'].fillna('').astype(str).str.strip()
    df_combined['price'] = df_combined['price'].apply(clean_price)
    df_combined['stock'] = df_combined['stock'].fillna(0).astype(int)

    # Export to the final CSV expected by your application
    output_path = "data/inventory.csv"
    df_combined.to_csv(output_path, index=False)
    
    print(f"Success! {len(df_combined)} total items (books + stationery) written to {output_path}")

if __name__ == "__main__":
    build_inventory()
    