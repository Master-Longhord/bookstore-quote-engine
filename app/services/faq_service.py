"""FAQ Service for handling common customer inquiries."""

FAQS = {
    "1": {
        "question": "What are your opening hours?",
        "answer": "We are open From Monday to Saturday 7:30am to 5pm. We are open on Sundays during back-to-school periods (August to September). You can also call our office line to confirm."
    },
    "2": {
        "question": "What type of books do you sell / have available?",
        "answer": "We provide a wide range of books, from Kindergarten to High School / Secondary Level."
    },
    "3": {
        "question": "Can I preorder a book that is currently out of stock?",
        "answer": "No, but you can call our direct line to confirm the availability."
    },
    "4": {
        "question": "Do you offer payment plans?",
        "answer": "Yes, but payment plans are only available in stores where customers can make payments while the books are reserved. They will be handed over once the balance is paid."
    },
    "5": {
        "question": "Do you offer book wrapping on all books?",
        "answer": "Free book wrapping is provided for Textbooks Only that are purchased from Book Depot."
    },
    "6": {
        "question": "Do you accept book vouchers?",
        "answer": "Yes, we do accept 'Book Depot' vouchers only."
    },
    "7": {
        "question": "Do you sell educational toys?",
        "answer": "Yes, we do sell toys with most being educational."
    },
    "8": {
        "question": "Where exactly is Book Depot located?",
        "answer": "Book Depot is located at 38 Langston Road, Kingston 3, Vineyard Town. Our location is accurate on Google Maps."
    },
    "9": {
        "question": "What are your payment methods?",
        "answer": "Debit/Credit Card, Transfer, Cash (in store only), Cashapp, Zelle."
    },
    "10": {
        "question": "Can I collect my order in store without a receipt?",
        "answer": "A receipt / order number MUST be provided before an order can be released."
    },
    "11": {
        "question": "Do you offer discounts?",
        "answer": "Yes, we offer discounts during selected promotional periods."
    },
    "12": {
        "question": "How can I get a quote?",
        "answer": "You can scan your book list on our website, and a quote will be generated."
    },
    "13": {
        "question": "Do you deliver to areas outside of Kingston?",
        "answer": "For out-of-town orders, delivery is done through Knutsford Express."
    },
    "14": {
        "question": "Do you offer printing?",
        "answer": "Yes, we do offer printing services in store. You can contact our office line for more information."
    }
}

def get_faq_menu_text() -> str:
    """Generates the interactive menu text."""
    menu = "Welcome to Book Depot! 📚\nPlease reply with a number (1 to 14) to get an instant answer, or send your book list to get a quote:\n\n"
    for key, data in FAQS.items():
        menu += f"{key}. {data['question']}\n"
    return menu

def process_faq(message_text: str, sender_phone: str, whatsapp_client) -> bool:
    """
    Evaluates incoming text. 
    Returns True if handled as an FAQ. 
    Returns False if it should be sent to the book extractor.
    """
    cleaned_text = message_text.strip().lower()

    # 1. Check for menu triggers
    if cleaned_text in ["menu", "faq", "hi", "hello", "help"]:
        menu_text = get_faq_menu_text()
        whatsapp_client.send_text(sender_phone, menu_text)
        return True
        
    # 2. Check for explicit FAQ number selection
    if cleaned_text in FAQS:
        answer = FAQS[cleaned_text]["answer"]
        whatsapp_client.send_text(sender_phone, answer)
        return True
        
    return False
        