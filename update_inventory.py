import pandas as pd

def build_inventory():
    print("Loading Excel files...")
    # Read the files, skipping the first 4 rows of metadata
    df_books = pd.read_excel("books_export_20260718_001612.xlsx", header=4)
    df_stat = pd.read_excel("stationery_export_20260718_002226.xlsx", header=4)

    # Format Books
    df_books_clean = df_books[['ISBN/SKU', 'Title', 'Author', 'Price ($)', 'Stock Qty']].copy()
    df_books_clean.columns = ['sku', 'title', 'author_or_publisher', 'price', 'stock']

    # Format Stationery (Mapping 'Category' to the author/publisher field)
    df_stat_clean = df_stat[['SKU', 'Name', 'Category', 'Price ($)', 'Stock Qty']].copy()
    df_stat_clean.columns = ['sku', 'title', 'author_or_publisher', 'price', 'stock']

    # Combine both datasets
    df_combined = pd.concat([df_books_clean, df_stat_clean], ignore_index=True)

    # Clean up empty values (NaNs) so the matcher doesn't break
    df_combined['sku'] = df_combined['sku'].fillna('UNKNOWN-SKU')
    df_combined['title'] = df_combined['title'].fillna('Unknown Title')
    df_combined['author_or_publisher'] = df_combined['author_or_publisher'].fillna('')
    df_combined['price'] = df_combined['price'].fillna(0.0)
    df_combined['stock'] = df_combined['stock'].fillna(0).astype(int)

    # Export to the final CSV expected by your application
    output_path = "data/inventory.csv" # Update to "data/inventory.csv" if your app looks for it in the data folder
    df_combined.to_csv(output_path, index=False)
    
    print(f"Success! {len(df_combined)} items written to {output_path}")

if __name__ == "__main__":
    build_inventory()